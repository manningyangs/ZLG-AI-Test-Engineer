"""P2-D 门面与编排器增量（evidence 挂载、state_listener）测试。"""

from __future__ import annotations

import pytest

from zlg_ai_test_engine.application.pipeline import build_mock_orchestrator, load_example_irs
from zlg_ai_test_engine.domain.execution.orchestrator import RunState
from zlg_ai_test_engine.infrastructure.persistence import create_engine_and_session, init_db
from zlg_ai_test_engine.application.audit_service import AuditService
from zlg_ai_test_engine.plugins.mock_adapter import (
    MockAdapter,
    MockScenario,
    PeriodicFrameSpec,
)

CAN_ID = 0x2C6

CYCLE_IR = {
    "schema_version": "1.0",
    "case_id": "FACADE_CYCLE_001",
    "title": "门面周期用例",
    "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
    "steps": [
        {"id": "S1", "action": "listen_bus", "duration_ms": 300},
        {"id": "S2", "action": "assert_cycle", "message": "VehicleSpeed",
         "expected_period_ms": 20, "tolerance_ms": 2, "evidence_source": "REQ_X"},
    ],
    "execution_policy": {"mode": "offline_only", "risk_level": "L0",
                         "requires_approval": False, "max_duration_ms": 5000},
    "unconfirmed_items": [],
}


@pytest.fixture
def audit(tmp_path):
    engine, factory = create_engine_and_session(tmp_path / "facade.db")
    init_db(engine)
    yield AuditService(factory)
    engine.dispose()


class TestPipelineFacade:
    def test_example_irs_loadable(self):
        examples = load_example_irs()
        assert len(examples) >= 5
        assert all(ex["schema_version"] == "1.0" for ex in examples)
        assert all("case_id" in ex for ex in examples)

    def test_facade_run_completes_with_evidence(self, audit):
        adapter = MockAdapter(
            MockScenario(
                periodic_frames=(PeriodicFrameSpec(can_id=CAN_ID, data=b"\x00\x00",
                                                   period_ms=20),)
            )
        )
        orch = build_mock_orchestrator(
            adapter=adapter, audit=audit, message_ids={"VehicleSpeed": CAN_ID}
        )
        rec = orch.submit(CYCLE_IR)
        assert rec.state == RunState.QUEUED, rec.error
        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED, rec.error
        # evidence 挂载：REPORT-01 的数据源
        assert rec.evidence is not None
        assert len(rec.evidence.frames) > 5
        assert rec.evidence.combined_window() is not None
        assert rec.results[0].verdict.value == "Pass"

    def test_facade_send_blocked_by_default_safe_approval(self, audit):
        """默认管线 L0 无授权：受控发送在执行前被 VALID-01 阻断（安全默认）。"""
        orch = build_mock_orchestrator(audit=audit)
        ir = dict(CYCLE_IR)
        ir["case_id"] = "FACADE_TX_001"
        ir["steps"] = [
            {"id": "S1", "action": "send_periodic", "can_id": 1, "payload_hex": "00",
             "period_ms": 20, "count": 3}
        ]
        ir["execution_policy"] = {"mode": "offline_only", "risk_level": "L1",
                                  "requires_approval": True, "max_duration_ms": 5000}
        rec = orch.submit(ir)
        assert rec.state == RunState.FAILED
        assert "chk_security" in (rec.error or "")

    def test_state_listener_called_on_transitions(self, audit):
        seen: list[str] = []
        orch = build_mock_orchestrator(audit=audit, state_listener=lambda r: seen.append(r.state))
        rec = orch.submit(CYCLE_IR)
        assert "Validated" in seen and "Queued" in seen
        orch.start(rec.run_id)
        assert "PreparingDevice" in seen and "Blocked" in seen  # 无帧场景 → 证据不足落 Blocked
        # 监听器异常不得破坏状态机
        boom = build_mock_orchestrator(
            audit=audit, state_listener=lambda _r: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        rec2 = boom.submit(CYCLE_IR)
        assert rec2.state == RunState.QUEUED

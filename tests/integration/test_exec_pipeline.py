"""P2-C 端到端集成：真实 COMP-01 编译器 + VALID-01 校验器 + EXEC-01a 编排器 + MockAdapter。

对应任务书验收：
- 同一 IR 在 Mock 重复执行产生确定性判定；
- 未审批的发送停在审批门、不触达设备；
- 未知设备能力（unknown ≠ 支持）在执行前阻断；
- 断言证据不足 → Blocked，绝不 Pass。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zlg_ai_test_engine.application.audit_service import AuditService
from zlg_ai_test_engine.domain.execution.compiler import compile_to_mock_plan
from zlg_ai_test_engine.domain.execution.judge import Verdict
from zlg_ai_test_engine.domain.execution.orchestrator import ExecutionOrchestrator, RunState
from zlg_ai_test_engine.domain.validation import ValidationContext, validate_execution
from zlg_ai_test_engine.infrastructure.persistence import create_engine_and_session, init_db
from zlg_ai_test_engine.plugins.mock_adapter import (
    MockAdapter,
    MockScenario,
    PeriodicFrameSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "examples" / "test-ir"

VEHICLE_SPEED_ID = 0x2C6
DBC_SHA = "5f2c1f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2f"


@pytest.fixture
def audit(tmp_path):
    engine, factory = create_engine_and_session(tmp_path / "pipe_audit.db")
    init_db(engine)
    yield AuditService(factory)
    engine.dispose()


def make_adapter() -> MockAdapter:
    return MockAdapter(
        MockScenario(
            periodic_frames=(
                PeriodicFrameSpec(
                    can_id=VEHICLE_SPEED_ID, data=b"\x00\x00", period_ms=20
                ),
            )
        )
    )


def build_ctx(ir: dict, *, adapter: MockAdapter, approval: dict | None = None) -> ValidationContext:
    return ValidationContext(
        ir=ir,
        asset_registry={
            "vehicle.dbc": {"sha256": DBC_SHA, "status": "active", "type": "dbc"},
            "dbc_vehicle": {"sha256": DBC_SHA, "status": "active", "type": "dbc"},
        },
        network_model=None,  # 引用检查降级为 WARNING（DATA-01 另行集成）
        capabilities=adapter.device_capabilities("mock-0"),
        approval=approval
        or {"user_level": "L0", "approved_actions": [], "environment": "bench"},
        resource_state={
            "device_busy": False,
            "busy_channels": [],
            "periodic_active": False,
            "queue_active": False,
        },
    )


def make_orchestrator(adapter: MockAdapter, audit: AuditService, approval: dict | None = None,
                      **kwargs) -> ExecutionOrchestrator:
    caps = adapter.device_capabilities("mock-0")

    def validator(ir: dict):
        return validate_execution(build_ctx(ir, adapter=adapter, approval=approval))

    def compiler(ir: dict):
        return compile_to_mock_plan(ir, capabilities=caps)

    params = {
        "adapter": adapter,
        "compiler": compiler,
        "audit": audit,
        "extra_validators": [validator],
        "message_ids": {"VehicleSpeed": VEHICLE_SPEED_ID},
    }
    params.update(kwargs)
    return ExecutionOrchestrator(**params)


def load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class TestCycleEndToEnd:
    def test_example_ir_passes_validation_and_executes(self, audit):
        """TC-02 路径：官方样例 IR → VALID → 真编译 → Mock 执行 → 周期断言 Pass。"""
        adapter = make_adapter()
        orch = make_orchestrator(adapter, audit)
        rec = orch.submit(load_example("valid_cycle_monitor.json"))
        assert rec.state == RunState.QUEUED, rec.error
        assert rec.plan is not None
        assert rec.plan["compiler_version"] == "1.0"
        assert rec.plan["cleanup"][0]["type"] == "stop_sends"  # 四件套

        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED, rec.error
        assert rec.results[0].action == "assert_cycle"
        assert rec.results[0].verdict is Verdict.PASS
        assert rec.results[0].computed["frames"] >= 400  # 10s @ 20ms
        assert rec.cleanup_errors == []

    def test_repeat_execution_deterministic(self, audit):
        """同一 IR 两次独立运行 → 判定与摘要完全一致（run_id 不同）。"""
        digests = []
        for _ in range(2):
            orch = make_orchestrator(make_adapter(), audit)
            rec = orch.submit(load_example("valid_cycle_monitor.json"))
            orch.start(rec.run_id)
            assert rec.state == RunState.COMPLETED
            digests.append(
                (rec.summary, [r.inputs_digest for r in rec.results],
                 rec.plan["plan_hash"])
            )
        assert digests[0] == digests[1]

    def test_wrong_period_fails_dut_but_run_completes(self, audit):
        ir = load_example("valid_cycle_monitor.json")
        ir["steps"][1]["expected_period_ms"] = 50  # 场景实际 20ms
        orch = make_orchestrator(make_adapter(), audit)
        rec = orch.submit(ir)
        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED
        assert rec.results[0].verdict is Verdict.FAIL

    def test_no_frames_blocked_run(self, audit):
        """无证据：断言 Blocked → 运行 Blocked（绝不 Pass）。"""
        adapter = MockAdapter()  # 无周期帧
        orch = make_orchestrator(adapter, audit)
        rec = orch.submit(load_example("valid_cycle_monitor.json"))
        orch.start(rec.run_id)
        assert rec.state == RunState.BLOCKED
        assert rec.results[0].verdict is Verdict.BLOCKED


class TestApprovalAndSend:
    def _send_ir(self) -> dict:
        return {
            "schema_version": "1.0",
            "case_id": "PIPE_TX_001",
            "title": "message_ref 型受控周期发送（集成）",
            "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
            "steps": [
                {
                    "id": "S1",
                    "action": "send_periodic",
                    "message_ref": {"message": "VehicleSpeed"},
                    "payload_hex": "aabb",
                    "period_ms": 20,
                    "count": 5,
                },
            ],
            "execution_policy": {
                "mode": "offline_only",
                "risk_level": "L1",
                "requires_approval": True,
                "max_duration_ms": 5000,
            },
            "unconfirmed_items": [],
        }

    def test_message_ref_resolved_and_approved_send_executes(self, audit):
        adapter = make_adapter()
        approval = {"user_level": "L1", "approved_actions": ["send_periodic"],
                    "environment": "bench"}
        orch = make_orchestrator(adapter, audit, approval=approval)
        rec = orch.submit(self._send_ir())
        assert rec.state == RunState.AWAITING_APPROVAL, rec.error
        # 审批前：设备上没有任何帧
        assert adapter.sent_frames == ()
        orch.approve(rec.run_id, approver="lead")
        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED, rec.error
        sent = adapter.sent_frames
        assert len(sent) == 5
        assert all(tf.frame.can_id == VEHICLE_SPEED_ID for tf in sent)
        # 周期间隔由虚拟时钟精确保证
        times = [tf.time_ms for tf in sent]
        assert all(b - a == 20 for a, b in zip(times, times[1:]))

    def test_unknown_capability_blocked_by_valid(self, audit):
        """样例声明 hardware_periodic_tx，而 Mock 该能力为 unknown → 执行前阻断。"""
        adapter = make_adapter()
        orch = make_orchestrator(adapter, audit)
        rec = orch.submit(load_example("valid_periodic_send.json"))
        assert rec.state == RunState.FAILED
        assert "unproven_capability" in (rec.error or "") or "chk_device_cap" in (rec.error or "")
        assert adapter.sent_frames == ()

    def test_unapproved_user_level_blocked_by_valid(self, audit):
        """L1 需审批但 VALID 上下文无授权 → Failed（执行前阻断，不到审批门）。"""
        adapter = make_adapter()
        orch = make_orchestrator(adapter, audit)  # 默认 approval：L0、无动作授权
        rec = orch.submit(self._send_ir())
        assert rec.state == RunState.FAILED
        assert "chk_security" in (rec.error or "")
        assert adapter.sent_frames == ()


class TestDbcWiredPipeline:
    """DATA-01 → EXEC 接线：DBC 解析出的 message/frame_id/周期属性直接驱动执行与断言。"""

    def test_dbc_cycle_time_drives_cycle_assertion(self, audit):
        from zlg_ai_test_engine.domain.network.queries import NetworkQueries
        from zlg_ai_test_engine.plugins.parser_dbc import DbcParser

        parsed = DbcParser().parse(REPO_ROOT / "examples" / "dbc" / "demo_vehicle.dbc")
        queries = NetworkQueries.from_parse_result(parsed)

        # message 名 → CAN ID 映射由 NetworkModel 生成（执行侧不再手写）
        message_ids = {m.message_name: m.frame_id for m in queries.model.messages}
        assert "WheelSpeeds" in message_ids

        wheel = queries.find_message_by_name("WheelSpeeds")
        assert wheel is not None
        cycle_ms = wheel.cycle_time_ms  # 来自 DBC GenMsgCycleTime 属性 = 20
        assert cycle_ms == 20

        # 场景按 DBC 周期注入周期帧 → 断言阈值同样取自 DBC
        adapter = MockAdapter(
            MockScenario(
                periodic_frames=(
                    PeriodicFrameSpec(
                        can_id=wheel.frame_id, data=b"\x00" * 8, period_ms=cycle_ms
                    ),
                )
            )
        )
        orch = make_orchestrator(adapter, audit, message_ids=message_ids)
        ir = {
            "schema_version": "1.0",
            "case_id": "PIPE_DBC_CYCLE_001",
            "title": "DBC 周期属性驱动的周期断言（接线集成）",
            "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
            "steps": [
                {"id": "S1", "action": "listen_bus", "duration_ms": 500},
                {"id": "S2", "action": "assert_cycle", "message": "WheelSpeeds",
                 "expected_period_ms": cycle_ms, "tolerance_ms": 1,
                 "evidence_source": "DBC:GenMsgCycleTime:WheelSpeeds"},
            ],
            "execution_policy": {"mode": "offline_only", "risk_level": "L0",
                                 "requires_approval": False, "max_duration_ms": 5000},
            "unconfirmed_items": [],
        }
        rec = orch.submit(ir)
        assert rec.state == RunState.QUEUED, rec.error
        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED, rec.error
        assert rec.results[0].verdict is Verdict.PASS
        assert rec.results[0].threshold["evidence_source"].startswith("DBC:")

    def test_dbc_decode_available_for_signal_wiring(self):
        """接线基础：NetworkQueries 可解码 DBC 报文（供后续信号断言注入 decoder）。"""
        from zlg_ai_test_engine.domain.network.queries import NetworkQueries
        from zlg_ai_test_engine.plugins.parser_dbc import DbcParser

        parsed = DbcParser().parse(REPO_ROOT / "examples" / "dbc" / "demo_vehicle.dbc")
        queries = NetworkQueries.from_parse_result(parsed)
        # WheelSpeeds: WheelFL = 7|16@0+ (motorola, 0.01,0)，大端 10000(0x2710) → 100.00 km/h
        values = queries.decode_message(0x200, bytes.fromhex("2710" + "00" * 6))
        assert values["WheelFL"] == pytest.approx(100.0, abs=0.01)

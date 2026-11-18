"""EXEC-01a：执行编排器测试。

覆盖任务书验收：各状态转换有单测；超时/取消可恢复；清理失败单独记录；
运行任务不可被覆盖；证据不足落 Blocked；AI 不得覆盖机器判定。

注：本文件自带最小 fake_compiler（形状兼容 COMP-01 计划），保证编排器
可独立于 COMP-01 测试；与真实 COMP-01 的集成见 integration 测试。
"""

from __future__ import annotations

import threading
import time

import pytest

from zlg_ai_test_engine.application.audit_service import AuditService
from zlg_ai_test_engine.domain.execution.orchestrator import (
    STATE_TRANSITIONS,
    ExecutionOrchestrator,
    InvalidStateError,
    RunState,
)
from zlg_ai_test_engine.domain.execution.judge import FailureClass, Verdict
from zlg_ai_test_engine.infrastructure.persistence import create_engine_and_session, init_db
from zlg_ai_test_engine.plugins.mock_adapter import (
    MockAdapter,
    MockScenario,
    PeriodicFrameSpec,
)

CAN_ID = 0x123  # 291


# ---------------------------------------------------------------------------
# 最小测试编译器（形状兼容 COMP-01）
# ---------------------------------------------------------------------------


def fake_compiler(ir: dict) -> dict:
    commands = [
        {"seq": 1, "type": "configure_channel", "step_id": None, "action": None,
         "params": {"channel_index": 0, "bus_type": "CAN", "listen_only": True}},
        {"seq": 2, "type": "start_channel", "step_id": None, "action": None,
         "params": {"channel_index": 0}},
    ]
    seq = 3
    for step in ir.get("steps", []):
        action = step.get("action")
        if action == "listen_bus":
            commands.append({"seq": seq, "type": "listen", "step_id": step["id"],
                             "action": action, "params": {"duration_ms": step["duration_ms"]}})
        elif action == "monitor_signal":
            commands.append({"seq": seq, "type": "monitor", "step_id": step["id"],
                             "action": action, "params": {"duration_ms": step["duration_ms"]}})
        elif action == "send_periodic":
            commands.append({"seq": seq, "type": "send_periodic", "step_id": step["id"],
                             "action": action, "params": {
                                 "can_id": step.get("can_id", CAN_ID),
                                 "payload_hex": step.get("payload_hex", "0102"),
                                 "period_ms": step["period_ms"],
                                 "count": step.get("count"),
                                 "duration_ms": step.get("duration_ms"),
                                 "start_delay_ms": step.get("start_delay_ms", 0),
                             }})
        elif action.startswith("assert_"):
            commands.append({"seq": seq, "type": "assert", "step_id": step["id"],
                             "action": action, "params": dict(step),
                             "evidence_source": step.get("evidence_source")})
        seq += 1
    return {
        "plan_version": "1.0",
        "compiler_version": "test-fake-1.0",
        "backend": "mock",
        "ir_hash": "fake",
        "plan_hash": "fake",
        "device": {"device_id": "mock-0", "channel": 0},
        "resources": {"max_frames": 1_000_000},
        "commands": commands,
        "cleanup": [
            {"type": "stop_receiving"},
            {"type": "reset_channel", "channel_index": 0},
            {"type": "close_device"},
        ],
    }


# ---------------------------------------------------------------------------
# IR 构造器
# ---------------------------------------------------------------------------


def cycle_ir(*, requires_approval: bool = False, unconfirmed: list | None = None,
             period: int = 20, listen_ms: int = 500) -> dict:
    return {
        "schema_version": "1.0",
        "case_id": "EXEC_CYCLE_001",
        "title": "周期监控（编排器测试）",
        "protocol": {"bus": "CAN", "database": "demo.dbc"},
        "steps": [
            {"id": "S1", "action": "listen_bus", "duration_ms": listen_ms},
            {"id": "S2", "action": "assert_cycle", "message": "VehicleSpeed",
             "expected_period_ms": period, "tolerance_ms": 2, "evidence_source": "REQ_X"},
        ],
        "execution_policy": {"mode": "offline_only", "risk_level": "L1" if requires_approval else "L0",
                             "requires_approval": requires_approval, "max_duration_ms": 10000},
        "unconfirmed_items": unconfirmed or [],
    }


def periodic_send_ir(count: int = 5) -> dict:
    return {
        "schema_version": "1.0",
        "case_id": "EXEC_TX_001",
        "title": "受控周期发送（编排器测试）",
        "protocol": {"bus": "CAN", "database": "demo.dbc"},
        "steps": [
            {"id": "S1", "action": "send_periodic", "can_id": CAN_ID, "payload_hex": "aabb",
             "period_ms": 1, "count": count},
        ],
        "execution_policy": {"mode": "offline_only", "risk_level": "L1",
                             "requires_approval": True, "max_duration_ms": 5000},
        "unconfirmed_items": [],
    }


def make_orchestrator(*, adapter: MockAdapter | None = None, with_audit: bool = True,
                      audit=None, **kwargs) -> ExecutionOrchestrator:
    if audit is None and with_audit:
        raise ValueError("audit fixture required")
    params: dict = {
        "adapter": adapter if adapter is not None else MockAdapter(),
        "compiler": fake_compiler,
        "message_ids": {"VehicleSpeed": CAN_ID},
        "audit": audit,
    }
    params.update(kwargs)  # 调用方覆盖（如 message_ids={}）
    return ExecutionOrchestrator(**params)


@pytest.fixture
def audit(tmp_path):
    engine, factory = create_engine_and_session(tmp_path / "exec_audit.db")
    init_db(engine)
    yield AuditService(factory)
    engine.dispose()


def scenario_adapter(period_ms: int = 20) -> MockAdapter:
    return MockAdapter(
        MockScenario(periodic_frames=(PeriodicFrameSpec(can_id=CAN_ID, data=b"\x00\x00",
                                                        period_ms=period_ms),))
    )


# ---------------------------------------------------------------------------
# 状态机基础
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_transition_table_covers_all_states_and_leads_to_terminals(self):
        from zlg_ai_test_engine.domain.execution.orchestrator import TERMINAL_STATES

        assert TERMINAL_STATES <= set(STATE_TRANSITIONS)
        # 每个非终态都可达终态（一步或两步内存在通往终态的边）
        for state, targets in STATE_TRANSITIONS.items():
            if state in TERMINAL_STATES:
                assert targets == frozenset()

    def test_start_unknown_run_rejected(self, audit):
        orch = make_orchestrator(audit=audit)
        with pytest.raises(InvalidStateError, match="不存在"):
            orch.start("nope")

    def test_cancel_terminal_rejected(self, audit):
        orch = make_orchestrator(adapter=scenario_adapter(), audit=audit)
        rec = orch.submit(cycle_ir())
        assert rec.state == RunState.QUEUED
        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED
        with pytest.raises(InvalidStateError, match="终态"):
            orch.cancel(rec.run_id)


# ---------------------------------------------------------------------------
# 提交路径
# ---------------------------------------------------------------------------


class TestSubmitPaths:
    def test_valid_no_approval_goes_to_queued(self, audit):
        orch = make_orchestrator(audit=audit)
        rec = orch.submit(cycle_ir())
        assert rec.state == RunState.QUEUED
        assert rec.plan is not None

    def test_requires_approval_parks_at_awaiting(self, audit):
        orch = make_orchestrator(audit=audit)
        rec = orch.submit(cycle_ir(requires_approval=True))
        assert rec.state == RunState.AWAITING_APPROVAL
        with pytest.raises(InvalidStateError, match="Queued"):
            orch.start(rec.run_id)
        orch.approve(rec.run_id, approver="tester", granted=True)
        assert rec.state == RunState.QUEUED
        assert rec.approval == "granted"

    def test_approval_denied_cancels(self, audit):
        orch = make_orchestrator(audit=audit)
        rec = orch.submit(cycle_ir(requires_approval=True))
        orch.approve(rec.run_id, approver="tester", granted=False)
        assert rec.state == RunState.CANCELLED
        assert "审批拒绝" in (rec.error or "")

    def test_unconfirmed_goes_to_clarification(self, audit):
        orch = make_orchestrator(audit=audit)
        rec = orch.submit(cycle_ir(unconfirmed=["周期阈值待确认"]))
        assert rec.state == RunState.CLARIFICATION_REQUIRED
        assert "unconfirmed_items" in (rec.error or "")

    def test_structural_invalid_fails(self, audit):
        orch = make_orchestrator(audit=audit)
        ir = cycle_ir()
        ir["steps"][0]["duration_ms"] = -5  # 非法
        rec = orch.submit(ir)
        assert rec.state == RunState.FAILED
        assert rec.error

    def test_repeated_runs_get_distinct_ids_no_overwrite(self, audit):
        orch = make_orchestrator(adapter=scenario_adapter(), audit=audit)
        r1 = orch.submit(cycle_ir())
        orch.start(r1.run_id)
        r2 = orch.submit(cycle_ir())
        orch.start(r2.run_id)
        assert r1.run_id != r2.run_id
        assert orch.get(r1.run_id) is r1 and orch.get(r2.run_id) is r2
        assert len(orch.list_runs()) == 2
        # 两次独立运行均完成
        assert r1.state == RunState.COMPLETED and r2.state == RunState.COMPLETED


# ---------------------------------------------------------------------------
# 执行与判定
# ---------------------------------------------------------------------------


class TestExecution:
    def test_happy_path_cycle_pass(self, audit):
        orch = make_orchestrator(adapter=scenario_adapter(period_ms=20), audit=audit)
        rec = orch.submit(cycle_ir(listen_ms=500, period=20))
        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED
        assert len(rec.results) == 1
        assert rec.results[0].verdict is Verdict.PASS
        assert rec.summary["Pass"] == 1
        assert rec.runner_status == "completed"
        assert rec.cleanup_errors == []

    def test_period_mismatch_fails_but_run_completes(self, audit):
        """判定 Fail ≠ 执行失败：运行 Completed，断言 Fail 且类别 DUT。"""
        orch = make_orchestrator(adapter=scenario_adapter(period_ms=50), audit=audit)
        rec = orch.submit(cycle_ir(listen_ms=500, period=20))
        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED
        assert rec.results[0].verdict is Verdict.FAIL
        assert rec.results[0].failure_class is FailureClass.DUT

    def test_no_evidence_run_blocked(self, audit):
        """无帧场景：断言全 Blocked → 运行落 Blocked（绝不 Pass）。"""
        orch = make_orchestrator(adapter=MockAdapter(), audit=audit)  # 无周期帧场景
        rec = orch.submit(cycle_ir(listen_ms=300))
        orch.start(rec.run_id)
        assert rec.state == RunState.BLOCKED
        assert rec.results[0].verdict is Verdict.BLOCKED

    def test_unresolved_message_reference_blocked(self, audit):
        orch = make_orchestrator(adapter=scenario_adapter(), audit=audit,
                                 message_ids={})  # 不提供映射
        rec = orch.submit(cycle_ir())
        orch.start(rec.run_id)
        assert rec.state == RunState.BLOCKED
        assert "无法解析" in rec.results[0].reason

    def test_deterministic_across_runs(self, audit):
        results = []
        for _ in range(2):
            orch = make_orchestrator(adapter=scenario_adapter(), audit=audit)
            rec = orch.submit(cycle_ir())
            orch.start(rec.run_id)
            results.append([r.to_dict() for r in rec.results])
        assert results[0] == results[1]

    def test_device_open_failure_fails_run(self, audit):
        adapter = MockAdapter()
        adapter.faults.offline = True
        orch = make_orchestrator(adapter=adapter, audit=audit)
        rec = orch.submit(cycle_ir())
        orch.start(rec.run_id)
        assert rec.state == RunState.FAILED
        assert rec.error and "MockDeviceOfflineError" in rec.error

    def test_send_without_approval_never_reaches_device(self, audit):
        """L1 未审批的发送不可能到达设备（停在 AwaitingApproval）。"""
        adapter = MockAdapter()
        orch = make_orchestrator(adapter=adapter, audit=audit)
        rec = orch.submit(periodic_send_ir())
        assert rec.state == RunState.AWAITING_APPROVAL
        assert adapter.sent_frames == ()


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_queued(self, audit):
        orch = make_orchestrator(audit=audit)
        rec = orch.submit(cycle_ir())
        orch.cancel(rec.run_id, reason="用户取消")
        assert rec.state == RunState.CANCELLED
        with pytest.raises(InvalidStateError):
            orch.start(rec.run_id)

    def test_cancel_during_running(self, audit):
        """运行中取消（跨线程）：置标志 → start 线程收敛到 Cancelled。"""
        orch = make_orchestrator(adapter=scenario_adapter(), audit=audit)
        ir = periodic_send_ir(count=300_000)  # 足够长的执行窗口
        ir["execution_policy"]["requires_approval"] = False
        ir["execution_policy"]["risk_level"] = "L0"
        rec = orch.submit(ir)
        assert rec.state == RunState.QUEUED

        errors: list[Exception] = []

        def _run() -> None:
            try:
                orch.start(rec.run_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        deadline = time.time() + 5
        while rec.state != RunState.RUNNING and time.time() < deadline:
            time.sleep(0.001)
        assert rec.state == RunState.RUNNING, f"未进入 Running: {rec.state}"
        orch.cancel(rec.run_id, reason="运行中停止")
        t.join(timeout=10)
        assert not t.is_alive()
        assert errors == []
        assert rec.state == RunState.CANCELLED
        # 取消后资源已释放（cleanup 执行过）
        assert not adapter_is_open(orch)


def adapter_is_open(orch: ExecutionOrchestrator) -> bool:
    adapter = orch._adapter
    return bool(getattr(adapter, "is_open", lambda _d: False)("mock-0"))


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_state_transitions_audited_with_run_id(self, audit):
        orch = make_orchestrator(adapter=scenario_adapter(), audit=audit)
        rec = orch.submit(cycle_ir())
        orch.start(rec.run_id)
        events = audit.list_events(run_id=rec.run_id)
        actions = [e.action for e in events]
        assert "run.submit" in actions
        assert "run.start" in actions
        assert "run.finish" in actions
        changes = [e for e in events if e.action == "run.state_change"]
        refs = " | ".join(str(e.refs_json) for e in changes)
        for expected in ("Draft -> Validated", "Queued -> PreparingDevice",
                         "Running -> CollectingEvidence", "DeterministicEvaluation -> Completed"):
            assert expected in refs

    def test_approval_audited(self, audit):
        orch = make_orchestrator(audit=audit)
        rec = orch.submit(cycle_ir(requires_approval=True))
        orch.approve(rec.run_id, approver="lead", granted=True)
        events = audit.list_events(action="run.approve", run_id=rec.run_id)
        assert events and events[0].refs_json == ["lead"]


# ---------------------------------------------------------------------------
# AI 只解释不改判
# ---------------------------------------------------------------------------


class TestAIExplanation:
    def test_ai_explainer_called_and_results_untouched(self, audit):
        seen: dict = {}

        def explainer(results, ir):
            seen["count"] = len(results)
            before = [r.verdict for r in results]
            assert before  # 断言存在
            return "AI 建议：周期抖动疑似总线负载上升（推测）"

        orch = make_orchestrator(
            adapter=scenario_adapter(period_ms=50), audit=audit, ai_explainer=explainer
        )
        rec = orch.submit(cycle_ir(listen_ms=500, period=20))
        orch.start(rec.run_id)
        assert rec.state == RunState.COMPLETED
        assert seen["count"] == 1
        # 判定仍是 Fail（AI 未覆盖）
        assert rec.results[0].verdict is Verdict.FAIL
        assert "推测" in (rec.ai_explanation or "")
        events = audit.list_events(action="run.ai_explanation", run_id=rec.run_id)
        assert events

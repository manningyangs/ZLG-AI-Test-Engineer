"""EXEC-01a：执行编排器（状态机 + 审计 + 证据判定接线）。

状态机（任务书 10.1）：
    Draft → ClarificationRequired / Validated → AwaitingApproval → Queued
          → PreparingDevice → Running → CollectingEvidence
          → DeterministicEvaluation → AIExplanation → Completed
    异常状态：Failed / Cancelled / Blocked

原则：
- 所有状态转换经过白名单校验并写追加式审计日志；
- 同一用例重复运行生成不同 run_id，历史运行不可被覆盖；
- AI 只解释（ai_explainer 可选注入），**不得改变判定结果**；
- 证据不足的运行落 Blocked，执行异常落 Failed，用户取消落 Cancelled。

编译器通过构造参数注入（依赖倒置）：默认由调用方传入 COMP-01 的
``compile_to_mock_plan``，保证编排器与编译器可独立测试。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from zlg_ai_test_engine.application.audit_service import AuditService
from zlg_ai_test_engine.domain.execution.judge import (
    AssertionResult,
    Verdict,
    judge_step,
)
from zlg_ai_test_engine.domain.execution.runner import PlanRunner, RunnerOutcome
from zlg_ai_test_engine.domain.plugins.contracts import DeviceSDKAdapter
from zlg_ai_test_engine.domain.test_ir import validate_executable

_ASSERT_ACTIONS = frozenset({"assert_cycle", "assert_range", "assert_enum", "assert_timeout"})


class RunState(str):
    """运行状态（str 子类便于序列化）。"""

    DRAFT = "Draft"
    CLARIFICATION_REQUIRED = "ClarificationRequired"
    VALIDATED = "Validated"
    AWAITING_APPROVAL = "AwaitingApproval"
    QUEUED = "Queued"
    PREPARING_DEVICE = "PreparingDevice"
    RUNNING = "Running"
    COLLECTING_EVIDENCE = "CollectingEvidence"
    DETERMINISTIC_EVALUATION = "DeterministicEvaluation"
    AI_EXPLANATION = "AIExplanation"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    BLOCKED = "Blocked"


#: 状态迁移白名单（未列出的一律拒绝）
STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    RunState.DRAFT: frozenset(
        {RunState.CLARIFICATION_REQUIRED, RunState.VALIDATED, RunState.FAILED}
    ),
    RunState.CLARIFICATION_REQUIRED: frozenset(),
    RunState.VALIDATED: frozenset(
        {RunState.AWAITING_APPROVAL, RunState.QUEUED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.AWAITING_APPROVAL: frozenset({RunState.QUEUED, RunState.CANCELLED, RunState.FAILED}),
    RunState.QUEUED: frozenset({RunState.PREPARING_DEVICE, RunState.CANCELLED, RunState.FAILED}),
    RunState.PREPARING_DEVICE: frozenset(
        {RunState.RUNNING, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.RUNNING: frozenset(
        {RunState.COLLECTING_EVIDENCE, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.COLLECTING_EVIDENCE: frozenset(
        {RunState.DETERMINISTIC_EVALUATION, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.DETERMINISTIC_EVALUATION: frozenset(
        {RunState.AI_EXPLANATION, RunState.COMPLETED, RunState.BLOCKED, RunState.FAILED}
    ),
    RunState.AI_EXPLANATION: frozenset({RunState.COMPLETED, RunState.BLOCKED, RunState.FAILED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
    RunState.BLOCKED: frozenset(),
}

TERMINAL_STATES = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.BLOCKED}
)


class InvalidStateError(Exception):
    """非法状态迁移或对不存在/已终态运行执行操作。"""


@dataclass
class RunRecord:
    """一次运行的完整记录（不覆盖、只追加字段直到终态）。"""

    run_id: str
    state: str
    ir: Mapping[str, Any]
    created_at: datetime
    plan: Mapping[str, Any] | None = None
    results: list[AssertionResult] = field(default_factory=list)
    error: str | None = None
    approval: str | None = None  # pending / granted / denied
    approver: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    adapter_id: str = ""
    device_id: str = ""
    cleanup_errors: list[str] = field(default_factory=list)
    runner_status: str | None = None
    ai_explanation: str | None = None

    @property
    def summary(self) -> dict[str, int]:
        counts = {"Pass": 0, "Fail": 0, "Blocked": 0, "NotEvaluated": 0}
        for r in self.results:
            counts[r.verdict.value] += 1
        return counts

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digest(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class ExecutionOrchestrator:
    """执行编排器：submit → approve → start →（cancel）→ 终态。"""

    def __init__(
        self,
        *,
        adapter: DeviceSDKAdapter,
        compiler: Callable[..., Mapping[str, Any]],
        device_id: str = "mock-0",
        audit: AuditService | None = None,
        extra_validators: Sequence[Callable[[Mapping[str, Any]], Any]] = (),
        message_ids: Mapping[str, int] | None = None,
        signal_ids: Mapping[str, int] | None = None,
        signal_decoder: Callable[[str, bytes], float | int] | None = None,
        ai_explainer: Callable[[Sequence[AssertionResult], Mapping[str, Any]], str] | None = None,
    ) -> None:
        self._adapter = adapter
        self._compiler = compiler
        self._device_id = device_id
        self._audit = audit
        self._extra_validators = tuple(extra_validators)
        self._message_ids = dict(message_ids or {})
        self._signal_ids = dict(signal_ids or {})
        self._signal_decoder = signal_decoder
        self._ai_explainer = ai_explainer
        self._runs: dict[str, RunRecord] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._runner: PlanRunner | None = None

    # -- 查询 -----------------------------------------------------------------

    def get(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError:
            raise InvalidStateError(f"run 不存在: {run_id}") from None

    def list_runs(self) -> list[RunRecord]:
        return list(self._runs.values())

    # -- 审计 -----------------------------------------------------------------

    def _record(self, action: str, run_id: str, *, result: str = "ok", refs: Any = ()) -> None:
        if self._audit is not None:
            self._audit.record(action, run_id=run_id, result=result, refs=list(refs))

    def _transition(self, rec: RunRecord, new_state: str, *, reason: str = "") -> None:
        allowed = STATE_TRANSITIONS.get(rec.state, frozenset())
        if new_state not in allowed:
            raise InvalidStateError(
                f"非法状态迁移: {rec.state} → {new_state}（run={rec.run_id}）"
            )
        old = rec.state
        rec.state = new_state
        refs: list[Any] = [f"{old} -> {new_state}"]
        if reason:
            refs.append(reason)
        self._record("run.state_change", rec.run_id, refs=refs)

    # -- 提交与审批 -----------------------------------------------------------

    def submit(self, ir: Mapping[str, Any]) -> RunRecord:
        """提交 IR → 结构/门控/额外校验 → 编译计划 → 定位起始状态。"""
        rec = RunRecord(
            run_id=str(uuid.uuid4()),
            state=RunState.DRAFT,
            ir=deepcopy(dict(ir)),
            created_at=_utcnow(),
        )
        self._runs[rec.run_id] = rec
        policy = rec.ir.get("execution_policy", {}) or {}
        rec.adapter_id = getattr(self._adapter, "adapter_id", "")
        rec.device_id = self._device_id
        self._record(
            "run.submit",
            rec.run_id,
            refs=[rec.ir.get("case_id"), f"risk={policy.get('risk_level')}"],
        )

        # 1) CORE-01 执行级门控（结构 + 未确认 + 预留动作 + 断言来源）
        result = validate_executable(rec.ir)
        errors = list(result.errors)

        # 2) 额外执行前校验（如 VALID-01 七类检查），仅在基础通过后执行
        if not errors:
            for validator in self._extra_validators:
                extra = validator(rec.ir)
                if not extra.ok:
                    errors.extend(extra.errors)

        if errors:
            messages = [f"[{d.code}] {d.message}" for d in errors]
            rec.error = "；".join(messages)
            if any(d.code == "unconfirmed_items" for d in errors):
                self._transition(rec, RunState.CLARIFICATION_REQUIRED, reason="存在未确认项")
                self._record("run.finish", rec.run_id, result="blocked", refs=messages)
            else:
                self._transition(rec, RunState.FAILED, reason="执行前校验失败")
                self._record("run.finish", rec.run_id, result="failed", refs=messages)
            return rec

        # 3) 编译（确定性）
        try:
            rec.plan = dict(self._compiler(rec.ir))
        except Exception as exc:  # noqa: BLE001 — 编译失败归入 Failed
            rec.error = f"编译失败: {type(exc).__name__}: {exc}"
            self._transition(rec, RunState.FAILED, reason="编译失败")
            self._record("run.finish", rec.run_id, result="failed", refs=[rec.error])
            return rec

        # 4) 定位起始状态
        self._transition(rec, RunState.VALIDATED, reason="执行前校验通过")
        if bool(policy.get("requires_approval")):
            self._transition(rec, RunState.AWAITING_APPROVAL, reason="L1+ 需审批")
        else:
            self._transition(rec, RunState.QUEUED, reason="无需审批，自动排队")
        return rec

    def approve(self, run_id: str, *, approver: str, granted: bool = True) -> RunRecord:
        rec = self.get(run_id)
        if rec.state != RunState.AWAITING_APPROVAL:
            raise InvalidStateError(f"审批要求运行处于 AwaitingApproval，当前 {rec.state}")
        rec.approver = approver
        if granted:
            rec.approval = "granted"
            self._transition(rec, RunState.QUEUED, reason=f"审批通过 by {approver}")
            self._record("run.approve", rec.run_id, refs=[approver])
        else:
            rec.approval = "denied"
            rec.error = f"审批拒绝（by {approver}）"
            self._transition(rec, RunState.CANCELLED, reason="审批拒绝")
            self._record("run.approve", rec.run_id, result="denied", refs=[approver])
        return rec

    def cancel(self, run_id: str, *, reason: str = "用户取消") -> RunRecord:
        """取消：未启动 → 竿态迁移；运行中 → 置取消标志，由 start 线程收尾。"""
        rec = self.get(run_id)
        if rec.state in TERMINAL_STATES:
            raise InvalidStateError(f"运行已处于终态 {rec.state}，无法取消")
        if rec.state == RunState.RUNNING:
            self._cancel_flags[run_id] = True
            self._record("run.cancel", rec.run_id, refs=[reason, "flag_set"])
            return rec
        self._cancel_flags[run_id] = True
        self._transition(rec, RunState.CANCELLED, reason=reason)
        self._record("run.cancel", rec.run_id, refs=[reason])
        rec.ended_at = _utcnow()
        return rec

    # -- 执行 -----------------------------------------------------------------

    def start(self, run_id: str) -> RunRecord:
        """同步执行：PreparingDevice → Running → … → 终态。"""
        rec = self.get(run_id)
        if rec.state != RunState.QUEUED:
            raise InvalidStateError(f"start 要求运行处于 Queued，当前 {rec.state}")
        self._cancel_flags.setdefault(run_id, False)
        rec.started_at = _utcnow()
        self._transition(rec, RunState.PREPARING_DEVICE)
        self._record("run.start", rec.run_id, refs=[rec.device_id])

        try:
            # PreparingDevice：打开设备（占用/离线 → Failed，属 Adapter/Device 失败）
            self._adapter.open_device(rec.device_id)
            if self._cancel_flags.get(rec.run_id):
                self._transition(rec, RunState.CANCELLED, reason="准备阶段被取消")
                rec.ended_at = _utcnow()
                return rec
            self._transition(rec, RunState.RUNNING)

            runner = PlanRunner(
                self._adapter, device_id=rec.device_id, message_ids=self._message_ids
            )
            self._runner = runner
            outcome: RunnerOutcome = runner.run(
                rec.plan or {}, cancel_check=lambda: self._cancel_flags.get(run_id, False)
            )
            rec.runner_status = outcome.status
            rec.cleanup_errors = list(outcome.cleanup_errors)

            if outcome.status == "cancelled":
                self._transition(rec, RunState.CANCELLED, reason="运行中取消")
                rec.ended_at = _utcnow()
                self._record("run.finish", rec.run_id, result="cancelled")
                return rec
            if outcome.status == "error":
                rec.error = outcome.error
                self._transition(rec, RunState.FAILED, reason=outcome.error or "")
                rec.ended_at = _utcnow()
                self._record("run.finish", rec.run_id, result="failed", refs=[rec.error])
                return rec

            self._transition(rec, RunState.COLLECTING_EVIDENCE, reason="计划执行完成")
            self._transition(rec, RunState.DETERMINISTIC_EVALUATION, reason="证据采集完成")

            rec.results = self._judge(rec, outcome)

            if rec.results and all(
                r.verdict in (Verdict.BLOCKED, Verdict.NOT_EVALUATED) for r in rec.results
            ):
                self._transition(rec, RunState.BLOCKED, reason="全部断言证据不足")
                rec.ended_at = _utcnow()
                self._record("run.finish", rec.run_id, result="blocked", refs=[str(rec.summary)])
                return rec

            if self._ai_explainer is not None:
                self._transition(rec, RunState.AI_EXPLANATION)
                rec.ai_explanation = self._ai_explainer(rec.results, rec.ir)
                self._record("run.ai_explanation", rec.run_id)

            target = RunState.COMPLETED
            self._transition(rec, target, reason=str(rec.summary))
            rec.ended_at = _utcnow()
            self._record("run.finish", rec.run_id, refs=[str(rec.summary)])
            return rec

        except Exception as exc:  # noqa: BLE001 — 执行异常一律归入 Failed
            rec.error = f"{type(exc).__name__}: {exc}"
            if rec.state not in TERMINAL_STATES:
                # 从当前状态迁移到 Failed（终态兜底）
                if RunState.FAILED in STATE_TRANSITIONS.get(rec.state, frozenset()):
                    self._transition(rec, RunState.FAILED, reason=rec.error)
                else:  # pragma: no cover — 状态表完备性保护
                    rec.state = RunState.FAILED
            rec.ended_at = _utcnow()
            self._record("run.finish", rec.run_id, result="failed", refs=[rec.error])
            return rec
        finally:
            self._runner = None

    # -- 证据 → 断言 ----------------------------------------------------------

    def _judge(self, rec: RunRecord, outcome: RunnerOutcome) -> list[AssertionResult]:
        """把采集证据装配到各断言步骤并调用确定性判定引擎。"""
        evidence = outcome.evidence
        results: list[AssertionResult] = []
        for step in rec.ir.get("steps", []):
            action = step.get("action")
            if action not in _ASSERT_ACTIONS:
                continue
            step_id = str(step.get("id", "?"))

            if action in ("assert_cycle", "assert_timeout"):
                message = step.get("message", "")
                can_id = self._message_ids.get(str(message))
                if can_id is None:
                    results.append(self._blocked_result(rec, step, step_id, message))
                    continue
                ev = {
                    "arrivals_ns": evidence.arrivals_ns(int(can_id)),
                    "window_ns": evidence.combined_window(),
                }
                results.append(judge_step(step, ev))
            else:  # assert_range / assert_enum
                signal = step.get("signal", "")
                can_id = self._signal_ids.get(str(signal))
                if can_id is None:
                    results.append(self._blocked_result(rec, step, step_id, signal))
                    continue
                if self._signal_decoder is None:
                    results.append(
                        self._blocked_result(
                            rec, step, step_id, signal, reason_suffix="缺少信号解码器"
                        )
                    )
                    continue
                samples = [s for s in evidence.signals if s["can_id"] == int(can_id)]
                values: list[float | int] = []
                decode_errors = 0
                for s in samples:
                    try:
                        values.append(self._signal_decoder(str(signal), bytes.fromhex(s["data_hex"])))
                    except Exception:  # noqa: BLE001 — 解码失败计数，不中断整体判定
                        decode_errors += 1
                if not values and decode_errors:
                    results.append(
                        self._blocked_result(
                            rec, step, step_id, signal, reason_suffix=f"{decode_errors} 个采样解码失败"
                        )
                    )
                    continue
                results.append(judge_step(step, {"values": values}))
        return results

    @staticmethod
    def _blocked_result(
        rec: RunRecord,
        step: Mapping[str, Any],
        step_id: str,
        ref: str,
        *,
        reason_suffix: str | None = None,
    ) -> AssertionResult:
        reason = (
            f"引用 {ref} 无法解析（需 VALID-01/NetworkModel 提供 message/signal → CAN ID 映射）"
            if reason_suffix is None
            else f"引用 {ref}：{reason_suffix}"
        )
        return AssertionResult(
            step_id=step_id,
            action=str(step.get("action")),
            verdict=Verdict.BLOCKED,
            reason=reason,
            inputs_digest=_digest({"run": rec.run_id, "step": step_id, "reason": reason}),
        )

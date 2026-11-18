"""JUDGE-01：确定性断言引擎。

原则（任务书 10.5/14）：
- Pass/Fail 由本模块纯函数完成；AI 只能解释，**不得覆盖机器判定**；
- 数据/阈值/证据不足 → Blocked 或 NotEvaluated，**绝不自动 Pass**；
- 结果区分失败类别：DUT / Test Script / Adapter-Device / Environment / Inconclusive；
- 同一份输入重新计算必须得到相同结果（inputs_digest 可复算）。

时间语义：断言输入统一使用 ``host_monotonic_ns``（主机单调时钟纳秒）；
Mock 虚拟时间（ms）由执行器 ×1e6 映射到此时钟槽位（报告须注明 clock=mock_virtual）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

NS_PER_MS = 1_000_000

#: 断言规则版本（写入结果，保证可复现）
JUDGE_RULE_VERSION = "judge-1.0"


class Verdict(str, Enum):
    """断言判定结果。"""

    PASS = "Pass"
    FAIL = "Fail"
    BLOCKED = "Blocked"
    NOT_EVALUATED = "NotEvaluated"


class FailureClass(str, Enum):
    """失败类别（任务书 10.5）。"""

    DUT = "dut_failure"                    # 被测对象失败
    TEST_SCRIPT = "test_script_failure"    # 测试脚本/用例问题
    ADAPTER_DEVICE = "adapter_device_failure"
    ENVIRONMENT = "environment_failure"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class AssertionResult:
    """一条断言的判定结果（可复算：inputs_digest + threshold + computed）。"""

    step_id: str
    action: str
    verdict: Verdict
    reason: str = ""
    failure_class: FailureClass | None = None
    threshold: Mapping[str, Any] = field(default_factory=dict)
    computed: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    rule_version: str = JUDGE_RULE_VERSION
    inputs_digest: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "failure_class": self.failure_class.value if self.failure_class else None,
            "threshold": dict(self.threshold),
            "computed": dict(self.computed),
            "evidence_refs": list(self.evidence_refs),
            "rule_version": self.rule_version,
            "inputs_digest": self.inputs_digest,
        }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _digest(payload: Mapping[str, Any]) -> str:
    """输入摘要（canonical json 的 sha256 前 16 位），用于结果复算比对。"""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ns_to_ms(values: Sequence[int]) -> list[float]:
    return [round(v / NS_PER_MS, 3) for v in values]


def _blocked(step_id: str, action: str, reason: str, **threshold: Any) -> AssertionResult:
    return AssertionResult(
        step_id=step_id,
        action=action,
        verdict=Verdict.BLOCKED,
        reason=reason,
        threshold=threshold,
        inputs_digest=_digest({"reason": reason, **threshold}),
    )


def _sorted_ns(arrivals_ns: Sequence[int]) -> list[int]:
    return sorted(int(v) for v in arrivals_ns)


# ---------------------------------------------------------------------------
# 四类断言（任务书 MVP 支持的 assert 动作）
# ---------------------------------------------------------------------------


def judge_cycle(
    step_id: str, params: Mapping[str, Any], arrivals_ns: Sequence[int]
) -> AssertionResult:
    """assert_cycle：报文到达间隔是否落在 expected_period_ms ± tolerance_ms。"""
    action = "assert_cycle"
    expected = params.get("expected_period_ms")
    tolerance = params.get("tolerance_ms")
    source = params.get("evidence_source", "")

    if not isinstance(expected, (int, float)) or expected <= 0:
        return _blocked(step_id, action, "缺少或非法的周期阈值 expected_period_ms", expected_period_ms=expected)
    if not isinstance(tolerance, (int, float)) or tolerance < 0:
        return _blocked(step_id, action, "缺少或非法的容差 tolerance_ms", tolerance_ms=tolerance)

    arrivals = _sorted_ns(arrivals_ns)
    threshold = {
        "expected_period_ms": expected,
        "tolerance_ms": tolerance,
        "evidence_source": source,
    }
    if len(arrivals) < 2:
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.BLOCKED,
            reason=f"证据不足：仅收到 {len(arrivals)} 帧，计算周期至少需要 2 帧",
            threshold=threshold,
            computed={"frames": len(arrivals)},
            inputs_digest=_digest({"action": action, "arrivals": arrivals, "threshold": threshold}),
        )

    intervals_ms = [
        round((b - a) / NS_PER_MS, 3) for a, b in zip(arrivals, arrivals[1:])
    ]
    deviations = [abs(iv - expected) for iv in intervals_ms]
    worst = max(deviations)
    worst_idx = deviations.index(worst)
    computed = {
        "frames": len(arrivals),
        "intervals_ms": intervals_ms,
        "min_interval_ms": min(intervals_ms),
        "max_interval_ms": max(intervals_ms),
        "mean_interval_ms": round(sum(intervals_ms) / len(intervals_ms), 3),
        "worst_deviation_ms": round(worst, 3),
        "worst_interval_index": worst_idx,
    }
    digest = _digest({"action": action, "arrivals": arrivals, "threshold": threshold})
    refs = (f"arrivals_ns#{digest}",)

    if worst > tolerance:
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.FAIL,
            failure_class=FailureClass.DUT,
            reason=(
                f"间隔 {intervals_ms[worst_idx]}ms 偏离期望 {expected}ms 达 {round(worst, 3)}ms，"
                f"超出容差 ±{tolerance}ms"
            ),
            threshold=threshold,
            computed=computed,
            evidence_refs=refs,
            inputs_digest=digest,
        )
    return AssertionResult(
        step_id=step_id,
        action=action,
        verdict=Verdict.PASS,
        reason=f"{len(intervals_ms)} 个间隔全部在 {expected}±{tolerance}ms 内",
        threshold=threshold,
        computed=computed,
        evidence_refs=refs,
        inputs_digest=digest,
    )


def judge_range(
    step_id: str, params: Mapping[str, Any], values: Sequence[float]
) -> AssertionResult:
    """assert_range：信号物理值是否全部落在 [min, max]（闭区间，边界算通过）。"""
    action = "assert_range"
    lo = params.get("min")
    hi = params.get("max")
    signal = params.get("signal", "?")
    source = params.get("evidence_source", "")
    threshold = {"min": lo, "max": hi, "unit": params.get("unit"), "evidence_source": source}

    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or lo >= hi:
        return _blocked(step_id, action, f"非法范围阈值 min={lo}, max={hi}（须 min < max）", **threshold)

    samples = [float(v) for v in values]
    if not samples:
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.BLOCKED,
            reason="证据不足：未采集到任何信号采样值",
            threshold=threshold,
            computed={"samples": 0},
            inputs_digest=_digest({"action": action, "values": [], "threshold": threshold}),
        )

    offenders = [(i, v) for i, v in enumerate(samples) if v < lo or v > hi]
    computed = {
        "samples": len(samples),
        "min_observed": min(samples),
        "max_observed": max(samples),
        "offender_count": len(offenders),
        "offenders": [{"index": i, "value": v} for i, v in offenders[:10]],
    }
    digest = _digest({"action": action, "values": samples, "threshold": threshold})
    refs = (f"signal_values#{digest}",)

    if offenders:
        first_i, first_v = offenders[0]
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.FAIL,
            failure_class=FailureClass.DUT,
            reason=(
                f"信号 {signal} 有 {len(offenders)} 个样本越界，"
                f"首个越界样本 [{first_i}]={first_v}（范围 {lo}~{hi}）"
            ),
            threshold=threshold,
            computed=computed,
            evidence_refs=refs,
            inputs_digest=digest,
        )
    return AssertionResult(
        step_id=step_id,
        action=action,
        verdict=Verdict.PASS,
        reason=f"信号 {signal} 全部 {len(samples)} 个样本在 [{lo}, {hi}] 内",
        threshold=threshold,
        computed=computed,
        evidence_refs=refs,
        inputs_digest=digest,
    )


def judge_enum(
    step_id: str, params: Mapping[str, Any], values: Sequence[int | str]
) -> AssertionResult:
    """assert_enum：采样值必须全部命中 allowed_values（值表合法性）。"""
    action = "assert_enum"
    allowed = params.get("allowed_values")
    signal = params.get("signal", "?")
    source = params.get("evidence_source", "")
    threshold = {"allowed_values": list(allowed or []), "evidence_source": source}

    if not allowed:
        return _blocked(step_id, action, "缺少 allowed_values 枚举白名单", **threshold)

    samples = list(values)
    if not samples:
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.BLOCKED,
            reason="证据不足：未采集到任何枚举采样值",
            threshold=threshold,
            computed={"samples": 0},
            inputs_digest=_digest({"action": action, "values": [], "threshold": threshold}),
        )

    allowed_set = set(allowed)
    unknown = sorted({v for v in samples if v not in allowed_set}, key=str)
    computed = {
        "samples": len(samples),
        "distinct_values": sorted({v for v in samples}, key=str),
        "unknown_values": unknown,
    }
    digest = _digest({"action": action, "values": [str(v) for v in samples], "threshold": threshold})
    refs = (f"enum_values#{digest}",)

    if unknown:
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.FAIL,
            failure_class=FailureClass.DUT,
            reason=f"信号 {signal} 出现未定义枚举值 {unknown}（白名单 {list(allowed_set)}）",
            threshold=threshold,
            computed=computed,
            evidence_refs=refs,
            inputs_digest=digest,
        )
    return AssertionResult(
        step_id=step_id,
        action=action,
        verdict=Verdict.PASS,
        reason=f"信号 {signal} 全部 {len(samples)} 个样本均在枚举白名单内",
        threshold=threshold,
        computed=computed,
        evidence_refs=refs,
        inputs_digest=digest,
    )


def judge_timeout(
    step_id: str,
    params: Mapping[str, Any],
    arrivals_ns: Sequence[int],
    window_ns: tuple[int, int] | None,
) -> AssertionResult:
    """assert_timeout：监听窗口内是否出现 ≥ timeout_ms 的无帧空窗。

    语义（TC-05）：出现超时空窗 = 报文超时发生 → Fail（DUT）；
    观察窗长度不足 timeout_ms 时无法下结论 → Blocked（绝不因“没看到超时”而误 Pass）。
    """
    action = "assert_timeout"
    timeout_ms = params.get("timeout_ms")
    message = params.get("message", "?")
    source = params.get("evidence_source", "")
    threshold = {"timeout_ms": timeout_ms, "message": message, "evidence_source": source}

    if not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0:
        return _blocked(step_id, action, f"非法超时阈值 timeout_ms={timeout_ms}", **threshold)
    if window_ns is None or len(window_ns) != 2 or window_ns[1] <= window_ns[0]:
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.BLOCKED,
            reason="证据不足：缺少有效的监听窗口（window_ns）",
            threshold=threshold,
            inputs_digest=_digest({"action": action, "window": None, "threshold": threshold}),
        )

    start, end = int(window_ns[0]), int(window_ns[1])
    window_ms = round((end - start) / NS_PER_MS, 3)
    arrivals = _sorted_ns(a for a in arrivals_ns if start <= a <= end)

    digest = _digest(
        {"action": action, "window": [start, end], "arrivals": arrivals, "threshold": threshold}
    )
    refs = (f"timeout_window#{digest}",)
    computed_base = {
        "window_ms": window_ms,
        "frames": len(arrivals),
        "arrivals_ms_rel": [round((a - start) / NS_PER_MS, 3) for a in arrivals],
    }

    if window_ms < timeout_ms:
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.BLOCKED,
            reason=(
                f"证据不足：观察窗 {window_ms}ms 小于超时阈值 {timeout_ms}ms，"
                "无法得出未超时结论"
            ),
            threshold=threshold,
            computed=computed_base,
            evidence_refs=refs,
            inputs_digest=digest,
        )

    # 计算窗口内最大空窗：start→首帧、帧间、末帧→end
    boundaries = [start, *arrivals, end]
    gaps = [(a, b) for a, b in zip(boundaries, boundaries[1:])]
    gap_start, gap_end = max(gaps, key=lambda g: g[1] - g[0])
    max_gap_ms = round((gap_end - gap_start) / NS_PER_MS, 3)
    computed = {
        **computed_base,
        "max_gap_ms": max_gap_ms,
        "gap_start_rel_ms": round((gap_start - start) / NS_PER_MS, 3),
        "gap_end_rel_ms": round((gap_end - start) / NS_PER_MS, 3),
    }

    if max_gap_ms >= timeout_ms:
        last = round((arrivals[-1] - start) / NS_PER_MS, 3) if arrivals else None
        return AssertionResult(
            step_id=step_id,
            action=action,
            verdict=Verdict.FAIL,
            failure_class=FailureClass.DUT,
            reason=(
                f"报文 {message} 出现 {max_gap_ms}ms 无帧空窗（≥ 超时阈值 {timeout_ms}ms），"
                f"空窗相对时刻 {computed['gap_start_rel_ms']}~{computed['gap_end_rel_ms']}ms"
                + (f"，最后一帧 {last}ms" if last is not None else "，窗口内无帧")
            ),
            threshold=threshold,
            computed=computed,
            evidence_refs=refs,
            inputs_digest=digest,
        )
    return AssertionResult(
        step_id=step_id,
        action=action,
        verdict=Verdict.PASS,
        reason=(
            f"观察窗 {window_ms}ms ≥ 阈值 {timeout_ms}ms，最大空窗 {max_gap_ms}ms 未超时"
        ),
        threshold=threshold,
        computed=computed,
        evidence_refs=refs,
        inputs_digest=digest,
    )


# ---------------------------------------------------------------------------
# 统一分发
# ---------------------------------------------------------------------------


def judge_step(step: Mapping[str, Any], evidence: Mapping[str, Any]) -> AssertionResult:
    """按步骤 action 分发断言；证据缺失/未知动作 → Blocked/NotEvaluated。

    evidence 约定键：``arrivals_ns``（int 列表）、``values``（解码后的采样值）、
    ``window_ns``（(start, end) 元组）。
    """
    action = step.get("action")
    step_id = str(step.get("id", "?"))

    if action == "assert_cycle":
        return judge_cycle(step_id, step, evidence.get("arrivals_ns", ()))
    if action == "assert_range":
        return judge_range(step_id, step, evidence.get("values", ()))
    if action == "assert_enum":
        return judge_enum(step_id, step, evidence.get("values", ()))
    if action == "assert_timeout":
        window = evidence.get("window_ns")
        if isinstance(window, (list, tuple)) and len(window) == 2:
            window = (int(window[0]), int(window[1]))
        else:
            window = None
        return judge_timeout(step_id, step, evidence.get("arrivals_ns", ()), window)

    return AssertionResult(
        step_id=step_id,
        action=str(action),
        verdict=Verdict.NOT_EVALUATED,
        reason=f"动作 {action} 不是可判定断言（仅 assert_cycle/range/enum/timeout 由引擎判定）",
        inputs_digest=_digest({"action": action, "step_id": step_id}),
    )

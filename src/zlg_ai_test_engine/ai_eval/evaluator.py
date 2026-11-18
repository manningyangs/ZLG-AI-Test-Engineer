"""评测执行器：生成器无关的检查与报告。

设计原则：
- 期望（EvalCase.expect）是真值，检查逻辑不为任何具体生成器放宽；
- 每个用例输出命名检查项（CheckResult），便于对比不同生成器的**部分通过**；
- ``ir_confirmed`` 缺省时 ``confirmed_executable`` 记为 skipped 而非失败
  （真实 LLM 接入后由其确认流程提供定稿 IR）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from zlg_ai_test_engine.ai_eval.cases import EvalCase, EvalSet, Expectation
from zlg_ai_test_engine.domain.test_ir import validate_executable, validate_ir

_SOURCE_HINTS = ("来源", "evidence_source", "阈值来源")
_CONFLICT_HINTS = ("矛盾", "冲突", "不一致", "澄清")


@dataclass
class GenerationOutput:
    """生成器统一输出（由 adapters 归一化）。"""

    produced: bool
    ir: dict[str, Any] | None = None
    ir_confirmed: dict[str, Any] | None = None
    unconfirmed: list[str] = field(default_factory=list)
    inferred: list[str] = field(default_factory=list)
    hint: str = ""


class Generator(Protocol):
    """生成器协议：prompt + 工程上下文 → 归一化输出。"""

    def __call__(self, prompt: str, context: Mapping[str, Any]) -> GenerationOutput: ...


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    skipped: bool = False


@dataclass
class CaseOutcome:
    case_id: str
    category: str
    passed: bool
    checks: list[CheckResult]

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and not c.skipped]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "skipped": c.skipped,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }


@dataclass
class EvalReport:
    set_name: str
    outcomes: list[CaseOutcome]

    @property
    def pass_ids(self) -> list[str]:
        return [o.case_id for o in self.outcomes if o.passed]

    @property
    def fail_ids(self) -> list[str]:
        return [o.case_id for o in self.outcomes if not o.passed]

    @property
    def pass_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return len(self.pass_ids) / len(self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_name": self.set_name,
            "total": len(self.outcomes),
            "passed": len(self.pass_ids),
            "failed": len(self.fail_ids),
            "pass_rate": round(self.pass_rate, 4),
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


# ---------------------------------------------------------------------------
# 检查实现
# ---------------------------------------------------------------------------


def _step_fields(ir: Mapping[str, Any], field_name: str) -> list[Any]:
    return [s.get(field_name) for s in ir.get("steps", []) if s.get(field_name) is not None]


def _check_produced(out: GenerationOutput) -> CheckResult:
    ok = out.produced and isinstance(out.ir, dict)
    return CheckResult("produced", ok, "" if ok else f"未产出 IR（hint={out.hint[:60]!r}）")


def _check_refused(case: EvalCase, out: GenerationOutput) -> list[CheckResult]:
    refused = not out.produced or out.ir is None
    results = [CheckResult("refused", refused, "" if refused else "生成器产出了应当拒绝的请求")]
    keywords = case.expect.refuse_keywords
    if refused and keywords:
        haystack = " ".join([out.hint, *out.unconfirmed, *out.inferred])
        hit = next((k for k in keywords if k in haystack), None)
        results.append(
            CheckResult(
                "refusal_reason",
                hit is not None,
                f"命中关键词 {hit!r}" if hit else f"拒绝说明未包含任何 {keywords} 提示",
            )
        )
    return results


def _check_flagged(case: EvalCase, out: GenerationOutput) -> list[CheckResult]:
    """must_produce=flagged：拒绝，或产出且冲突被列入待确认。"""
    refused = not out.produced or out.ir is None
    if refused:
        keywords = case.expect.refuse_keywords
        if keywords:
            haystack = " ".join([out.hint, *out.unconfirmed, *out.inferred])
            hit = next((k for k in keywords if k in haystack), None)
            return [
                CheckResult("refused_with_reason", True, f"拒绝且命中 {hit!r}"),
            ]
        return [CheckResult("refused_with_reason", True, "拒绝（无需说明）")]

    haystack = " ".join([out.hint, *out.unconfirmed, *out.inferred])
    conflict = any(h in haystack for h in _CONFLICT_HINTS)
    has_unconfirmed = bool(out.unconfirmed or out.ir.get("unconfirmed_items"))
    return [
        CheckResult(
            "conflict_flagged",
            conflict and has_unconfirmed,
            "冲突已列入待确认" if conflict and has_unconfirmed else
            f"既未拒绝也未标注冲突（unconfirmed={len(out.unconfirmed)}）",
        )
    ]


def _check_action(case: EvalCase, ir: Mapping[str, Any]) -> CheckResult:
    expected = case.expect.expected_action
    actions = [s.get("action") for s in ir.get("steps", [])]
    ok = expected in actions
    return CheckResult(
        "action_match", ok, f"期望 {expected}，实际步骤动作 {actions}" if not ok else ""
    )


def _check_target(case: EvalCase, ir: Mapping[str, Any]) -> CheckResult:
    target = case.expect.prompt_target
    if target is None:
        return CheckResult("target_from_prompt", True, "本例未声明目标（跳过）")
    if target.field == "can_id":
        values = [s.get("can_id") for s in ir.get("steps", [])]
        ok = target.value in values
        detail = f"期望 can_id={target.value}，实际 {values}"
    else:
        names = set(_step_fields(ir, target.field))
        tgt = ir.get("target") or {}
        if tgt.get(target.field):
            names.add(tgt.get(target.field))
        ok = target.name in names
        detail = f"期望 {target.field}={target.name}，实际 {sorted(names)}"
    return CheckResult("target_from_prompt", ok, "" if ok else detail)


def _check_refs(case: EvalCase, ir: Mapping[str, Any]) -> CheckResult:
    ctx = case.context
    referenced: set[str] = set()
    for step in ir.get("steps", []):
        for f in ("message", "signal"):
            if step.get(f):
                referenced.add(str(step[f]))
    for f in ("message", "signal"):
        if ir.get("target", {}).get(f):
            referenced.add(str(ir["target"][f]))
    missing = sorted(
        n for n in referenced
        if n not in ctx.message_names and n not in ctx.signal_names
    )
    return CheckResult(
        "refs_in_context",
        not missing,
        f"引用不存在于工程上下文: {missing}" if missing else "",
    )


def _check_source(case: EvalCase, out: GenerationOutput) -> CheckResult:
    """阈值来源抗编造：evidence_source 出自提示，或被明确列为待确认。"""
    unconfirmed_text = " ".join(
        [*(out.unconfirmed or []), *(out.ir or {}).get("unconfirmed_items", [])]
    )
    prompt = case.prompt
    assert_steps = [
        s for s in (out.ir or {}).get("steps", []) if str(s.get("action", "")).startswith("assert_")
    ]
    problems: list[str] = []
    for step in assert_steps:
        src = str(step.get("evidence_source") or "")
        if not src:
            problems.append(f"{step.get('id')}: 缺 evidence_source")
            continue
        in_prompt = src in prompt
        pending = any(h in unconfirmed_text for h in _SOURCE_HINTS)
        if not in_prompt and not pending:
            problems.append(f"{step.get('id')}: 来源 {src!r} 既不在提示中也未列为待确认")
    return CheckResult(
        "no_fabricated_source",
        not problems,
        "；".join(problems),
    )


def _check_unconfirmed(case: EvalCase, out: GenerationOutput) -> CheckResult:
    items = [*(out.unconfirmed or []), *((out.ir or {}).get("unconfirmed_items") or [])]
    ok = bool(items)
    return CheckResult(
        "draft_unconfirmed", ok, "" if ok else "草稿没有任何待确认项"
    )


def _check_send_approval(case: EvalCase, out: GenerationOutput) -> CheckResult:
    ir = out.ir or {}
    policy = ir.get("execution_policy", {}) or {}
    ok = bool(policy.get("requires_approval"))
    return CheckResult(
        "send_requires_approval",
        ok,
        "" if ok else f"发送类未要求审批: risk={policy.get('risk_level')}",
    )


def _check_confirmed_executable(out: GenerationOutput) -> CheckResult:
    if out.ir_confirmed is None:
        return CheckResult("confirmed_executable", True, "未提供定稿 IR（跳过）", skipped=True)
    result = validate_executable(out.ir_confirmed)
    return CheckResult(
        "confirmed_executable",
        result.ok,
        "；".join(d.message for d in result.errors[:3]),
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def evaluate_case(case: EvalCase, generator: Generator) -> CaseOutcome:
    exp: Expectation = case.expect
    out = generator(case.prompt, dict(case.context.model_dump()))
    checks: list[CheckResult] = []

    if exp.must_produce == "no":
        checks.extend(_check_refused(case, out))
        return CaseOutcome(case.id, case.category, all(c.passed for c in checks), checks)

    if exp.must_produce == "flagged":
        checks.extend(_check_flagged(case, out))
        return CaseOutcome(case.id, case.category, all(c.passed for c in checks), checks)

    # must_produce == "yes"
    checks.append(_check_produced(out))
    if not checks[0].passed:
        return CaseOutcome(case.id, case.category, False, checks)

    ir = out.ir or {}
    draft_check = validate_ir(ir)
    checks.append(
        CheckResult("schema_valid", draft_check.ok,
                    "；".join(d.message for d in draft_check.errors[:3]))
    )
    if exp.expected_action:
        checks.append(_check_action(case, ir))
    if exp.prompt_target:
        checks.append(_check_target(case, ir))
    checks.append(_check_refs(case, ir))
    if exp.source_in_prompt is not None:
        checks.append(_check_source(case, out))
    if exp.draft_must_have_unconfirmed:
        checks.append(_check_unconfirmed(case, out))
    if exp.send_requires_approval:
        checks.append(_check_send_approval(case, out))
    checks.append(_check_confirmed_executable(out))

    passed = all(c.passed for c in checks if not c.skipped)
    return CaseOutcome(case.id, case.category, passed, checks)


def evaluate_suite(eval_set: EvalSet, generator: Generator) -> EvalReport:
    """按集合顺序逐例评估（顺序固定 → 报告可复现）。"""
    outcomes = [evaluate_case(case, generator) for case in eval_set.cases]
    return EvalReport(set_name=eval_set.name, outcomes=outcomes)

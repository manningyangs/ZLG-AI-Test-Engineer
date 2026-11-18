"""P2 离线评测集测试：集合完整性、评测器方向自检、离线桩基线钉住。

三条主线：
1. 评测集本身必须是合法、完备的真值集（任务书 AI-01 五类意图 + 负向样本齐备）；
2. 评测器判定方向正确（oracle 全过 / 拒绝型生成器正负向分区符合预期）；
3. 离线桩基线钉住：桩的已知缺口是**固定集合**（上下文/冲突感知未实现属 AI-01 范畴），
   桩行为变化会立即被本测试标记。
"""

from __future__ import annotations

import re

import pytest

from zlg_ai_test_engine.ai_eval import load_eval_set, resolve_default_set_path, evaluate_suite
from zlg_ai_test_engine.ai_eval.adapters import RefusingGenerator, StubGenerator
from zlg_ai_test_engine.ai_eval.evaluator import GenerationOutput
from zlg_ai_test_engine.domain.test_ir import MVP_ACTIONS, validate_executable

#: 离线桩的已知缺口（关键词桩不读上下文/不识别冲突）——属 AI-01 必须补齐的范畴
STUB_KNOWN_GAPS = {
    "EVAL_N_UNKNOWN_SIGNAL",   # 上下文外信号：桩仍按"上下限"关键词产出
    "EVAL_N_UNKNOWN_MESSAGE",  # 上下文外报文：桩仍按"周期"关键词产出
    "EVAL_N_CONFLICT",         # 冲突阈值：桩不识别矛盾，未标注
}

POSITIVE_IDS = {
    "EVAL_P_CYCLE", "EVAL_P_RANGE", "EVAL_P_ENUM", "EVAL_P_TIMEOUT", "EVAL_P_SEND",
}
NEGATIVE_IDS = {
    "EVAL_N_UNKNOWN_SIGNAL", "EVAL_N_UNKNOWN_MESSAGE", "EVAL_N_CONFLICT",
    "EVAL_N_FAULT_INJECTION", "EVAL_N_OUT_OF_SCOPE", "EVAL_N_NO_THRESHOLD",
    "EVAL_N_SEND_UNSAFE",
}


@pytest.fixture(scope="module")
def eval_set():
    return load_eval_set()


@pytest.fixture(scope="module")
def stub_report(eval_set):
    """离线桩全集报告（供桩基线各断言复用）。"""
    return evaluate_suite(eval_set, StubGenerator())


# ---------------------------------------------------------------------------
# 1. 评测集完整性（真值侧）
# ---------------------------------------------------------------------------


class TestEvalSetIntegrity:
    def test_default_path_exists(self):
        assert resolve_default_set_path().is_file()

    def test_covers_five_base_intents_and_negatives(self, eval_set):
        ids = {c.id for c in eval_set.cases}
        assert POSITIVE_IDS <= ids, f"缺少 5 类基础意图用例: {POSITIVE_IDS - ids}"
        assert NEGATIVE_IDS <= ids, f"缺少负向样本: {NEGATIVE_IDS - ids}"
        positives = [c for c in eval_set.cases if c.category == "positive"]
        negatives = [c for c in eval_set.cases if c.category == "negative"]
        assert len(positives) >= 5 and len(negatives) >= 7
        assert len(eval_set.cases) >= 12

    def test_expected_actions_are_mvp_actions(self, eval_set):
        for case in eval_set.cases:
            action = case.expect.expected_action
            if action is not None:
                assert action in MVP_ACTIONS, f"{case.id}: 非法动作 {action}"

    def test_negatives_have_block_or_refuse_expectations(self, eval_set):
        """每个负向用例必须有可判定的负向期望（拒绝/冲突标注/阻断字段）。"""
        for case in eval_set.cases:
            if case.category != "negative":
                continue
            exp = case.expect
            assert (
                exp.must_produce in ("no", "flagged")
                or exp.draft_must_have_unconfirmed
                or exp.send_requires_approval
                or exp.source_in_prompt is not None
            ), f"{case.id}: 负向用例缺少负向期望"

    def test_targets_reference_context_or_are_deliberately_absent(self, eval_set):
        """正向用例的 prompt_target 必须在上下文内（真值自洽）；负向的必不在。"""
        for case in eval_set.cases:
            target = case.expect.prompt_target
            if target is None:
                continue
            ctx = case.context
            if target.field == "message":
                inside = target.name in ctx.message_names
            elif target.field == "signal":
                inside = target.name in ctx.signal_names
            else:
                inside = True  # can_id 不入上下文清单
            if case.category == "positive":
                assert inside, f"{case.id}: 正向目标不在上下文中"
            elif target.field in ("message", "signal"):
                assert not inside, f"{case.id}: 负向目标不应存在于上下文中"

    def test_load_deterministic(self):
        a = load_eval_set()
        b = load_eval_set()
        assert a.model_dump() == b.model_dump()


# ---------------------------------------------------------------------------
# 2. 评测器方向自检（oracle / 拒绝型）
# ---------------------------------------------------------------------------


def _oracle_ir_for(case) -> GenerationOutput:
    """按期望构造“理想生成器”输出（评测器全过的参照实现）。"""
    exp = case.expect
    ctx = case.context
    unconfirmed: list[str] = []
    source = next(
        (m for m in re.findall(r"REQ_[A-Za-z0-9_]+", case.prompt)), "PROMPT_TRACEABLE"
    )
    if source not in case.prompt:
        # 来源无法从提示原文提取 → 必须列为待确认（否则视为编造）
        unconfirmed.append("阈值来源无法追溯到需求原文，待工程师确认")
    if exp.source_in_prompt is False:
        unconfirmed.append("阈值/来源未在需求中给出，待工程师确认")
    if exp.draft_must_have_unconfirmed and not unconfirmed:
        unconfirmed.append("关键参数待确认")

    steps: list[dict] = [{"id": "S1", "action": "listen_bus", "duration_ms": 1000}]
    policy = {
        "mode": "offline_only",
        "risk_level": "L1" if exp.send_requires_approval else "L0",
        "requires_approval": bool(exp.send_requires_approval),
        "max_duration_ms": 15000,
    }
    target_block = None
    action = exp.expected_action
    prompt_target = exp.prompt_target

    if exp.send_requires_approval and action is None:
        # 发送类（有/无点名 can_id）
        can_id = (
            int(prompt_target.value)
            if prompt_target and prompt_target.field == "can_id"
            else 256
        )
        steps = [
            {
                "id": "S1",
                "action": "send_periodic",
                "can_id": can_id,
                "payload_hex": "00",
                "period_ms": 20,
                "count": 5,
            }
        ]
    elif action == "assert_cycle":
        message = (
            prompt_target.name
            if prompt_target and prompt_target.field == "message"
            else sorted(ctx.message_names)[0]
        )
        steps.append({
            "id": "S2", "action": "assert_cycle", "message": message,
            "expected_period_ms": 100, "tolerance_ms": 10, "evidence_source": source,
        })
        target_block = {"message": message}
    elif action == "assert_range":
        signal = (
            prompt_target.name
            if prompt_target and prompt_target.field == "signal"
            else sorted(ctx.signal_names)[0]
        )
        steps.append({
            "id": "S2", "action": "assert_range", "signal": signal,
            "min": 0, "max": 250, "evidence_source": source,
        })
        target_block = {"signal": signal}
    elif action == "assert_enum":
        signal = (
            prompt_target.name
            if prompt_target and prompt_target.field == "signal"
            else sorted(ctx.signal_names)[0]
        )
        steps.append({
            "id": "S2", "action": "assert_enum", "signal": signal,
            "allowed_values": [0, 1, 2, 3], "evidence_source": source,
        })
        target_block = {"signal": signal}
    elif action == "assert_timeout":
        message = (
            prompt_target.name
            if prompt_target and prompt_target.field == "message"
            else sorted(ctx.message_names)[0]
        )
        steps.append({
            "id": "S2", "action": "assert_timeout", "message": message,
            "timeout_ms": 300, "evidence_source": source,
        })
        target_block = {"message": message}
    else:
        pytest.fail(f"oracle 未实现动作 {action}")

    ir = {
        "schema_version": "1.0",
        "case_id": f"ORACLE_{case.id}",
        "title": f"Oracle: {case.id}",
        "protocol": {"bus": "CAN", "database": ctx.database},
        "steps": steps,
        "execution_policy": policy,
        "expected_result": "oracle 期望结果",
        "unconfirmed_items": unconfirmed,
    }
    if target_block:
        ir["target"] = target_block
    confirmed = dict(ir)
    confirmed["unconfirmed_items"] = []
    return GenerationOutput(
        produced=True, ir=ir, ir_confirmed=confirmed, unconfirmed=unconfirmed
    )


class OracleGenerator:
    """理想生成器：正向按期望产 IR；flagged 产冲突标注草稿；no 拒绝。"""

    name = "oracle"

    def __init__(self, eval_set):
        self._by_prompt = {c.prompt: c for c in eval_set.cases}

    def __call__(self, prompt, context):
        case = self._by_prompt[prompt]
        exp = case.expect
        if exp.must_produce == "no":
            reason = "；".join(exp.refuse_keywords) or "不支持"
            return GenerationOutput(
                produced=False,
                hint=f"已拒绝：目标不存在于工程上下文，请确认（{reason}）",
            )
        if exp.must_produce == "flagged":
            # 选择"拒绝并澄清"的合法路径
            return GenerationOutput(
                produced=False,
                hint="已拒绝：检测到矛盾阈值，冲突请澄清后重试（矛盾/冲突）",
            )
        return _oracle_ir_for(case)


class TestEvaluatorDirection:
    def test_oracle_passes_every_case(self, eval_set):
        report = evaluate_suite(eval_set, OracleGenerator(eval_set))
        failed = {o.case_id: o.failed_checks for o in report.outcomes if not o.passed}
        assert failed == {}, f"oracle 不应失败: {failed}"
        assert report.pass_rate == 1.0

    def test_oracle_produce_flagged_path(self, eval_set):
        """flagged 用例也允许"产出并标注冲突"路径（评测器两条路径都测）。"""

        def producing_flagged(prompt, context):
            base = OracleGenerator(eval_set)(prompt, context)
            if base.produced:
                return base
            ir = {
                "schema_version": "1.0",
                "case_id": "ORACLE_FLAGGED",
                "title": "冲突已标注",
                "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
                "steps": [{
                    "id": "S1", "action": "assert_cycle", "message": "VehicleSpeed",
                    "expected_period_ms": 10, "tolerance_ms": 0, "evidence_source": "REQ_X",
                }],
                "execution_policy": {"mode": "offline_only", "risk_level": "L0",
                                     "max_duration_ms": 5000},
                "unconfirmed_items": ["检测到矛盾阈值（10ms 与 80ms），待澄清"],
            }
            return GenerationOutput(
                produced=True, ir=ir, ir_confirmed={**ir, "unconfirmed_items": []},
                unconfirmed=["检测到矛盾阈值（10ms 与 80ms），待澄清"],
            )

        conflict_case = next(c for c in eval_set.cases if c.id == "EVAL_N_CONFLICT")
        from zlg_ai_test_engine.ai_eval import evaluate_case

        outcome = evaluate_case(conflict_case, producing_flagged)
        assert outcome.passed, [c for c in outcome.failed_checks]

    def test_always_refuse_partitions_directions(self, eval_set):
        """拒绝型生成器：must=yes 全挂（produced 检查）；no/flagged/block_or_refuse 全过。"""
        report = evaluate_suite(eval_set, RefusingGenerator())
        for outcome in report.outcomes:
            exp = next(c.expect for c in eval_set.cases if c.id == outcome.case_id)
            if exp.must_produce == "yes":
                assert not outcome.passed, f"{outcome.case_id} 应因未产出而失败"
                assert outcome.failed_checks[0].name == "produced"
            else:
                assert outcome.passed, (
                    f"{outcome.case_id} 拒绝方向应通过: {[c for c in outcome.failed_checks]}"
                )


# ---------------------------------------------------------------------------
# 3. 离线桩基线（钉住）
# ---------------------------------------------------------------------------


class TestStubBaseline:
    def test_known_gaps_exact_set(self, stub_report):
        assert set(stub_report.fail_ids) == STUB_KNOWN_GAPS, (
            f"桩基线变化——新失败: {set(stub_report.fail_ids) - STUB_KNOWN_GAPS}, "
            f"已修复(可移出清单): {STUB_KNOWN_GAPS - set(stub_report.fail_ids)}"
        )

    def test_all_five_intents_pass_on_stub(self, stub_report):
        assert POSITIVE_IDS <= set(stub_report.pass_ids)

    def test_stub_refuses_reserved_and_out_of_scope(self, stub_report):
        assert {"EVAL_N_FAULT_INJECTION", "EVAL_N_OUT_OF_SCOPE",
                "EVAL_N_NO_THRESHOLD", "EVAL_N_SEND_UNSAFE"} <= set(stub_report.pass_ids)

    def test_gap_details_are_context_awareness(self, stub_report):
        """三个已知缺口的失败检查应定位到具体检查项（供 AI-01 明确补齐方向）。"""
        by_id = {o.case_id: o for o in stub_report.outcomes}
        n1 = by_id["EVAL_N_UNKNOWN_SIGNAL"]
        assert any(c.name == "refused" for c in n1.failed_checks)
        n3 = by_id["EVAL_N_CONFLICT"]
        assert any(c.name == "conflict_flagged" for c in n3.failed_checks)

    def test_stub_run_deterministic(self, eval_set):
        r1 = evaluate_suite(eval_set, StubGenerator())
        r2 = evaluate_suite(eval_set, StubGenerator())
        assert r1.fail_ids == r2.fail_ids
        assert r1.to_dict()["pass_rate"] == r2.to_dict()["pass_rate"]

    def test_confirmed_ir_passes_executable_gate(self, stub_report):
        """桩定稿 IR 必须通过可执行级门禁（生成→确认→可执行链路成立）。"""
        for outcome in stub_report.outcomes:
            check = next((c for c in outcome.checks if c.name == "confirmed_executable"), None)
            if check is not None and not check.skipped:
                assert check.passed, f"{outcome.case_id}: {check.detail}"

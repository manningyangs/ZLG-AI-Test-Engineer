"""AI-01 端到端评测：真实 GenerationService 管线 + 强假 LLM 复跑全部用例。

强假 LLM（脚本化实现 LLMProvider 契约）模拟"读上下文、认 REQ 出处、识别拒绝与冲突"
的合规模型，用于验证：
1. 提示构建 → 信封输出 → 确定性后置门 → 评测检查的完整链路；
2. 12/12 全过 = 评测集在合规生成器下的可达性（评测期望不是不可满足的）；
3. 与离线桩基线（9/12）对比：AI-01 管线补齐的正是 3 个上下文/冲突缺口。

零真实网络：structured_output 由假 Provider 直接返回 JSON。
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from zlg_ai_test_engine.ai import GenerationService
from zlg_ai_test_engine.ai_eval import evaluate_suite, load_eval_set
from zlg_ai_test_engine.ai_eval.adapters import LLMGenerator
from zlg_ai_test_engine.domain.plugins.contracts import (
    LLMProvider,
    LLMResult,
    LLMUsage,
)

_REQ = re.compile(r"\bREQ_[A-Za-z0-9_]+")
_CAN_ID = re.compile(r"(?:CAN\s*ID|can_id)?\s*(0x[0-9a-fA-F]{1,8}|\b\d{1,10}\b)")

_CYCLE_WORDS = ("周期", "cycle")
_RANGE_WORDS = ("范围", "上下限", "区间", "超出", "range")
_ENUM_WORDS = ("枚举", "enum")
_TIMEOUT_WORDS = ("超时", "timeout")
_SEND_WORDS = ("发送", "send")


def _split_user_message(messages) -> tuple[dict, str]:
    user = next(m["content"] for m in messages if m["role"] == "user")
    ctx_part = user.split("【工程上下文允许清单】\n", 1)[1].split("\n\n【用户测试需求】\n", 1)
    context = json.loads(ctx_part[0])
    prompt = ctx_part[1].split("\n\n请按系统规则输出信封 JSON。", 1)[0]
    return context, prompt


def _first_in(text: str, haystacks: list[str]) -> str | None:
    for name in haystacks:
        if name in text:
            return name
    return None


def _intent(prompt: str) -> str:
    low = prompt.lower()
    if "故障注入" in prompt:
        return "reserved"
    if "LIN" in prompt or "LDF" in prompt:
        return "out_of_scope"
    if "同时满足" in prompt or "矛盾" in prompt:
        return "conflict"
    if any(w in low for w in _SEND_WORDS):
        return "send"
    if any(w in low for w in _TIMEOUT_WORDS):
        return "timeout"
    if any(w in low for w in _RANGE_WORDS):
        return "range"
    if any(w in low for w in _ENUM_WORDS):
        return "enum"
    if any(w in low for w in _CYCLE_WORDS):
        return "cycle"
    return "none"


class FakeLLMProvider(LLMProvider):
    """合规强假模型：认上下文、认 REQ 出处、拒绝未知/冲突/预留/超范围。"""

    @property
    def provider_id(self) -> str:
        return "fake-llm"

    def list_models(self) -> tuple[str, ...]:
        return ("fake-llm-1",)

    def complete(self, messages, *, model, **kw) -> LLMResult:
        return LLMResult(text="{}", model=model)

    def structured_output(self, messages, *, model, json_schema, **kw) -> LLMResult:
        context, prompt = _split_user_message(messages)
        env = self._respond(context, prompt)
        return LLMResult(
            text=json.dumps(env, ensure_ascii=False),
            model=model,
            usage=LLMUsage(100, 200),
        )

    # -- 决策 -----------------------------------------------------------------

    def _respond(self, context: dict, prompt: str) -> dict:
        kind = _intent(prompt)
        msg_names = [m["name"] for m in context["messages"]]
        sig_names = list(context["signals"])
        req = _REQ.search(prompt)

        def refuse(reason: str) -> dict:
            return {
                "decision": "refuse",
                "refusal_reason": reason,
                "read_from_asset": [f"上下文 database={context['database']}"],
                "inferred_suggestions": [],
                "requires_confirmation": [],
                "ir": None,
            }

        if kind == "reserved":
            return refuse("MVP 预留动作（inject_fault）需专项批准，当前版本不支持自动生成")
        if kind == "out_of_scope":
            return refuse("当前 MVP 不支持 LIN/LDF（属 2.0 阶段），请确认或改用 CAN 测试需求")
        if kind == "conflict":
            return refuse("检测到矛盾阈值：需求同时满足的周期要求不一致（矛盾/冲突），请澄清后重试")
        if kind == "none":
            return refuse("需求不属于 5 类基础测试意图，请重述")
        if kind == "send":
            return self._send_draft(context, prompt, req)
        if kind in ("cycle", "timeout"):
            target = _first_in(prompt, msg_names)
            if target is None:
                return refuse(f"报文不存在于当前工程上下文（未找到，请确认）：{prompt[:24]}")
            return self._assert_draft(context, prompt, kind, target, req)
        # range / enum
        target = _first_in(prompt, sig_names)
        if target is None:
            return refuse(f"信号不存在于当前工程上下文（未找到，请确认）：{prompt[:24]}")
        return self._assert_draft(context, prompt, kind, target, req)

    def _base_ir(self, context: dict, title: str) -> dict:
        digest = hashlib.sha1(f"{context['database']}|{title}".encode("utf-8")).hexdigest()[:8]
        return {
            "schema_version": "1.0",
            "case_id": f"FAKE_{digest.upper()}",
            "title": title,
            "protocol": {"bus": "CAN", "database": context["database"]},
            "steps": [{"id": "S1", "action": "listen_bus", "duration_ms": 1000}],
            "execution_policy": {"mode": "offline_only", "risk_level": "L0",
                                 "requires_approval": False, "max_duration_ms": 15000},
            "unconfirmed_items": ["AI 草案执行前需人工确认参数"],
        }

    def _assert_draft(self, context: dict, prompt: str, kind: str, target: str,
                      req: re.Match | None) -> dict:
        source = req.group(0) if req else "MODEL_SUGGESTED"
        ir = self._base_ir(context, f"{kind}: {target}")
        if kind == "cycle":
            ir["steps"].append({
                "id": "S2", "action": "assert_cycle", "message": target,
                "expected_period_ms": 100, "tolerance_ms": 10, "evidence_source": source,
            })
            ir["target"] = {"message": target}
        elif kind == "timeout":
            ir["steps"].append({
                "id": "S2", "action": "assert_timeout", "message": target,
                "timeout_ms": 300, "evidence_source": source,
            })
            ir["target"] = {"message": target}
        elif kind == "range":
            ir["steps"].append({
                "id": "S2", "action": "assert_range", "signal": target,
                "min": 0, "max": 250, "evidence_source": source,
            })
            ir["target"] = {"signal": target}
        else:  # enum
            ir["steps"].append({
                "id": "S2", "action": "assert_enum", "signal": target,
                "allowed_values": [0, 1, 2, 3], "evidence_source": source,
            })
            ir["target"] = {"signal": target}
        return {
            "decision": "draft",
            "read_from_asset": [f"目标 {target} 来自 {context['database']} 清单"],
            "inferred_suggestions": ["采集时长按默认档推断"],
            "requires_confirmation": ["执行前人工确认阈值与目标"],
            "ir": ir,
        }

    def _send_draft(self, context: dict, prompt: str, req: re.Match | None) -> dict:
        match = _CAN_ID.search(prompt)
        can_id = int(match.group(1), 0) if match else 256
        ir = self._base_ir(context, f"send: 0x{can_id:X}")
        ir["steps"] = [{
            "id": "S1", "action": "send_periodic", "can_id": can_id,
            "payload_hex": "00", "period_ms": 20, "count": 5,
        }]
        ir["execution_policy"] = {"mode": "offline_only", "risk_level": "L1",
                                  "requires_approval": True, "max_duration_ms": 5000}
        ir["unconfirmed_items"] = ["发送参数（帧数据/次数/授权）执行前需人工确认"]
        return {
            "decision": "draft",
            "read_from_asset": [f"目标 can_id=0x{can_id:X}"],
            "inferred_suggestions": ["周期按 20ms 档推断"],
            "requires_confirmation": ["确认发送授权与帧内容"],
            "ir": ir,
        }


@pytest.fixture(scope="module")
def llm_report():
    eval_set = load_eval_set()
    service = GenerationService(FakeLLMProvider())
    return eval_set, evaluate_suite(eval_set, LLMGenerator(service, name="fake_llm"))


class TestLLMEndToEndEval:
    def test_full_suite_passes_with_compliant_llm(self, llm_report):
        """合规生成器（真实管线）→ 12/12：评测期望在 AI-01 下可达。"""
        eval_set, report = llm_report
        failed = {o.case_id: [c.name for c in o.failed_checks] for o in report.outcomes
                  if not o.passed}
        assert failed == {}, failed
        assert len(report.pass_ids) == len(eval_set.cases) == 12

    def test_ai_pipeline_closes_stub_gaps(self, llm_report):
        """桩的 3 个已知缺口在 AI-01 管线下全部转为通过。"""
        _, report = llm_report
        stub_gaps = {"EVAL_N_UNKNOWN_SIGNAL", "EVAL_N_UNKNOWN_MESSAGE", "EVAL_N_CONFLICT"}
        assert stub_gaps <= set(report.pass_ids)

    def test_negatives_refused_not_silently_produced(self, llm_report):
        _, report = llm_report
        by_id = {o.case_id: o for o in report.outcomes}
        for case_id in ("EVAL_N_UNKNOWN_SIGNAL", "EVAL_N_UNKNOWN_MESSAGE",
                        "EVAL_N_FAULT_INJECTION", "EVAL_N_OUT_OF_SCOPE"):
            outcome = by_id[case_id]
            refused = next(c for c in outcome.checks if c.name == "refused")
            assert refused.passed, f"{case_id} 未被拒绝"

    def test_conflict_flagged_path(self, llm_report):
        _, report = llm_report
        outcome = next(o for o in report.outcomes if o.case_id == "EVAL_N_CONFLICT")
        assert outcome.passed
        check = next(c for c in outcome.checks if c.name == "refused_with_reason")
        assert check.passed

    def test_service_gate_backstop_beyond_model_honesty(self, llm_report):
        """引用核验是服务级门：即使模型说了 draft，幻觉引用也会被服务改写为拒绝。

        用同一管线喂“不读上下文的劣化输出”验证（不需要第二个模型实现）。
        """
        from tests.unit.test_ai_generation import ScriptedProvider, envelope, cycle_ir

        sloppy = ScriptedProvider([
            envelope("draft", ir=cycle_ir(message="PhantomECU"), confirm=["确认"])
        ])
        service = GenerationService(sloppy)
        result = service.generate("检查 VehicleSpeed 报文周期",
                                  {"database": "v.dbc",
                                   "messages": [{"name": "VehicleSpeed", "frame_id": 1}],
                                   "signals": ["VehicleSpeed"]})
        assert result.decision == "refuse" and "PhantomECU" in result.refusal_reason

"""AI-01 生成管线测试：确定性后置门（引用核验/来源标注/风险改写/重试/审计）。

所有 LLM 交互使用脚本化假 Provider（实现 LLMProvider 契约），零真实网络。
"""

from __future__ import annotations

import json

import pytest

from zlg_ai_test_engine.ai import GenerationService, build_messages, ENVELOPE_SCHEMA
from zlg_ai_test_engine.ai.generation_service import GenerationResult
from zlg_ai_test_engine.domain.plugins.contracts import (
    LLMError,
    LLMErrorKind,
    LLMProvider,
    LLMResult,
    LLMUsage,
)
from zlg_ai_test_engine.domain.test_ir import validate_executable

CONTEXT = {
    "database": "vehicle.dbc",
    "messages": [
        {"name": "VehicleSpeed", "frame_id": 710},
        {"name": "BMS_Status_1", "frame_id": 1113},
    ],
    "signals": ["VehicleSpeed", "GearState"],
}


def cycle_ir(message: str = "VehicleSpeed", source: str = "REQ_VS_001") -> dict:
    return {
        "schema_version": "1.0",
        "case_id": "AI_TEST_001",
        "title": "周期检查（假模型输出）",
        "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
        "steps": [
            {"id": "S1", "action": "listen_bus", "duration_ms": 1000},
            {"id": "S2", "action": "assert_cycle", "message": message,
             "expected_period_ms": 100, "tolerance_ms": 10, "evidence_source": source},
        ],
        "execution_policy": {"mode": "offline_only", "risk_level": "L0",
                             "requires_approval": False, "max_duration_ms": 5000},
        "unconfirmed_items": [],
    }


def envelope(decision: str, *, ir: dict | None = None, reason: str = "",
             read=(), inferred=(), confirm=()) -> str:
    data = {
        "decision": decision,
        "read_from_asset": list(read),
        "inferred_suggestions": list(inferred),
        "requires_confirmation": list(confirm),
        "ir": ir,
    }
    if decision == "refuse":
        data["refusal_reason"] = reason
    return json.dumps(data, ensure_ascii=False)


class ScriptedProvider(LLMProvider):
    """按脚本顺序返回结果/抛错（第 i 次调用取 items[i]，越界重复最后一项）。"""

    def __init__(self, items: list) -> None:
        self._items = list(items)
        self.calls: list[int] = []

    @property
    def provider_id(self) -> str:
        return "scripted"

    def list_models(self) -> tuple[str, ...]:
        return ("scripted-1",)

    def complete(self, messages, *, model, **kw) -> LLMResult:
        return LLMResult(text="n/a", model=model)

    def structured_output(self, messages, *, model, json_schema, **kw) -> LLMResult:
        idx = min(len(self.calls), len(self._items) - 1)
        self.calls.append(idx)
        item = self._items[idx]
        if isinstance(item, Exception):
            raise item
        return LLMResult(text=item, model=model, usage=LLMUsage(11, 22))


class TestMessageBuilding:
    def test_messages_embed_context_listing_only(self):
        messages = build_messages("检查周期", CONTEXT)
        assert messages[0]["role"] == "system"
        user = messages[1]["content"]
        assert "vehicle.dbc" in user and "VehicleSpeed" in user
        assert "【用户测试需求】" in user
        # 本地优先：只发送名录，不发送任何完整文件内容
        assert "BEGIN DBC" not in user

    def test_envelope_schema_requires_fields(self):
        assert ENVELOPE_SCHEMA["required"] and "ir" in ENVELOPE_SCHEMA["required"]


class TestPostGates:
    def test_valid_draft_passes_and_gets_provenance(self):
        provider = ScriptedProvider([
            envelope("draft", ir=cycle_ir(), read=["报文 VehicleSpeed 来自 vehicle.dbc"],
                     confirm=["执行前需人工确认阈值"])
        ])
        service = GenerationService(provider, snapshot_id="snap123")
        out = service.generate("按需求 REQ_VS_001 检查 VehicleSpeed 报文周期", CONTEXT)
        assert out.decision == "draft" and out.ir is not None
        assert out.ir["provenance"]["model"] == "scripted-1"
        assert out.ir["provenance"]["rule_version"]
        assert out.ir["provenance"]["database_hash"] == "snap123"
        assert out.attempts == 1
        # 草案必有确认项 → 定稿前执行级被阻断
        assert validate_executable(out.ir).ok is False

    def test_out_of_context_reference_refused(self):
        """模型幻觉引用（上下文外 message）→ 服务级拒绝并给出澄清问题。"""
        provider = ScriptedProvider([
            envelope("draft", ir=cycle_ir(message="GhostSpeed"), confirm=["?"])
        ])
        service = GenerationService(provider)
        out = service.generate("检查 VehicleSpeed 报文周期", CONTEXT)
        assert out.decision == "refuse"
        assert "GhostSpeed" in out.refusal_reason
        assert "上下文" in out.refusal_reason
        assert out.clarifying_questions

    def test_source_not_in_prompt_flagged_not_silently_ok(self):
        provider = ScriptedProvider([
            envelope("draft", ir=cycle_ir(source="MODEL_MADE_UP_REQ"),
                     confirm=["阈值来源待确认"])
        ])
        service = GenerationService(provider)
        out = service.generate("检查 VehicleSpeed 报文周期（无需求编号）", CONTEXT)
        assert out.decision == "draft"
        joined = " ".join(out.ir["unconfirmed_items"])
        assert "MODEL_MADE_UP_REQ" in joined and "待人工确认" in joined

    def test_send_risk_rewritten_to_l1_approval(self):
        ir = cycle_ir()
        ir["steps"] = [{"id": "S1", "action": "send_periodic", "can_id": 256,
                        "payload_hex": "00", "period_ms": 20, "count": 3}]
        ir["execution_policy"] = {"mode": "offline_only", "risk_level": "L0",
                                  "requires_approval": False}
        provider = ScriptedProvider([envelope("draft", ir=ir, confirm=["确认发送"])])
        service = GenerationService(provider)
        out = service.generate("以 20ms 周期发送 0x100 报文 3 次", CONTEXT)
        assert out.decision == "draft"
        policy = out.ir["execution_policy"]
        assert policy["risk_level"] == "L1" and policy["requires_approval"] is True

    def test_model_refusal_passthrough(self):
        provider = ScriptedProvider([envelope("refuse", reason="MVP 不支持 LIN 调度表测试")])
        service = GenerationService(provider)
        out = service.generate("测试 LIN 调度表", CONTEXT)
        assert out.decision == "refuse"
        assert "LIN" in out.refusal_reason


class TestRetryBehavior:
    def test_invalid_envelope_json_repaired_then_pass(self):
        provider = ScriptedProvider(["这不是 JSON", envelope("draft", ir=cycle_ir(),
                                                              confirm=["确认"])])
        service = GenerationService(provider)
        out = service.generate("检查周期", CONTEXT)
        assert out.decision == "draft"
        assert out.attempts == 2
        assert len(provider.calls) == 2

    def test_invalid_structure_repaired_with_feedback(self):
        bad = envelope("draft", ir={"schema_version": "1.0"})  # 缺必填字段
        provider = ScriptedProvider([bad, envelope("draft", ir=cycle_ir(), confirm=["确认"])])
        service = GenerationService(provider)
        out = service.generate("检查周期", CONTEXT)
        assert out.decision == "draft" and out.attempts == 2

    def test_exhausted_retries_returns_refusal(self):
        provider = ScriptedProvider(["still not json"])
        service = GenerationService(provider)
        out = service.generate("检查周期", CONTEXT)
        assert out.decision == "refuse"
        assert out.attempts == 3
        assert "未通过" in out.refusal_reason

    def test_auth_error_propagates(self):
        provider = ScriptedProvider([LLMError(LLMErrorKind.AUTH, "401 invalid key")])
        service = GenerationService(provider)
        with pytest.raises(LLMError) as exc:
            service.generate("检查周期", CONTEXT)
        assert exc.value.kind is LLMErrorKind.AUTH


class TestAudit:
    def test_model_invocation_recorded_without_full_context(self, tmp_path):
        from zlg_ai_test_engine.application.audit_service import AuditService  # noqa: F401
        from zlg_ai_test_engine.infrastructure.persistence import (
            create_engine_and_session,
            init_db,
        )
        from zlg_ai_test_engine.infrastructure.persistence.models import ModelInvocation

        engine, factory = create_engine_and_session(tmp_path / "ai_audit.db")
        init_db(engine)
        provider = ScriptedProvider([envelope("draft", ir=cycle_ir(), confirm=["确认"])])
        service = GenerationService(provider, model="scripted-1",
                                    audit_session_factory=factory)
        out = service.generate("按需求 REQ_VS_001 检查 VehicleSpeed 报文周期", CONTEXT)
        assert out.invocation_id

        with factory() as session:
            row = session.get(ModelInvocation, out.invocation_id)
            assert row is not None
            assert row.provider == "scripted" and row.model == "scripted-1"
            assert row.prompt_version and row.output_hash
            refs_text = json.dumps(row.input_refs_json, ensure_ascii=False)
            # 只记引用：提示哈希 + database + 名录，不含密钥与完整工程
            assert "prompt_sha256" in refs_text
            assert "vehicle.dbc" in refs_text
        engine.dispose()

    def test_no_session_factory_still_works(self):
        provider = ScriptedProvider([envelope("draft", ir=cycle_ir(), confirm=["确认"])])
        service = GenerationService(provider)
        out = service.generate("检查周期", CONTEXT)
        assert out.produced and out.invocation_id  # id 照常生成，仅不落库

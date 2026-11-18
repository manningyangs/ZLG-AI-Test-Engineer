"""AI-01：需求 → Test IR 生成编排与确定性后置门。

流程：
    build_messages → provider.structured_output（信封 Schema；INVALID_OUTPUT 有限重试）
    → 确定性复核门（不信任模型自觉）：
       1. 结构：ir 必须过 validate_ir（draft 级），否则按无效输出重试/拒绝；
       2. **引用核验**：ir 引用的 message/signal 必须 ∈ 上下文清单，缺失 → 转拒绝
          （附澄清问题，绝不放行）；
       3. **来源标注**：assert 步骤 evidence_source 不在用户原文且未被模型列入确认项
          → 自动追加待确认（草案级可留值，执行级必被 validate_executable 阻断）；
       4. **草案必确认**：unconfirmed_items 至少 1 条（AI 草案不得直接可执行）；
       5. 风险等级：send_* 强制 L1 + requires_approval（模型谎报会被改写）；
    → Provenance（prompt 版本/哈希/模型/快照）+ ModelInvocation 审计（外发引用最小化）。

失败路径全部可观测：结构错误带最后一次诊断拒绝；LLMError 分类上抛（AUTH 等不可重试）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from zlg_ai_test_engine.ai.prompt_builder import ENVELOPE_SCHEMA, PROMPT_VERSION, build_messages
from zlg_ai_test_engine.domain.plugins.contracts import LLMError, LLMErrorKind, LLMProvider
from zlg_ai_test_engine.domain.test_ir import validate_ir

#: 结构性复核失败的最大重试次数（在 provider 自身重试之外）
_MAX_REPAIR_ATTEMPTS = 3

_REQ_TOKEN = re.compile(r"\bREQ_[A-Za-z0-9_]+")


@dataclass
class GenerationResult:
    """生成结果（供 UI 三态展示与评测适配）。"""

    decision: str  # "draft" | "refuse"
    refusal_reason: str = ""
    read_items: list[str] = field(default_factory=list)
    inferred_items: list[str] = field(default_factory=list)
    confirmation_items: list[str] = field(default_factory=list)
    clarifying_questions: list[str] = field(default_factory=list)
    ir: dict[str, Any] | None = None
    attempts: int = 1
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    invocation_id: str = ""

    @property
    def produced(self) -> bool:
        return self.decision == "draft" and isinstance(self.ir, dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _collect_refs(ir: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for step in ir.get("steps", []) or []:
        for key in ("message", "signal"):
            if step.get(key):
                refs.add(str(step[key]))
    target = ir.get("target") or {}
    for key in ("message", "signal"):
        if target.get(key):
            refs.add(str(target[key]))
    return refs


class GenerationService:
    """AI-01 生成服务：LLM 调用 + 确定性后置门 + 审计。"""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str | None = None,
        snapshot_id: str | None = None,
        audit_session_factory: Callable[[], Any] | None = None,
        clock: Callable[[], str] = _now_iso,
    ) -> None:
        self._provider = provider
        self._model = model or (provider.list_models()[0] if provider.list_models() else "")
        self._snapshot_id = snapshot_id
        self._audit_session_factory = audit_session_factory
        self._clock = clock

    # -- 入口 ---------------------------------------------------------------

    def generate(self, prompt: str, context: Mapping[str, Any]) -> GenerationResult:
        messages = build_messages(prompt, context)
        allowed_messages = {str(m.get("name")) for m in (context.get("messages") or [])
                            if isinstance(m, Mapping)} - {None}
        allowed_signals = {str(s) for s in (context.get("signals") or [])}

        envelope: dict[str, Any] | None = None
        last_error = ""
        attempts = 0
        repair_messages = list(messages)
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            attempts = attempt
            try:
                result = self._provider.structured_output(
                    repair_messages,
                    model=self._model,
                    json_schema=ENVELOPE_SCHEMA,
                )
            except LLMError as exc:
                if exc.kind is LLMErrorKind.INVALID_OUTPUT and attempt < _MAX_REPAIR_ATTEMPTS:
                    repair_messages = messages + [
                        {
                            "role": "user",
                            "content": (
                                f"上一次输出未通过校验：{exc}。"
                                "请仅重新输出符合信封 Schema 的 JSON。"
                            ),
                        }
                    ]
                    last_error = str(exc)
                    continue
                raise  # AUTH/TIMEOUT 等由分类决定，不在此吞掉

            candidate = self._parse_envelope(result.text)
            if candidate is None:
                last_error = "信封 JSON 解析失败"
                if attempt < _MAX_REPAIR_ATTEMPTS:
                    repair_messages = messages + [
                        {"role": "user", "content": "输出不是合法 JSON，请仅重新输出信封 JSON。"}
                    ]
                    continue
                break

            outcome = self._post_gates(
                candidate,
                prompt=prompt,
                allowed_messages=allowed_messages,
                allowed_signals=allowed_signals,
                context=context,
            )
            if outcome is not None:
                outcome.attempts = attempts
                outcome.model = self._model
                self._audit(outcome, prompt, context, result_text=result.text,
                            tokens=(result.usage.prompt_tokens, result.usage.completion_tokens))
                return outcome
            # 结构门未过 → 携带诊断重试
            last_error = self._last_gate_error or "结构校验失败"
            if attempt < _MAX_REPAIR_ATTEMPTS:
                repair_messages = messages + [
                    {"role": "user", "content": f"输出未通过 Test IR 结构校验：{last_error}。"
                                                "请仅重新输出修正后的信封 JSON。"}
                ]

        refusal = GenerationResult(
            decision="refuse",
            refusal_reason=f"模型输出经 {attempts} 次校验仍未通过：{last_error}",
            read_items=[],
            inferred_items=[],
            confirmation_items=[],
            clarifying_questions=["模型输出无效，请重述需求或稍后重试"],
        )
        refusal.attempts = attempts
        refusal.model = self._model
        self._audit(refusal, prompt, context, result_text=last_error, tokens=(0, 0))
        return refusal

    # -- 内部 ---------------------------------------------------------------

    _last_gate_error: str = ""

    @staticmethod
    def _parse_envelope(text: str) -> dict[str, Any] | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", cleaned).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _post_gates(
        self,
        envelope: Mapping[str, Any],
        *,
        prompt: str,
        allowed_messages: set[str],
        allowed_signals: set[str],
        context: Mapping[str, Any],
    ) -> GenerationResult | None:
        """确定性后置门；返回 None 表示结构不合格（调用方重试）。"""
        self._last_gate_error = ""
        decision = envelope.get("decision")
        read_items = [str(x) for x in envelope.get("read_from_asset", [])]
        inferred_items = [str(x) for x in envelope.get("inferred_suggestions", [])]
        confirmation_items = [str(x) for x in envelope.get("requires_confirmation", [])]

        if decision == "refuse":
            reason = str(envelope.get("refusal_reason") or "模型拒绝")
            return GenerationResult(
                decision="refuse",
                refusal_reason=reason,
                read_items=read_items,
                inferred_items=inferred_items,
                confirmation_items=confirmation_items,
                clarifying_questions=[reason] if reason else [],
            )

        ir = envelope.get("ir")
        if not isinstance(ir, dict):
            self._last_gate_error = "decision=draft 但缺少 ir 对象"
            return None

        # 门 1：draft 级结构校验
        struct = validate_ir(ir)
        if not struct.ok:
            self._last_gate_error = "；".join(d.message for d in struct.errors[:3])
            return None

        # 门 2：引用核验（上下文外引用 → 拒绝并澄清，绝不放行）
        missing = sorted(
            ref
            for ref in _collect_refs(ir)
            if ref not in allowed_messages and ref not in allowed_signals
        )
        if missing:
            question = f"以下引用不存在于当前工程上下文：{'、'.join(missing)}，请确认目标或导入正确 DBC"
            return GenerationResult(
                decision="refuse",
                refusal_reason=question,
                read_items=read_items,
                inferred_items=inferred_items,
                confirmation_items=confirmation_items,
                clarifying_questions=[question],
            )

        # 门 3：来源可追溯性（不在原文 → 转待确认，草案可留值、执行必被阻断）
        unconfirmed: list[str] = list(ir.get("unconfirmed_items") or [])
        for step in ir.get("steps", []):
            action = str(step.get("action", ""))
            if not action.startswith("assert_"):
                continue
            source = str(step.get("evidence_source") or "")
            if source and source not in prompt and not _mentioned(source, unconfirmed + confirmation_items):
                item = f"步骤 {step.get('id')} 阈值来源 {source!r} 无法追溯到需求原文，待人工确认"
                unconfirmed.append(item)
                confirmation_items.append(item)

        # 门 4：草案必有人工确认项（AI 草案不得直接可执行）
        if not unconfirmed:
            unconfirmed.append("AI 草案参数（阈值/时长/目标）执行前需人工逐项确认")
        if not confirmation_items:
            confirmation_items.append("AI 草案执行前需人工逐项确认")

        # 门 5：发送类强制 L1 + 审批（模型谎报会被改写）
        policy = dict(ir.get("execution_policy") or {})
        actions = [str(s.get("action")) for s in ir.get("steps", [])]
        if any(a.startswith("send_") for a in actions):
            policy["risk_level"] = "L1"
            policy["requires_approval"] = True
        elif policy.get("risk_level") in (None, ""):
            policy["risk_level"] = "L0"
        if "max_duration_ms" not in policy:
            policy["max_duration_ms"] = 15000
        policy.setdefault("mode", "offline_only")
        ir["execution_policy"] = policy

        # Provenance（模型/提示版本/哈希/生成时刻；不含密钥与完整工程内容）
        provenance = dict(ir.get("provenance") or {})
        provenance.update(
            {
                "model": self._model,
                "rule_version": PROMPT_VERSION,
                "generated_at": self._clock(),
            }
        )
        if self._snapshot_id:
            provenance["database_hash"] = self._snapshot_id
        ir["provenance"] = provenance
        ir["unconfirmed_items"] = _dedupe(unconfirmed)
        if not str(ir.get("case_id", "")):
            ir["case_id"] = f"AI_{uuid.uuid4().hex[:10].upper()}"

        return GenerationResult(
            decision="draft",
            refusal_reason="",
            read_items=read_items,
            inferred_items=inferred_items,
            confirmation_items=_dedupe(confirmation_items),
            clarifying_questions=[c for c in confirmation_items if "?" in c or "？" in c],
            ir=ir,
        )

    def _audit(
        self,
        outcome: GenerationResult,
        prompt: str,
        context: Mapping[str, Any],
        *,
        result_text: str,
        tokens: tuple[int, int],
    ) -> None:
        """写 ModelInvocation：只记引用（database/清单名），不记完整工程与密钥。"""
        outcome.invocation_id = str(uuid.uuid4())
        if self._audit_session_factory is None:
            return
        from zlg_ai_test_engine.infrastructure.persistence.models import ModelInvocation

        input_refs = [
            {"kind": "prompt_sha256", "value": _sha256_text(prompt)},
            {"kind": "database", "value": context.get("database")},
            {
                "kind": "context_names",
                "value": sorted(
                    [str(m.get("name")) for m in (context.get("messages") or [])
                     if isinstance(m, Mapping)]
                    + [str(s) for s in (context.get("signals") or [])]
                ),
            },
        ]
        try:
            with self._audit_session_factory() as session, session.begin():
                session.add(
                    ModelInvocation(
                        invocation_id=outcome.invocation_id,
                        provider=self._provider.provider_id,
                        model=self._model,
                        prompt_version=PROMPT_VERSION,
                        input_refs_json=input_refs,
                        output_hash=_sha256_text(result_text),
                        tokens_prompt=tokens[0],
                        tokens_completion=tokens[1],
                    )
                )
        except Exception:  # noqa: BLE001 — 审计失败不阻断生成（有日志更佳，见调用方）
            pass


def _mentioned(token: str, haystacks: list[str]) -> bool:
    joined = " ".join(haystacks)
    return token in joined


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

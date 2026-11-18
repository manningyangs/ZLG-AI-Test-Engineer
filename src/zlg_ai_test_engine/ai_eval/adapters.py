"""生成器适配器：把具体实现接入统一评测协议。"""

from __future__ import annotations

from typing import Any, Mapping

from zlg_ai_test_engine.ai_eval.evaluator import GenerationOutput


class StubGenerator:
    """把 UI 离线桩（ui.generation_stub）适配为评测生成器。

    语义：
    - ``detect_kind`` 无匹配 → 拒绝（produced=False，带重述建议）；
    - 有匹配 → 返回草案 IR（含待确认项）+ 以默认值确认得到的定稿 IR
      （供 confirmed_executable 检查）。
    惰性导入 ui 层，ai_eval 不在模块级依赖 PySide6。
    """

    name = "offline_stub"

    def __call__(self, prompt: str, context: Mapping[str, Any]) -> GenerationOutput:
        from zlg_ai_test_engine.ui.generation_stub import build_confirmed_ir, generate_draft

        draft = generate_draft(prompt)
        if draft.kind == "none" or not draft.base_ir:
            return GenerationOutput(produced=False, hint=draft.hint)
        confirmed = build_confirmed_ir(draft, {})
        return GenerationOutput(
            produced=True,
            ir=draft.base_ir,
            ir_confirmed=confirmed,
            unconfirmed=list(draft.base_ir.get("unconfirmed_items", [])),
            inferred=list(draft.inferred_items),
            hint=draft.hint,
        )


class RefusingGenerator:
    """永远拒绝的生成器（用于验证评测器对 must_produce=no/yes 的判定方向）。

    拒绝说明刻意覆盖常见关键词（不存在/上下文/请确认/不支持/矛盾/批准/预留/MVP），
    使“拒绝方向”的用例可全量通过——它只用于评测器自检，不代表真实语义。
    """

    name = "always_refuse"
    REFUSAL_HINT = (
        "已拒绝：所请求的信号或报文不存在于当前工程上下文（未找到，请确认），"
        "当前 MVP 不支持该功能；如存在矛盾或冲突阈值请澄清后重试，"
        "预留动作（故障注入等）需专项批准。"
    )

    def __call__(self, prompt: str, context: Mapping[str, Any]) -> GenerationOutput:
        return GenerationOutput(produced=False, hint=self.REFUSAL_HINT)


class LLMGenerator:
    """把 AI-01 GenerationService（真实 LLM 管线）接入评测协议。

    - 决策为 refuse → produced=False，hint=拒绝原因（含澄清问题语义）；
    - 决策为 draft → ir + 定稿副本（清空 unconfirmed_items，等价“人工确认后”）
      供 confirmed_executable 检查。
    """

    def __init__(self, service: Any, *, name: str | None = None) -> None:
        self._service = service
        self.name = name or "llm_service"

    def __call__(self, prompt: str, context: Mapping[str, Any]) -> GenerationOutput:
        result = self._service.generate(prompt, context)
        if not result.produced:
            return GenerationOutput(
                produced=False,
                hint=result.refusal_reason,
                unconfirmed=list(result.confirmation_items),
                inferred=list(result.inferred_items),
            )
        ir = dict(result.ir or {})
        confirmed = dict(ir)
        confirmed["unconfirmed_items"] = []
        return GenerationOutput(
            produced=True,
            ir=ir,
            ir_confirmed=confirmed,
            unconfirmed=list(ir.get("unconfirmed_items", [])),
            inferred=list(result.inferred_items),
            hint=result.refusal_reason,
        )

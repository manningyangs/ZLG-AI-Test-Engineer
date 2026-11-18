"""P2 离线评测集框架（AI-01 固定提示集的载体）。

- ``cases``：评测用例加载与结构校验（examples/ai_eval/eval_set.json）；
- ``evaluator``：生成器无关的检查器与报告；
- ``adapters``：把具体生成器（离线桩 / 未来的 LLMProvider）适配到统一协议。

评测期望是**真值**，不随生成器改变；接入真实 LLM 后复跑同一集合即可对比基线。
"""

from zlg_ai_test_engine.ai_eval.cases import (
    EvalCase,
    EvalSet,
    Expectation,
    PromptTarget,
    load_eval_set,
    resolve_default_set_path,
)
from zlg_ai_test_engine.ai_eval.evaluator import (
    CaseOutcome,
    CheckResult,
    EvalReport,
    GenerationOutput,
    evaluate_case,
    evaluate_suite,
)

__all__ = [
    "EvalCase",
    "EvalSet",
    "Expectation",
    "PromptTarget",
    "load_eval_set",
    "resolve_default_set_path",
    "CaseOutcome",
    "CheckResult",
    "EvalReport",
    "GenerationOutput",
    "evaluate_case",
    "evaluate_suite",
]

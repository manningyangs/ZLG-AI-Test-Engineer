"""AI-01/02：需求 → Test IR 生成管线与工程上下文管理。

- ``prompt_builder``：提示模板与模型输出信封 Schema（AI-01）
- ``generation_service``：生成编排 + **确定性后置门**（引用核验/来源标注/Schema 校验重试）
- ``context_snapshot``：工程上下文快照、知识源元数据与外发过滤（AI-02）

红线：AI 只产出草稿与澄清问题；所有引用必须可核验，门禁失败即拒绝或转待确认，
不允许模型把未核验内容带入可执行状态。
"""

from zlg_ai_test_engine.ai.context_snapshot import (
    ContextSnapshot,
    KnowledgeEntry,
    build_snapshot,
    snapshot_allows_upload,
)
from zlg_ai_test_engine.ai.generation_service import GenerationResult, GenerationService
from zlg_ai_test_engine.ai.prompt_builder import ENVELOPE_SCHEMA, PROMPT_VERSION, build_messages

__all__ = [
    "ContextSnapshot",
    "KnowledgeEntry",
    "build_snapshot",
    "snapshot_allows_upload",
    "GenerationResult",
    "GenerationService",
    "ENVELOPE_SCHEMA",
    "PROMPT_VERSION",
    "build_messages",
]

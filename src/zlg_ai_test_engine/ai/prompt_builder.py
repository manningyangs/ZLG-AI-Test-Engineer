"""AI-01：提示模板与模型输出信封 Schema。

设计要点：
- 模型**只输出信封 JSON**（decision + 三组标注 + ir 草稿），不输出代码；
- 上下文以"允许清单"形式给出（database/messages/signals），引用只能来自清单；
- 信封 Schema 同时用于 provider.structured_output 的 response_format 约束与
  服务端复核（不信任模型自觉）。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

#: 提示版本（写入 Provenance 与 ModelInvocation，变更须递增并留评测记录）
PROMPT_VERSION = "ai01-prompt-v1"

_SYSTEM_PROMPT = """\
你是汽车总线测试工程助手，把用户的中文测试需求转换为受约束的 Test IR 草案（JSON）。

硬性规则（违反即无效）：
1. 只输出一个 JSON 对象（信封），不要输出代码、解释或 markdown 围栏。
2. decision="draft" 仅当需求属于 5 类基础意图：报文周期 / 信号范围 / 枚举值 / 报文超时 /
   受控发送；其余（预留动作、未支持协议、含糊不清、目标不存在）一律 decision="refuse"
   并在 refusal_reason 说明原因。
3. ir 中引用的 message/signal 必须**逐字**出现在用户给出的上下文清单中；不在清单中 → refuse。
4. 阈值与容差必须能追溯到用户原文（如 REQ_* 编号、明确数值）；无法追溯 → 数值放入草案
   但把该参数写入 requires_confirmation（不得伪称已确认）。
5. 每个 assert_* 步骤必须有 evidence_source；来源优先取用户原文出处。
6. ir.unconfirmed_items 至少 1 条（AI 草案必须经人工确认才可执行）。
7. 风险等级：发送类 risk_level="L1" 且 requires_approval=true；只读类 "L0"。
8. 禁止出现 inject_fault、uds_request 等 MVP 预留动作；禁止任何代码/脚本字段。

信封结构：
{
  "decision": "draft" | "refuse",
  "refusal_reason": "仅 refuse 时给出",
  "read_from_asset": ["已从工程上下文读取的事实..."],
  "inferred_suggestions": ["模型推断建议..."],
  "requires_confirmation": ["需要人工确认的问题..."],
  "ir": { ...Test IR 草案... } | null
}
Test IR 草案字段遵循 schema_version="1.0"（含 case_id/title/protocol/steps/
execution_policy/unconfirmed_items/provenance）。
"""

#: 模型输出信封的 JSON Schema（Draft 2020-12）
ENVELOPE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ai01_envelope",
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "read_from_asset", "inferred_suggestions",
                 "requires_confirmation", "ir"],
    "properties": {
        "decision": {"enum": ["draft", "refuse"]},
        "refusal_reason": {"type": "string"},
        "read_from_asset": {"type": "array", "items": {"type": "string"}},
        "inferred_suggestions": {"type": "array", "items": {"type": "string"}},
        "requires_confirmation": {"type": "array", "items": {"type": "string"}},
        "ir": {
            "description": "decision=draft 时为 Test IR 草案（结构由宿主侧 validate_ir 复核）",
            "type": ["object", "null"],
        },
    },
    "allOf": [
        {
            "if": {"properties": {"decision": {"const": "refuse"}}},
            "then": {"required": ["refusal_reason"]},
        }
    ],
}


def build_messages(prompt: str, context: Mapping[str, Any]) -> list[dict[str, str]]:
    """构造 messages：系统规则 + 用户需求与上下文允许清单。

    只发送清单化的上下文（database/messages/signals 名录），**不上传完整 DBC/
    原始工程内容**（AI-02 本地优先策略）。
    """
    context_view = {
        "database": context.get("database"),
        "messages": [
            {"name": m.get("name"), "frame_id": m.get("frame_id")}
            if isinstance(m, Mapping)
            else m
            for m in (context.get("messages") or [])
        ],
        "signals": list(context.get("signals") or []),
    }
    user = (
        f"【工程上下文允许清单】\n{json.dumps(context_view, ensure_ascii=False)}\n\n"
        f"【用户测试需求】\n{prompt}\n\n"
        "请按系统规则输出信封 JSON。"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]

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
9. action 名**只能**是这些（不得自造，如不得用 assert_sign/assert_signal_range）：
   listen_bus, send_frame, send_periodic, monitor_signal, wait_for_frame,
   assert_cycle, assert_range, assert_enum, assert_timeout。
   decision=draft 时**必须**包含与意图对应的断言步骤（周期→assert_cycle、范围→assert_range、
   枚举→assert_enum、超时→assert_timeout），不得只给 listen 步骤。
10. 发送意图：优先用上下文清单中的报文名（message_ref）；只给 CAN ID 时，按清单 frame_id
    匹配（支持 0x 十六进制），payload 未给出则填占位 "00" 并把载荷列入 unconfirmed_items。
11. **各步骤字段表**（只能用这些键，字段名不得改写）：
    - listen_bus: duration_ms
    - monitor_signal: message, signal, duration_ms
    - wait_for_frame: message 或 can_id, timeout_ms
    - assert_cycle: message, expected_period_ms, tolerance_ms, evidence_source
    - assert_range: signal, min, max, unit?(可选), tolerance?(可选), evidence_source
    - assert_enum: signal, allowed_values, evidence_source
    - assert_timeout: message, **timeout_ms**（不是 expected_timeout_ms）, evidence_source

信封结构：
{
  "decision": "draft" | "refuse",
  "refusal_reason": "仅 refuse 时给出",
  "read_from_asset": ["已从工程上下文读取的事实..."],
  "inferred_suggestions": ["模型推断建议..."],
  "requires_confirmation": ["需要人工确认的问题..."],
  "ir": { ...Test IR 草案... } | null
}

ir 草案**最小合法示例**（字段形态必须与此一致，不要自创字段/不要输出 provenance，服务端会自动写入）：
{"schema_version":"1.0","case_id":"EX_001","title":"示例","protocol":{"bus":"CAN","database":"vehicle.dbc"},"target":{"message":"VehicleSpeed"},"steps":[{"id":"S1","action":"listen_bus","duration_ms":1000},{"id":"S2","action":"assert_cycle","message":"VehicleSpeed","expected_period_ms":100,"tolerance_ms":10,"evidence_source":"REQ_X"}],"execution_policy":{"mode":"offline_only","risk_level":"L0","requires_approval":false,"max_duration_ms":15000},"expected_result":"断言通过并保留原始帧证据","unconfirmed_items":["阈值待人工确认"]}

ir 结构硬约束：
- protocol 必须是对象 {"bus":"CAN|CAN_FD","database":"<清单中的库名>"}，不能是字符串；
- execution_policy 只允许键：mode（只能 "offline_only"|"offline_then_hardware"|"hardware"）、
  risk_level、requires_approval、max_duration_ms、max_frames、target_adapter、target_capabilities；
  发送类必须 risk_level="L1" 且 requires_approval=true，只读类 "L0"；
- 步骤字段按动作类型给全（如 assert_cycle 需 message/expected_period_ms/tolerance_ms/evidence_source）；
- 发送类步骤示例：{"id":"S1","action":"send_periodic","can_id":256,"payload_hex":"00","period_ms":20,"count":5}；
- 不要输出 provenance、不要输出任何示例中没有的键。
"""

#: 模型输出信封的 JSON Schema（Draft 2020-12）
#: 可空字段宽容（真实模型常输出 null）：解析层归一化为 ""/[]，语义门不变
ENVELOPE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ai01_envelope",
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "read_from_asset", "inferred_suggestions",
                 "requires_confirmation", "ir"],
    "properties": {
        "decision": {"enum": ["draft", "refuse"]},
        "refusal_reason": {"type": ["string", "null"]},
        "read_from_asset": {"type": ["array", "null"], "items": {"type": "string"}},
        "inferred_suggestions": {"type": ["array", "null"], "items": {"type": "string"}},
        "requires_confirmation": {"type": ["array", "null"], "items": {"type": "string"}},
        "ir": {
            "description": "decision=draft 时为 Test IR 草案（结构由宿主侧 validate_ir 复核）",
            "type": ["object", "null"],
        },
    },
    "allOf": [
        {
            "if": {"properties": {"decision": {"const": "refuse"}},
                   "required": ["decision"]},
            "then": {"properties": {"refusal_reason": {"type": ["string", "null"]}}},
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

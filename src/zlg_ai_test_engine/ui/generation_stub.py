"""UI-01：离线生成桩（AI-01 未接入时的占位实现）。

按需求文本关键词匹配五类用例模式（周期 / 范围 / 枚举 / 超时 / 发送），
产出固定三组结果（已从工程读取 / 推断建议 / 待确认）与一份 draft 级合法的
Test IR 骨架；全部待确认项确认后经 ``build_confirmed_ir`` 定稿，
再由页面用 ``validate_ir`` 静态校验后送入用例编辑器。

红线：纯本地字符串匹配，不调用任何模型、不访问网络、不触碰设备。
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from typing import Any, Mapping

#: 关键词匹配表（顺序即优先级：更具体的模式先匹配）
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # “周期发送”同时含“周期”与“发送”，发送模式须先于周期匹配
    ("send", ("发送", "send", "tx")),
    ("timeout", ("超时", "timeout", "未收到", "报文丢失")),
    ("cycle", ("周期", "cycle", "间隔", "period")),
    ("range", ("范围", "range", "上下限", "区间", "超出")),
    ("enum", ("枚举", "enum", "取值", "状态值")),
)

#: 无匹配时的重述建议
NO_MATCH_HINT = (
    "未匹配到已知用例模式，请重述需求。"
    "支持的关键词：周期/cycle、范围/range、枚举、超时/timeout、发送。"
)

#: case_id 生成计数器（保证多次生成的 case_id 不重复）
_case_counter = itertools.count(1)


@dataclass(frozen=True)
class PendingItem:
    """一项“待确认”参数：全部勾选确认后才允许生成 Test IR 草案。"""

    key: str  # 确认值字典中的键（页面展示无需关心）
    step_id: str  # 目标步骤 id
    field: str  # 写入步骤的字段名
    kind: str  # 值类型："str" | "int" | "float" | "int_list"
    value: str  # 默认值（勾选即采用）
    label: str  # 展示文案


@dataclass
class GenerationDraft:
    """离线桩产出：三组结果 + Test IR 骨架。"""

    kind: str  # cycle/range/enum/timeout/send/none
    title: str
    read_items: list[str] = field(default_factory=list)
    inferred_items: list[str] = field(default_factory=list)
    pending_items: list[PendingItem] = field(default_factory=list)
    base_ir: dict[str, Any] = field(default_factory=dict)
    hint: str = ""  # kind == "none" 时的重述建议


def detect_kind(text: str) -> str:
    """按关键词优先级识别需求模式；无匹配返回 ``"none"``。"""
    lowered = text.lower()
    for kind, words in _KEYWORDS:
        if any(word in lowered for word in words):
            return kind
    return "none"


def _coerce(kind: str, raw: str) -> Any:
    """把确认文本按声明类型转成 IR 字段值。"""
    text = str(raw).strip()
    if kind == "int":
        return int(float(text))
    if kind == "float":
        return float(text)
    if kind == "int_list":
        return [int(float(part)) for part in text.replace("，", ",").split(",") if part.strip()]
    return text


def _base_ir(
    case_id: str,
    title: str,
    steps: list[dict[str, Any]],
    *,
    risk_level: str = "L0",
    requires_approval: bool = False,
    target: dict[str, Any] | None = None,
    expected_result: str = "",
    unconfirmed: list[str],
) -> dict[str, Any]:
    """构造 draft 级 Test IR 骨架（占位 DBC：vehicle.dbc）。"""
    document: dict[str, Any] = {
        "schema_version": "1.0",
        "case_id": case_id,
        "title": title,
        "priority": "P2",
        "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
        "steps": steps,
        "execution_policy": {
            "mode": "offline_only",
            "risk_level": risk_level,
            "requires_approval": requires_approval,
            "max_duration_ms": 15000,
        },
        "expected_result": expected_result,
        "provenance": {"requirement_refs": []},
        "unconfirmed_items": list(unconfirmed),
    }
    if target:
        document["target"] = dict(target)
    return document


def _next_case_id(kind: str) -> str:
    return f"GEN_{kind.upper()}_{next(_case_counter)}"


# --------------------------------------------------------------------------- 五类草案


def _draft_cycle() -> GenerationDraft:
    pending = [
        PendingItem(
            key="evidence_source",
            step_id="S2",
            field="evidence_source",
            kind="str",
            value="REQ_GEN_CYCLE",
            label="阈值来源未指定：勾选表示采用默认需求编号 REQ_GEN_CYCLE",
        ),
        PendingItem(
            key="expected_period_ms",
            step_id="S2",
            field="expected_period_ms",
            kind="int",
            value="100",
            label="期望周期 100ms 未经需求确认：勾选表示确认该阈值",
        ),
    ]
    ir = _base_ir(
        _next_case_id("cycle"),
        "检查报文周期（离线桩草案）",
        [
            {"id": "S1", "action": "listen_bus", "duration_ms": 1000},
            {
                "id": "S2",
                "action": "assert_cycle",
                "message": "VehicleSpeed",
                "expected_period_ms": 100,
                "tolerance_ms": 10,
                "evidence_source": "REQ_GEN_CYCLE",
            },
        ],
        target={"message": "VehicleSpeed"},
        expected_result="报文到达周期在 100ms±10ms 内，保留原始帧证据",
        unconfirmed=[p.label for p in pending],
    )
    return GenerationDraft(
        kind="cycle",
        title=ir["title"],
        read_items=[
            "协议总线：CAN（工程上下文默认）",
            "DBC 数据库：vehicle.dbc（样例工程）",
            "目标报文：VehicleSpeed（样例 DBC 占位）",
        ],
        inferred_items=[
            "推断期望周期：100 ms（样例需求默认档）",
            "推断容差：±10 ms（默认 10% 档）",
            "采集时长：1000 ms（覆盖 ≥10 个周期）",
        ],
        pending_items=pending,
        base_ir=ir,
    )


def _draft_range() -> GenerationDraft:
    pending = [
        PendingItem(
            key="evidence_source",
            step_id="S2",
            field="evidence_source",
            kind="str",
            value="REQ_GEN_RANGE",
            label="阈值来源未指定：勾选表示采用默认需求编号 REQ_GEN_RANGE",
        ),
        PendingItem(
            key="max",
            step_id="S2",
            field="max",
            kind="float",
            value="250.0",
            label="上限 250.0 km/h 未经需求确认：勾选表示确认该阈值",
        ),
    ]
    ir = _base_ir(
        _next_case_id("range"),
        "信号范围检查（离线桩草案）",
        [
            {"id": "S1", "action": "listen_bus", "duration_ms": 1000},
            {
                "id": "S2",
                "action": "assert_range",
                "signal": "VehicleSpeed",
                "min": 0.0,
                "max": 250.0,
                "unit": "km/h",
                "evidence_source": "REQ_GEN_RANGE",
            },
        ],
        target={"signal": "VehicleSpeed"},
        expected_result="信号物理值落在 0.0~250.0 km/h 内",
        unconfirmed=[p.label for p in pending],
    )
    return GenerationDraft(
        kind="range",
        title=ir["title"],
        read_items=[
            "DBC 数据库：vehicle.dbc（样例工程）",
            "目标信号：VehicleSpeed（样例 DBC 占位）",
        ],
        inferred_items=[
            "推断合法范围：0.0 ~ 250.0 km/h",
            "采集时长：1000 ms",
        ],
        pending_items=pending,
        base_ir=ir,
    )


def _draft_enum() -> GenerationDraft:
    pending = [
        PendingItem(
            key="evidence_source",
            step_id="S2",
            field="evidence_source",
            kind="str",
            value="REQ_GEN_ENUM",
            label="阈值来源未指定：勾选表示采用默认需求编号 REQ_GEN_ENUM",
        ),
        PendingItem(
            key="allowed_values",
            step_id="S2",
            field="allowed_values",
            kind="int_list",
            value="0,1,2,3",
            label="允许取值 0/1/2/3 未经需求确认：勾选表示确认枚举白名单",
        ),
    ]
    ir = _base_ir(
        _next_case_id("enum"),
        "信号枚举取值检查（离线桩草案）",
        [
            {"id": "S1", "action": "listen_bus", "duration_ms": 1000},
            {
                "id": "S2",
                "action": "assert_enum",
                "signal": "GearState",
                "allowed_values": [0, 1, 2, 3],
                "evidence_source": "REQ_GEN_ENUM",
            },
        ],
        target={"signal": "GearState"},
        expected_result="信号取值命中枚举白名单 0/1/2/3",
        unconfirmed=[p.label for p in pending],
    )
    return GenerationDraft(
        kind="enum",
        title=ir["title"],
        read_items=[
            "DBC 数据库：vehicle.dbc（样例工程）",
            "目标信号：GearState（占位，样例 DBC 未定义）",
        ],
        inferred_items=["推断允许取值：0 / 1 / 2 / 3（占位枚举）"],
        pending_items=pending,
        base_ir=ir,
    )


def _draft_timeout() -> GenerationDraft:
    pending = [
        PendingItem(
            key="evidence_source",
            step_id="S2",
            field="evidence_source",
            kind="str",
            value="REQ_GEN_TIMEOUT",
            label="阈值来源未指定：勾选表示采用默认需求编号 REQ_GEN_TIMEOUT",
        ),
        PendingItem(
            key="timeout_ms",
            step_id="S2",
            field="timeout_ms",
            kind="int",
            value="300",
            label="超时阈值 300ms 未经需求确认：勾选表示确认该阈值",
        ),
    ]
    ir = _base_ir(
        _next_case_id("timeout"),
        "报文超时监听（离线桩草案）",
        [
            {"id": "S1", "action": "listen_bus", "duration_ms": 2000},
            {
                "id": "S2",
                "action": "assert_timeout",
                "message": "BMS_Status_1",
                "timeout_ms": 300,
                "evidence_source": "REQ_GEN_TIMEOUT",
                "requires_fault_injection": False,
            },
        ],
        target={"message": "BMS_Status_1"},
        expected_result="监听窗口内报文间隔不超过 300ms，超时时给出超时起止时刻",
        unconfirmed=[p.label for p in pending],
    )
    return GenerationDraft(
        kind="timeout",
        title=ir["title"],
        read_items=[
            "DBC 数据库：vehicle.dbc（样例工程）",
            "目标报文：BMS_Status_1（样例 DBC 占位）",
        ],
        inferred_items=[
            "推断超时阈值：300 ms",
            "监听窗口：2000 ms",
        ],
        pending_items=pending,
        base_ir=ir,
    )


def _draft_send() -> GenerationDraft:
    pending = [
        PendingItem(
            key="period_ms",
            step_id="S1",
            field="period_ms",
            kind="int",
            value="20",
            label="发送周期 20ms 未经需求确认：勾选表示确认该周期",
        ),
        PendingItem(
            key="can_id",
            step_id="S1",
            field="can_id",
            kind="int",
            value="256",
            label="目标 CAN ID 0x100（256）为占位值：勾选表示确认该 ID",
        ),
    ]
    ir = _base_ir(
        _next_case_id("send"),
        "周期发送（离线桩草案）",
        [
            {
                "id": "S1",
                "action": "send_periodic",
                "can_id": 256,
                "payload_hex": "00",
                "period_ms": 20,
                "count": 5,
            },
        ],
        risk_level="L1",
        requires_approval=True,
        expected_result="按 20ms 周期发送 5 帧且被设备接受",
        unconfirmed=[p.label for p in pending],
    )
    return GenerationDraft(
        kind="send",
        title=ir["title"],
        read_items=[
            "协议总线：CAN（工程上下文默认）",
            "风险等级：L1 受控发送（需审批）",
        ],
        inferred_items=[
            "推断发送周期：20 ms",
            "推断发送次数：5 次（有限次数保护）",
        ],
        pending_items=pending,
        base_ir=ir,
    )


_BUILDERS = {
    "cycle": _draft_cycle,
    "range": _draft_range,
    "enum": _draft_enum,
    "timeout": _draft_timeout,
    "send": _draft_send,
}


def generate_draft(text: str) -> GenerationDraft:
    """按需求文本产出草案；无匹配时返回 kind="none" 的空态草案。"""
    kind = detect_kind(text)
    builder = _BUILDERS.get(kind)
    if builder is None:
        return GenerationDraft(kind="none", title="", hint=NO_MATCH_HINT)
    return builder()


def build_confirmed_ir(draft: GenerationDraft, confirmed: Mapping[str, str]) -> dict[str, Any]:
    """把勾选确认的待确认项写回草案，清空 ``unconfirmed_items`` 得到定稿 IR。

    Args:
        draft: ``generate_draft`` 的产出。
        confirmed: ``PendingItem.key -> 采用值``；缺失键回退默认值。

    Returns:
        可过 ``validate_ir``（且可执行级也无未确认项）的 Test IR dict。
    """
    document = copy.deepcopy(draft.base_ir)
    steps = {str(s.get("id")): s for s in document.get("steps", [])}
    for item in draft.pending_items:
        step = steps.get(item.step_id)
        if step is None:
            continue
        step[item.field] = _coerce(item.kind, confirmed.get(item.key, item.value))
    document["unconfirmed_items"] = []
    return document


__all__ = [
    "GenerationDraft",
    "NO_MATCH_HINT",
    "PendingItem",
    "build_confirmed_ir",
    "detect_kind",
    "generate_draft",
]

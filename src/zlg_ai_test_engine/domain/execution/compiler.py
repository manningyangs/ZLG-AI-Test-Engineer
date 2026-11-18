"""COMP-01：Test IR → Mock 执行计划确定性编译器（任务书 10.3 / 16.2 验收）。

编译规则：
- 输入先过 ``validate_executable``：结构错误、未确认项、MVP 预留动作
  一律抛 ``IRValidationError``（非法 IR 不可编译）；
- 命令序列：``configure_channel`` → ``start_channel`` 置顶（通道取自
  preconditions 的 device_ready，缺省 0）；随后按 IR steps 顺序生成
  listen / send_frame / send_periodic / wait / monitor 命令；``assert_*``
  步骤统一编译为 ``type="assert"`` 命令，排在全部采集命令之后
  （保证断言执行时其证据已采集）；
- ``cleanup`` 恒为固定四件套：stop_sends → stop_receiving →
  reset_channel → close_device；
- ``resources.capabilities_required`` = IR target_capabilities ∪ 动作隐含需求
  （send_* → basic_tx，采集/断言类 → basic_rx），排序去重；
  ``device.listen_only`` = 风险等级 L0 且 IR 无 send 动作；
- 确定性（禁用 LLM、不做 IO）：canonical json（sort_keys=True、
  ensure_ascii=False、separators=(',',':')）计算 ``ir_hash`` 与
  ``plan_hash``（对剔除 plan_hash 字段后的整个计划取哈希）；同一 IR +
  同一 capabilities + 同一 compiler_version 两次编译结果完全相等。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from zlg_ai_test_engine.domain.test_ir.validation import validate_executable

#: 编译器版本（写入计划，保证可复现）
MOCK_COMPILER_VERSION = "1.0"
#: 计划格式版本
PLAN_VERSION = "1.0"

#: Mock 后端缺省设备与资源回退值
DEFAULT_DEVICE_ID = "mock-0"
DEFAULT_MAX_DURATION_MS = 10000
DEFAULT_MAX_FRAMES = 10000

#: 发送类动作（存在任一 → listen_only=false）
_SEND_ACTIONS = frozenset({"send_frame", "send_periodic"})
#: 需要信号证据订阅的动作
_SIGNAL_ACTIONS = frozenset({"monitor_signal", "assert_range", "assert_enum"})
#: 断言类动作（编译为 type="assert"，置于采集命令之后）
_ASSERT_PREFIX = "assert_"

#: 动作 → 隐含设备能力（与 MOCK-01 能力命名保持一致）
_ACTION_IMPLIED_CAPABILITY: dict[str, str] = {
    "send_frame": "basic_tx",
    "send_periodic": "basic_tx",
    "listen_bus": "basic_rx",
    "monitor_signal": "basic_rx",
    "wait_for_frame": "basic_rx",
    "assert_cycle": "basic_rx",
    "assert_range": "basic_rx",
    "assert_enum": "basic_rx",
    "assert_timeout": "basic_rx",
}


# ---------------------------------------------------------------------------
# 确定性哈希
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """规范化 JSON：键排序、保留非 ASCII、紧凑分隔符（哈希与对比的唯一基准）。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 推导辅助
# ---------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_int(value: Any) -> int:
    return value if _is_int(value) else 0


def _derive_channel(ir: Mapping[str, Any]) -> int:
    """执行通道：preconditions 中 device_ready 的 channel，缺省 0。"""
    for precondition in ir.get("preconditions") or []:
        if isinstance(precondition, Mapping) and precondition.get("action") == "device_ready":
            channel = precondition.get("channel")
            if _is_int(channel) and channel >= 0:
                return int(channel)
    return 0


def _derive_horizon(steps: list[Mapping[str, Any]]) -> int:
    """policy 未给 max_duration_ms 时的停止时长推导：取各步骤时间跨度上界。"""
    horizon = 0
    for step in steps:
        action = step.get("action")
        if action in ("listen_bus", "monitor_signal"):
            horizon = max(horizon, _as_int(step.get("duration_ms")))
        elif action in ("wait_for_frame", "assert_timeout"):
            horizon = max(horizon, _as_int(step.get("timeout_ms")))
        elif action == "send_periodic":
            start = _as_int(step.get("start_delay_ms"))
            period = _as_int(step.get("period_ms"))
            if step.get("count") is not None and period > 0:
                horizon = max(horizon, start + _as_int(step.get("count")) * period)
            elif step.get("duration_ms") is not None:
                horizon = max(horizon, start + _as_int(step.get("duration_ms")))
    return horizon


def _periodic_count(step: Mapping[str, Any]) -> int | None:
    """send_periodic 次数：显式 count → duration/period 推导 → None（不限次）。"""
    if step.get("count") is not None:
        return _as_int(step.get("count"))
    duration = step.get("duration_ms")
    period = _as_int(step.get("period_ms"))
    if duration and period:
        return max(1, _as_int(duration) // period)
    return None


# ---------------------------------------------------------------------------
# 步骤 → 命令映射
# ---------------------------------------------------------------------------


def _send_params(step: Mapping[str, Any], channel: int) -> dict[str, Any]:
    """发送类公共参数：帧数据按 IR 原样携带，通道显式注入。"""
    params: dict[str, Any] = {}
    if step.get("message_ref") is not None:
        params["message_ref"] = dict(step["message_ref"])
    if step.get("can_id") is not None:
        params["can_id"] = step["can_id"]
    if step.get("payload_hex") is not None:
        params["payload_hex"] = step["payload_hex"]
    if step.get("dlc") is not None:
        params["dlc"] = step["dlc"]
    if step.get("is_fd"):
        params["is_fd"] = True
    if step.get("is_extended") is not None:
        params["is_extended"] = step["is_extended"]
    params["channel"] = channel
    return params


def _step_command(
    step: Mapping[str, Any], channel: int
) -> tuple[str, str, dict[str, Any]]:
    """单个采集/发送步骤 → (命令类型, 动作, 参数)。"""
    action = step.get("action")
    if action == "listen_bus":
        params: dict[str, Any] = {"duration_ms": step.get("duration_ms")}
        if step.get("filter") is not None:
            params["filter"] = dict(step["filter"])
        return "listen", action, params
    if action == "send_frame":
        params = _send_params(step, channel)
        if step.get("count") is not None:
            params["count"] = step["count"]
        return "send_frame", action, params
    if action == "send_periodic":
        params = _send_params(step, channel)
        params["period_ms"] = step.get("period_ms")
        params["start_delay_ms"] = step.get("start_delay_ms") or 0
        count = _periodic_count(step)
        if count is not None:
            params["count"] = count
        return "send_periodic", action, params
    if action == "monitor_signal":
        params = {
            "message": step.get("message"),
            "signal": step.get("signal"),
            "duration_ms": step.get("duration_ms"),
        }
        if step.get("sample_ms") is not None:
            params["sample_ms"] = step["sample_ms"]
        return "monitor", action, params
    if action == "wait_for_frame":
        params = {"timeout_ms": step.get("timeout_ms")}
        if step.get("message") is not None:
            params["message"] = step["message"]
        if step.get("can_id") is not None:
            params["can_id"] = step["can_id"]
        return "wait", action, params
    raise ValueError(f"COMP-01 无法编译的动作: {action!r}（仅 MVP 动作可编译）")


def _append_command(
    commands: list[dict[str, Any]],
    type_: str,
    step_id: str | None,
    params: dict[str, Any],
    *,
    action: str | None = None,
    evidence_source: Any = None,
) -> None:
    command: dict[str, Any] = {"seq": len(commands) + 1, "type": type_, "step_id": step_id}
    if action is not None:
        command["action"] = action
    command["params"] = params
    if evidence_source is not None:
        command["evidence_source"] = evidence_source
    commands.append(command)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def compile_to_mock_plan(
    ir: Mapping[str, Any],
    *,
    capabilities: Mapping[str, Any],
    compiler_version: str = MOCK_COMPILER_VERSION,
) -> dict[str, Any]:
    """把有效 Test IR 编译为确定性 Mock 执行计划。

    :raises zlg_ai_test_engine.domain.test_ir.validation.IRValidationError:
        IR 未通过 ``validate_executable``（结构错误/未确认项/预留动作）。
    """
    validate_executable(ir).raise_if_failed()

    steps = list(ir.get("steps") or [])
    policy = ir.get("execution_policy") or {}
    channel = _derive_channel(ir)
    bus_type = str((ir.get("protocol") or {}).get("bus", "CAN"))
    has_send = any(step.get("action") in _SEND_ACTIONS for step in steps)
    risk_level = str(policy.get("risk_level", "L0"))
    listen_only = risk_level == "L0" and not has_send
    device_id = str(capabilities.get("device_id") or DEFAULT_DEVICE_ID)

    # -- 命令序列：configure/start 置顶 → 采集/发送按 IR 顺序 → 断言殿后 ------
    commands: list[dict[str, Any]] = []
    _append_command(
        commands,
        "configure_channel",
        None,
        {"channel_index": channel, "bus_type": bus_type, "listen_only": listen_only},
    )
    _append_command(commands, "start_channel", None, {"channel_index": channel})

    acquisition = [
        s for s in steps if not str(s.get("action", "")).startswith(_ASSERT_PREFIX)
    ]
    assertions = [
        s for s in steps if str(s.get("action", "")).startswith(_ASSERT_PREFIX)
    ]
    for step in acquisition:
        type_, action, params = _step_command(step, channel)
        _append_command(commands, type_, str(step.get("id")), params, action=action)
    for step in assertions:
        # assert_*：步骤参数原样透传，evidence_source 提升到命令级
        params = {k: v for k, v in step.items() if k not in ("id", "action")}
        _append_command(
            commands,
            "assert",
            str(step.get("id")),
            params,
            action=str(step.get("action")),
            evidence_source=step.get("evidence_source"),
        )

    # -- 资源需求 -------------------------------------------------------------
    implied = {
        capability
        for capability in (
            _ACTION_IMPLIED_CAPABILITY.get(str(step.get("action"))) for step in steps
        )
        if capability
    }
    capabilities_required = sorted(set(policy.get("target_capabilities") or []) | implied)
    max_duration = policy.get("max_duration_ms")
    if max_duration is None:
        max_duration = _derive_horizon(steps) or DEFAULT_MAX_DURATION_MS
    max_frames = policy.get("max_frames") or DEFAULT_MAX_FRAMES

    # -- 证据订阅 -------------------------------------------------------------
    signal_names = sorted(
        {
            str(step.get("signal"))
            for step in steps
            if step.get("action") in _SIGNAL_ACTIONS and step.get("signal")
        }
    )
    subscriptions: list[dict[str, Any]] = [{"kind": "frames", "channel": channel}]
    if signal_names:
        subscriptions.append({"kind": "signals", "names": signal_names})

    # -- 固定清理四件套 --------------------------------------------------------
    cleanup: list[dict[str, Any]] = [
        {"type": "stop_sends"},
        {"type": "stop_receiving"},
        {"type": "reset_channel", "channel_index": channel},
        {"type": "close_device"},
    ]

    plan: dict[str, Any] = {
        "plan_version": PLAN_VERSION,
        "compiler_version": compiler_version,
        "backend": "mock",
        "ir_hash": _sha256(ir),
        "device": {
            "device_id": device_id,
            "channel": channel,
            "bus_type": bus_type,
            "listen_only": listen_only,
            "risk_level": risk_level,
            "requires_approval": bool(policy.get("requires_approval", False)),
        },
        "resources": {
            "capabilities_required": capabilities_required,
            "max_duration_ms": int(max_duration),
            "max_frames": int(max_frames),
        },
        "commands": commands,
        "evidence_subscriptions": subscriptions,
        "cleanup": cleanup,
    }
    # plan_hash：对剔除 plan_hash 字段后的整个计划取 canonical json 哈希
    plan_hash = _sha256(plan)
    ordered: dict[str, Any] = {
        "plan_version": plan["plan_version"],
        "compiler_version": plan["compiler_version"],
        "backend": plan["backend"],
        "ir_hash": plan["ir_hash"],
        "plan_hash": plan_hash,
        "device": plan["device"],
        "resources": plan["resources"],
        "commands": plan["commands"],
        "evidence_subscriptions": plan["evidence_subscriptions"],
        "cleanup": plan["cleanup"],
    }
    return ordered

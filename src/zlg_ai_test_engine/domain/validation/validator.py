"""VALID-01：执行前七类集中规则校验（任务书 10.2 / 16.2 验收）。

七类检查（每类失败产出 ERROR Diagnostic，message 必含“修正建议”）：
1. ``chk_project_ref`` 工程引用：资产注册表状态/哈希匹配、
   NetworkModel 的 message/signal 引用存在性。
2. ``chk_protocol`` 协议能力：能力视图 buses 覆盖 IR 声明的总线。
3. ``chk_device_cap`` 设备能力：target_capabilities 的证据等级达标
   （official_declared / sdk_visible / verified_on_device；未知 ≠ 支持）。
4. ``chk_param`` 参数合法性：时长/帧数/周期/can_id/payload/dlc
   防御性复查（即使 CORE-01 结构校验漏过也在此拦截）。
5. ``chk_security`` 安全授权：L1+ 且 requires_approval 的用例必须
   审批对应动作且用户等级不低于风险等级；L4 恒阻断（MVP 禁止）。
6. ``chk_resource`` 资源冲突：设备/通道占用；周期发送与
   periodic/queue 活动资源互斥。
7. ``chk_plan`` 计划完备性：停止条件（max_duration_ms）、断言步骤、
   未确认项。

约定：
- 入口先执行 ``validate_executable``，结构级错误原样透传（不再进入七类检查）；
- NetworkModel 缺失/不含相应清单属降级场景，只出 WARNING（引用待核），
  绝不升级为 ERROR（降级检查不得成为阻断项）；
- 纯函数：不触碰设备、不做 IO、同一输入可重复得到同一结果。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from zlg_ai_test_engine.domain.plugins.contracts import Diagnostic, Severity
from zlg_ai_test_engine.domain.test_ir.actions import ACTION_SPECS
from zlg_ai_test_engine.domain.test_ir.validation import ValidationResult, validate_executable

#: 判定“设备能力受支持”的证据等级（与 MOCK-01 一致：未知 ≠ 支持）
CAPABLE_EVIDENCE: frozenset[str] = frozenset(
    {"official_declared", "sdk_visible", "verified_on_device"}
)

#: 风险等级数值序（L0 < L1 < L2 < L3 < L4）
_RISK_ORDER: dict[str, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}

#: 默认风险等级 >= L1 的动作（执行前必须出现在审批清单中）
_RISKY_ACTIONS: frozenset[str] = frozenset(
    action
    for action, spec in ACTION_SPECS.items()
    if _RISK_ORDER.get(spec.default_risk_level.value, 0) >= 1
)

#: 以 message 为引用来源的步骤动作（send_* 走 message_ref.message）
_MESSAGE_STEPS: frozenset[str] = frozenset(
    {"send_frame", "send_periodic", "monitor_signal", "wait_for_frame",
     "assert_cycle", "assert_timeout"}
)
#: 以 signal 为引用来源的步骤动作
_SIGNAL_STEPS: frozenset[str] = frozenset({"monitor_signal", "assert_range", "assert_enum"})

_HEX_PAYLOAD = re.compile(r"^(?:[0-9a-fA-F]{2})*$")
_CAN_ID_MAX = 0x1FFFFFFF


# ---------------------------------------------------------------------------
# 输入上下文
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationContext:
    """执行前校验输入（任务书 10.2 七类检查所需的全部上下文）。"""

    #: 已过 CORE-01 draft 级校验的 IR dict
    ir: Mapping[str, Any]
    #: asset_id -> {"sha256": str, "status": "active|stale|missing", "type": str}
    asset_registry: Mapping[str, Any]
    #: 解析后的 NetworkModel（None → 引用检查降级并出 WARNING）
    network_model: Mapping[str, Any] | None
    #: 设备能力视图（MockAdapter.device_capabilities() 形状，含 evidence 四级）
    capabilities: Mapping[str, Any]
    #: {"user_level": "L0".."L4", "approved_actions": [..], "environment": str}
    approval: Mapping[str, Any]
    #: {"device_busy": bool, "busy_channels": [int], "periodic_active": bool, "queue_active": bool}
    resource_state: Mapping[str, Any]


# ---------------------------------------------------------------------------
# 诊断小工具
# ---------------------------------------------------------------------------


def _error(code: str, message: str, location: str | None = None) -> Diagnostic:
    return Diagnostic(code=code, message=message, severity=Severity.ERROR, location=location)


def _warning(code: str, message: str, location: str | None = None) -> Diagnostic:
    return Diagnostic(code=code, message=message, severity=Severity.WARNING, location=location)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return _is_int(value) and value > 0


def _norm_sha(value: Any) -> str:
    text = str(value).strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):]
    return text


# ---------------------------------------------------------------------------
# 1. 工程引用（chk_project_ref）
# ---------------------------------------------------------------------------


def _check_asset_ref(
    ctx: ValidationContext, location: str, asset_id: str, declared_sha: Any
) -> list[Diagnostic]:
    entry = ctx.asset_registry.get(asset_id)
    if not isinstance(entry, Mapping):
        return [
            _error(
                "chk_project_ref.asset_not_registered",
                f"{location} 引用的资产 '{asset_id}' 未在资产注册表登记，执行被阻断。"
                "修正建议：将该资产导入工程并完成解析，确认注册状态为 active 后重试",
                location,
            )
        ]
    status = entry.get("status")
    if status != "active":
        return [
            _error(
                "chk_project_ref.asset_not_active",
                f"{location} 引用的资产 '{asset_id}' 状态为 {status!r}（要求 active），"
                "过期/缺失资产不得参与执行。修正建议：重新导入或重新解析该资产，"
                "确认哈希一致后将其刷新为 active 再执行",
                location,
            )
        ]
    registered_sha = entry.get("sha256")
    if declared_sha and registered_sha:
        if _norm_sha(declared_sha) != _norm_sha(registered_sha):
            return [
                _error(
                    "chk_project_ref.asset_hash_mismatch",
                    f"{location} 声明的 sha256 与资产注册表记录不一致"
                    f"（IR={_norm_sha(declared_sha)[:12]}…，注册表={_norm_sha(registered_sha)[:12]}…），"
                    "版本失配阻断执行。修正建议：重新解析资产以更新注册表哈希，"
                    "或把 IR 中的 sha256 更新为当前注册值",
                    location,
                )
            ]
    return []


def _collect_message_signal_refs(
    ir: Mapping[str, Any]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """收集 IR 中的 (location, 报文名) 与 (location, 信号名) 引用。"""
    messages: list[tuple[str, str]] = []
    signals: list[tuple[str, str]] = []
    target = ir.get("target") or {}
    if isinstance(target, Mapping):
        if target.get("message"):
            messages.append(("target.message", str(target["message"])))
        if target.get("signal"):
            signals.append(("target.signal", str(target["signal"])))
    for step in ir.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        action = step.get("action")
        location = f"steps/{step.get('id', '?')}"
        if action in ("send_frame", "send_periodic"):
            ref = step.get("message_ref")
            if isinstance(ref, Mapping) and ref.get("message"):
                messages.append((location, str(ref["message"])))
        elif action in _MESSAGE_STEPS and step.get("message"):
            messages.append((location, str(step["message"])))
        if action in _SIGNAL_STEPS and step.get("signal"):
            signals.append((location, str(step["signal"])))
    return messages, signals


def _absorb_names(
    entry: Mapping[str, Any], messages: set[str], signals: set[str]
) -> None:
    name = entry.get("name") or entry.get("message_name") or entry.get("message")
    if name:
        messages.add(str(name))
    for key in ("signals", "signal_names"):
        if key in entry:
            signals |= _name_set(entry.get(key))


def _name_set(container: Any) -> set[str]:
    """把 Mapping/Sequence 形状的名字容器归一为字符串集合（容错解析）。"""
    out: set[str] = set()
    if isinstance(container, Mapping):
        out.update(str(key) for key in container)
        for value in container.values():
            if isinstance(value, Mapping):
                name = value.get("name") or value.get("signal_name") or value.get("signal")
                if name:
                    out.add(str(name))
    elif isinstance(container, Sequence) and not isinstance(container, (str, bytes)):
        for item in container:
            if isinstance(item, str):
                out.add(item)
            elif isinstance(item, Mapping):
                name = item.get("name") or item.get("signal_name") or item.get("signal")
                if name:
                    out.add(str(name))
    return out


def _model_view(model: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """从 NetworkModel dict 提取（报文名集合, 信号名集合）。

    容忍常见容器形状：messages/signals 为 {名: 条目} 或 [{条目}]；
    信号也可嵌套在每个报文的 signals/signal_names 中。
    """
    messages: set[str] = set()
    signals: set[str] = _name_set(model.get("signals"))
    container = model.get("messages")
    if isinstance(container, Mapping):
        for key, value in container.items():
            messages.add(str(key))
            if isinstance(value, Mapping):
                _absorb_names(value, messages, signals)
    elif isinstance(container, Sequence) and not isinstance(container, (str, bytes)):
        for item in container:
            if isinstance(item, str):
                messages.add(item)
            elif isinstance(item, Mapping):
                _absorb_names(item, messages, signals)
    return messages, signals


def _check_project_ref(ctx: ValidationContext) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    ir = ctx.ir

    database = (ir.get("protocol") or {}).get("database")
    if database:
        diagnostics.extend(_check_asset_ref(ctx, "protocol.database", str(database), None))
    for ref in ir.get("database_refs") or []:
        if not isinstance(ref, Mapping):
            continue
        asset_id = ref.get("asset_id")
        if asset_id:
            diagnostics.extend(
                _check_asset_ref(
                    ctx, "database_refs.asset_id", str(asset_id), ref.get("sha256")
                )
            )

    msg_refs, sig_refs = _collect_message_signal_refs(ir)
    if not msg_refs and not sig_refs:
        return diagnostics

    model = ctx.network_model
    if model is None:
        diagnostics.append(
            _warning(
                "chk_project_ref.references_pending",
                f"未提供网络模型，{len(msg_refs)} 处报文与 {len(sig_refs)} 处信号引用待核"
                "（降级为警告，不阻断）。修正建议：先解析工程 DBC 生成 NetworkModel 后再执行",
            )
        )
        return diagnostics

    messages, signals = _model_view(model)
    if messages:
        for location, name in msg_refs:
            if name not in messages:
                diagnostics.append(
                    _error(
                        "chk_project_ref.message_not_found",
                        f"{location} 引用的报文 '{name}' 不存在于网络模型，执行被阻断。"
                        "修正建议：改用网络模型中已存在的报文，或重新解析对应 DBC 资产后再执行",
                        location,
                    )
                )
    elif msg_refs:
        diagnostics.append(
            _warning(
                "chk_project_ref.references_pending",
                "网络模型未包含报文清单，报文引用待核（降级为警告，不阻断）。"
                "修正建议：补充解析网络模型的 messages 清单后重新校验",
            )
        )
    if signals:
        for location, name in sig_refs:
            if name not in signals:
                diagnostics.append(
                    _error(
                        "chk_project_ref.signal_not_found",
                        f"{location} 引用的信号 '{name}' 不存在于网络模型，执行被阻断。"
                        "修正建议：改用网络模型中已存在的信号，或重新解析对应 DBC 资产后再执行",
                        location,
                    )
                )
    elif sig_refs:
        diagnostics.append(
            _warning(
                "chk_project_ref.references_pending",
                "网络模型未包含信号清单，信号引用待核（降级为警告，不阻断）。"
                "修正建议：补充解析网络模型的 signals 清单后重新校验",
            )
        )
    return diagnostics


# ---------------------------------------------------------------------------
# 2. 协议能力（chk_protocol）
# ---------------------------------------------------------------------------


def _check_protocol(ctx: ValidationContext) -> list[Diagnostic]:
    bus = (ctx.ir.get("protocol") or {}).get("bus")
    buses = ctx.capabilities.get("buses")
    bus_list = list(buses) if isinstance(buses, Sequence) and not isinstance(buses, (str, bytes)) else []
    if bus in bus_list:
        return []
    return [
        _error(
            "chk_protocol.bus_unsupported",
            f"协议总线 {bus!r} 不在设备能力视图 buses={bus_list} 中（能力缺失或未声明），"
            "执行被阻断。修正建议：更换声明支持该总线的设备/适配器，"
            "或在能力视图中登记该总线的证据等级后重试",
            "protocol.bus",
        )
    ]


# ---------------------------------------------------------------------------
# 3. 设备能力（chk_device_cap）
# ---------------------------------------------------------------------------


def _check_device_capability(ctx: ValidationContext) -> list[Diagnostic]:
    policy = ctx.ir.get("execution_policy") or {}
    evidence = ctx.capabilities.get("evidence")
    evidence_map = dict(evidence) if isinstance(evidence, Mapping) else {}
    diagnostics: list[Diagnostic] = []
    for capability in policy.get("target_capabilities") or []:
        level = evidence_map.get(capability)
        if level is None:
            diagnostics.append(
                _error(
                    "chk_device_cap.unproven_capability",
                    f"目标能力 '{capability}' 未在设备能力视图 evidence 中声明，"
                    "未知 ≠ 支持，执行被阻断。修正建议：改用设备已声明的能力项，"
                    "或经官方声明/SDK 可见/实机验证补充该能力证据后重试",
                    "execution_policy.target_capabilities",
                )
            )
        elif level not in CAPABLE_EVIDENCE:
            diagnostics.append(
                _error(
                    "chk_device_cap.unproven_capability",
                    f"目标能力 '{capability}' 的证据等级为 {level!r}"
                    "（要求 official_declared/sdk_visible/verified_on_device），"
                    "未知 ≠ 支持，执行被阻断。修正建议：降低能力要求，"
                    "或补齐可验证的设备能力证据后重试",
                    "execution_policy.target_capabilities",
                )
            )
    return diagnostics


# ---------------------------------------------------------------------------
# 4. 参数合法性（chk_param，防御性复查）
# ---------------------------------------------------------------------------


def _check_param(ctx: ValidationContext) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    ir = ctx.ir
    policy = ir.get("execution_policy") or {}

    max_duration = policy.get("max_duration_ms")
    if max_duration is not None and not _is_positive_int(max_duration):
        diagnostics.append(
            _error(
                "chk_param.invalid_max_duration",
                f"execution_policy.max_duration_ms={max_duration!r} 不是正整数，"
                "停止时长非法。修正建议：将其设为大于 0 的整数毫秒值后重新校验",
                "execution_policy.max_duration_ms",
            )
        )
    max_frames = policy.get("max_frames")
    if max_frames is not None and not _is_positive_int(max_frames):
        diagnostics.append(
            _error(
                "chk_param.invalid_max_frames",
                f"execution_policy.max_frames={max_frames!r} 不是正整数，"
                "最大帧数非法。修正建议：将其设为大于 0 的整数后重新校验",
                "execution_policy.max_frames",
            )
        )

    bus = (ir.get("protocol") or {}).get("bus")
    for step in ir.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        action = step.get("action")
        sid = str(step.get("id", "?"))

        if action == "send_periodic":
            if not _is_positive_int(step.get("period_ms")):
                diagnostics.append(
                    _error(
                        "chk_param.invalid_period",
                        f"步骤 {sid}（send_periodic）的 period_ms={step.get('period_ms')!r} "
                        "不是正整数，周期发送参数非法。修正建议：将 period_ms 设为大于 0 的"
                        "整数毫秒值后重新校验",
                        sid,
                    )
                )
            delay = step.get("start_delay_ms")
            if delay is not None and (not _is_int(delay) or delay < 0):
                diagnostics.append(
                    _error(
                        "chk_param.invalid_start_delay",
                        f"步骤 {sid}（send_periodic）的 start_delay_ms={delay!r} 为负或非整数，"
                        "启动延迟非法。修正建议：将 start_delay_ms 设为大于等于 0 的整数毫秒值",
                        sid,
                    )
                )
            duration = step.get("duration_ms")
            if duration is not None and not _is_positive_int(duration):
                diagnostics.append(
                    _error(
                        "chk_param.invalid_duration",
                        f"步骤 {sid}（send_periodic）的 duration_ms={duration!r} 不是正整数。"
                        "修正建议：将 duration_ms 设为大于 0 的整数毫秒值，或移除该字段改用 count 限定次数",
                        sid,
                    )
                )

        if action in ("send_frame", "send_periodic", "wait_for_frame"):
            can_id = step.get("can_id")
            if can_id is not None and (not _is_int(can_id) or not 0 <= can_id <= _CAN_ID_MAX):
                diagnostics.append(
                    _error(
                        "chk_param.invalid_can_id",
                        f"步骤 {sid} 的 can_id={can_id!r} 超出 0..{_CAN_ID_MAX} 范围（29 位标识）。"
                        "修正建议：改用合法的标准/扩展 CAN ID 后重新校验",
                        sid,
                    )
                )

        if action in ("send_frame", "send_periodic"):
            payload = step.get("payload_hex")
            is_fd = bool(step.get("is_fd"))
            if payload is not None and not _HEX_PAYLOAD.fullmatch(str(payload)):
                diagnostics.append(
                    _error(
                        "chk_param.invalid_payload",
                        f"步骤 {sid} 的 payload_hex={payload!r} 不是偶数长度的合法十六进制串。"
                        "修正建议：改为成对十六进制字符（每字节 2 位，如 '0102'）后重新校验",
                        sid,
                    )
                )
            elif payload is not None and not is_fd and len(str(payload)) // 2 > 8:
                diagnostics.append(
                    _error(
                        "chk_param.payload_too_large",
                        f"步骤 {sid} 在非 FD 帧上载荷 {len(str(payload)) // 2} 字节，"
                        "超过 CAN 经典帧 8 字节上限。修正建议：缩减载荷至 8 字节内，"
                        "或将该步骤与 protocol.bus 显式切换为 CAN FD",
                        sid,
                    )
                )
            if is_fd and bus != "CAN_FD":
                diagnostics.append(
                    _error(
                        "chk_param.fd_on_non_fd_bus",
                        f"步骤 {sid} 声明 is_fd=true，但协议总线为 {bus!r}（非 CAN FD），"
                        "帧格式与总线能力不匹配。修正建议：将该步骤 is_fd 设为 false 并把载荷"
                        "压缩到 8 字节内，或把 protocol.bus 改为 CAN_FD",
                        sid,
                    )
                )
            dlc = step.get("dlc")
            if dlc is not None and (
                not _is_int(dlc) or not 0 <= dlc <= 64 or (not is_fd and dlc > 8)
            ):
                diagnostics.append(
                    _error(
                        "chk_param.invalid_dlc",
                        f"步骤 {sid} 的 dlc={dlc!r} 非法（非 FD 帧上限 8，FD 帧上限 64）。"
                        "修正建议：把 dlc 设为与载荷字节数一致且不超过对应帧类型上限的值",
                        sid,
                    )
                )

        if action == "listen_bus" and not _is_positive_int(step.get("duration_ms")):
            diagnostics.append(
                _error(
                    "chk_param.invalid_duration",
                    f"步骤 {sid}（listen_bus）的 duration_ms={step.get('duration_ms')!r} 不是正整数。"
                    "修正建议：将监听时长设为大于 0 的整数毫秒值",
                    sid,
                )
            )
        if action == "monitor_signal":
            if not _is_positive_int(step.get("duration_ms")):
                diagnostics.append(
                    _error(
                        "chk_param.invalid_duration",
                        f"步骤 {sid}（monitor_signal）的 duration_ms={step.get('duration_ms')!r} "
                        "不是正整数。修正建议：将监控时长设为大于 0 的整数毫秒值",
                        sid,
                    )
                )
            sample = step.get("sample_ms")
            if sample is not None and not _is_positive_int(sample):
                diagnostics.append(
                    _error(
                        "chk_param.invalid_duration",
                        f"步骤 {sid}（monitor_signal）的 sample_ms={sample!r} 不是正整数。"
                        "修正建议：将采样间隔设为大于 0 的整数毫秒值",
                        sid,
                    )
                )
        if action in ("wait_for_frame", "assert_timeout") and not _is_positive_int(
            step.get("timeout_ms")
        ):
            diagnostics.append(
                _error(
                    "chk_param.invalid_timeout",
                    f"步骤 {sid}（{action}）的 timeout_ms={step.get('timeout_ms')!r} 不是正整数。"
                    "修正建议：将超时设为大于 0 的整数毫秒值后重新校验",
                    sid,
                )
            )
    return diagnostics


# ---------------------------------------------------------------------------
# 5. 安全授权（chk_security）
# ---------------------------------------------------------------------------


def _check_security(ctx: ValidationContext) -> list[Diagnostic]:
    policy = ctx.ir.get("execution_policy") or {}
    risk = str(policy.get("risk_level", "L0"))
    risk_no = _RISK_ORDER.get(risk, 0)

    if risk == "L4":
        return [
            _error(
                "chk_security.level_prohibited",
                "风险等级 L4 为高风险用例，MVP 阶段禁止执行（无论是否已审批）。"
                "修正建议：将用例拆分为 L0–L3 的受控步骤并重新评审风险等级",
                "execution_policy.risk_level",
            )
        ]

    if risk_no < 1 or not policy.get("requires_approval"):
        return []

    diagnostics: list[Diagnostic] = []
    approval = ctx.approval
    user_level = approval.get("user_level")
    user_no = _RISK_ORDER.get(str(user_level)) if user_level is not None else None
    if user_no is None:
        diagnostics.append(
            _error(
                "chk_security.user_level_missing",
                f"用例风险等级 {risk} 且 requires_approval=true，但无法确认当前用户授权等级"
                f"(user_level={user_level!r})。修正建议：完成用户登录并登记 L0–L4 等级后重试",
                "approval.user_level",
            )
        )
    elif user_no < risk_no:
        diagnostics.append(
            _error(
                "chk_security.user_level_insufficient",
                f"用户等级 {user_level} 低于用例风险等级 {risk}，授权不足阻断执行。"
                f"修正建议：由 {risk} 及以上等级的用户审批该用例，或将 execution_policy."
                "risk_level 降至用户等级以内",
                "approval.user_level",
            )
        )

    approved = [str(a) for a in (approval.get("approved_actions") or [])]
    for step in ctx.ir.get("steps") or []:
        action = step.get("action") if isinstance(step, Mapping) else None
        if action in _RISKY_ACTIONS and action not in approved:
            approved_repr = ", ".join(approved) if approved else "空"
            diagnostics.append(
                _error(
                    "chk_security.approval_missing_action",
                    f"步骤 {step.get('id')} 的动作 {action} 属受控动作，"
                    f"未出现在审批清单 approved_actions=[{approved_repr}] 中，执行被阻断。"
                    f"修正建议：在授权面板为当前环境批准动作 {action}（写入 approval."
                    "approved_actions）后重试",
                    str(step.get("id")),
                )
            )
    return diagnostics


# ---------------------------------------------------------------------------
# 6. 资源冲突（chk_resource）
# ---------------------------------------------------------------------------


def _required_channels(ir: Mapping[str, Any]) -> set[int]:
    """IR 所需通道：preconditions 中 device_ready 的 channel（缺省为 0）。"""
    channels: set[int] = set()
    for precondition in ir.get("preconditions") or []:
        if isinstance(precondition, Mapping) and precondition.get("action") == "device_ready":
            channel = precondition.get("channel")
            channels.add(int(channel) if _is_int(channel) else 0)
    if not channels:
        channels.add(0)
    return channels


def _check_resource(ctx: ValidationContext) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    state = ctx.resource_state

    if state.get("device_busy"):
        diagnostics.append(
            _error(
                "chk_resource.device_busy",
                "设备当前被占用（device_busy=true），并发打开会破坏执行确定性，已阻断。"
                "修正建议：等待占用任务结束或执行急停释放设备后重试",
                "resource_state.device_busy",
            )
        )

    busy_raw = state.get("busy_channels") or []
    busy = {int(c) for c in busy_raw if _is_int(c)} if isinstance(busy_raw, Sequence) else set()
    for channel in sorted(_required_channels(ctx.ir) & busy):
        diagnostics.append(
            _error(
                "chk_resource.channel_busy",
                f"IR 所需通道 {channel} 正被占用（busy_channels={sorted(busy)}），执行被阻断。"
                "修正建议：释放该通道的占用任务，或改用空闲通道并同步更新 preconditions 后重试",
                "resource_state.busy_channels",
            )
        )

    has_periodic = any(
        isinstance(step, Mapping) and step.get("action") == "send_periodic"
        for step in ctx.ir.get("steps") or []
    )
    if has_periodic:
        active = [
            flag
            for flag in ("periodic_active", "queue_active")
            if state.get(flag)
        ]
        if active:
            diagnostics.append(
                _error(
                    "chk_resource.periodic_conflict",
                    "IR 含 send_periodic 步骤，但资源侧周期/队列发送处于活动状态"
                    f"（{'、'.join(active)}），互斥规则禁止并发，执行被阻断。"
                    "修正建议：先停止现有周期/队列发送任务并清空定时槽位后重试",
                    "resource_state",
                )
            )
    return diagnostics


# ---------------------------------------------------------------------------
# 7. 计划完备性（chk_plan）
# ---------------------------------------------------------------------------


def _check_plan(ctx: ValidationContext) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    ir = ctx.ir
    policy = ir.get("execution_policy") or {}

    if policy.get("max_duration_ms") is None:
        diagnostics.append(
            _error(
                "chk_plan.missing_max_duration",
                "execution_policy 缺少 max_duration_ms，停止条件缺失，"
                "用例可能无限执行，已阻断。修正建议：设置正整数的最大执行时长"
                "（如 10000 毫秒）与必要的 max_frames 后重新校验",
                "execution_policy.max_duration_ms",
            )
        )

    has_assertion = any(
        isinstance(step, Mapping)
        and str(step.get("action", "")).startswith("assert_")
        for step in ir.get("steps") or []
    )
    if not has_assertion:
        diagnostics.append(
            _warning(
                "chk_plan.no_assertion",
                "用例不含任何 assert_* 断言步骤，执行后无法产生 Pass/Fail 判定"
                "（警告，不阻断）。修正建议：补充带 evidence_source 的断言步骤以形成可判定计划",
                "steps",
            )
        )

    unconfirmed = ir.get("unconfirmed_items") or []
    if unconfirmed:
        diagnostics.append(
            _error(
                "chk_plan.unconfirmed_items",
                f"存在 {len(unconfirmed)} 项未确认参数（{unconfirmed}），"
                "执行被阻断。修正建议：在用例编辑器中逐项确认或修订这些参数，"
                "清空 unconfirmed_items 后重新校验",
                "unconfirmed_items",
            )
        )
    return diagnostics


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

_CHECKS = (
    _check_project_ref,
    _check_protocol,
    _check_device_capability,
    _check_param,
    _check_security,
    _check_resource,
    _check_plan,
)


def validate_execution(ctx: ValidationContext) -> ValidationResult:
    """执行前七类集中校验（复用 CORE-01 的 ValidationResult）。

    先执行 ``validate_executable``：结构级失败（ir_schema/ir_model/未确认项/
    预留动作）原样透传，不再进入七类检查；通过后依次运行七类检查，
    无 ERROR 即 ``result.ok``。
    """
    base = validate_executable(ctx.ir)
    if not base.ok:
        return base
    diagnostics: list[Diagnostic] = list(base.diagnostics)
    for check in _CHECKS:
        diagnostics.extend(check(ctx))
    return ValidationResult(diagnostics=tuple(diagnostics))

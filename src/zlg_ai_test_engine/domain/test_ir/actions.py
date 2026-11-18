"""Test IR 动作字典（CORE-01）。

每种动作声明：默认风险等级、MVP 是否启用（可执行）、简要用途。
- ``mvp_enabled=False`` 为预留动作（inject_fault / uds_request）：
  结构上允许出现在草稿 IR 中，但 ``validate_executable`` 默认阻断执行。
"""

from __future__ import annotations

from dataclasses import dataclass

from zlg_ai_test_engine.domain.plugins.contracts import RiskLevel


@dataclass(frozen=True)
class ActionSpec:
    """动作规格。"""

    action: str
    title: str
    default_risk_level: RiskLevel
    mvp_enabled: bool
    summary: str


ACTION_SPECS: dict[str, ActionSpec] = {
    "listen_bus": ActionSpec(
        "listen_bus", "监听总线", RiskLevel.L0, True, "监听并记录报文；只读默认动作"
    ),
    "send_frame": ActionSpec(
        "send_frame", "单帧发送", RiskLevel.L1, True, "发送一帧或有限次数报文；需 L1 确认"
    ),
    "send_periodic": ActionSpec(
        "send_periodic", "周期发送", RiskLevel.L1, True, "周期发送；优先设备端硬件定时"
    ),
    "monitor_signal": ActionSpec(
        "monitor_signal", "信号监控", RiskLevel.L0, True, "监控信号物理值与采样数据"
    ),
    "wait_for_frame": ActionSpec(
        "wait_for_frame", "等待帧", RiskLevel.L0, True, "等待指定帧/信号到达，记录时间戳"
    ),
    "assert_cycle": ActionSpec(
        "assert_cycle", "周期断言", RiskLevel.L0, True, "验证报文到达周期与容差"
    ),
    "assert_range": ActionSpec(
        "assert_range", "范围断言", RiskLevel.L0, True, "验证信号物理值上下限"
    ),
    "assert_enum": ActionSpec(
        "assert_enum", "枚举断言", RiskLevel.L0, True, "验证信号枚举值合法性"
    ),
    "assert_timeout": ActionSpec(
        "assert_timeout", "超时断言", RiskLevel.L0, True, "验证监听窗口内报文是否超时未到"
    ),
    "inject_fault": ActionSpec(
        "inject_fault", "故障注入", RiskLevel.L2, False, "MVP 预留：默认禁用，需批准台架"
    ),
    "uds_request": ActionSpec(
        "uds_request", "UDS 诊断请求", RiskLevel.L3, False, "MVP 预留：1.2 版本按 L3 授权启用"
    ),
}

#: MVP 可执行动作集合
MVP_ACTIONS: frozenset[str] = frozenset(a for a, s in ACTION_SPECS.items() if s.mvp_enabled)

#: 预留（结构合法、执行阻断）动作集合
RESERVED_ACTIONS: frozenset[str] = frozenset(a for a, s in ACTION_SPECS.items() if not s.mvp_enabled)

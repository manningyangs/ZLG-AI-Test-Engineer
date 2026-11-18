"""MOCK-01：确定性 Mock/Offline 设备适配器 v1（无 DLL、无网络、不触碰真实设备）。

能力概览：
- 完整实现 domain.plugins.contracts.DeviceSDKAdapter 的全部抽象方法；
- 内置虚拟时钟：advance(ms) 手动步进、run(duration_ms) 运行一段虚拟时间，
  二者均按虚拟时间顺序触发到期事件；
- 场景配置 MockScenario（冻结 dataclass）：周期帧、单次定时帧、缺帧规则、
  延迟到达帧、错误帧注入、信号载荷序列；
- 故障注入 FaultInjection：设备离线、发送失败、接收回调中断、任务取消回滚、
  句柄占用冲突；
- 确定性：帧时间戳全部来自虚拟时钟（wall_clock 恒为 None），随机抖动只使用
  显式 seeded random（默认 seed 固定、jitter=0），事件队列按（时间, 序号）全序处理；
- 能力视图带证据等级（official_declared / sdk_visible / verified_on_device /
  unknown），未知能力 is_capable 一律返回 False（未知 ≠ 支持）；
- emergency_stop 逐项清理并写入 EmergencyStopReport，急停后可重新打开（无残留）。

已知限制：
- 仅实现虚拟时间模式（realtime 模式为可选项，本版本未实现）；
- 任务取消回滚仅覆盖 Mock 内部状态（虚拟时钟、事件队列、随机数状态、运行态标志）；
  已交付给接收回调的帧与已记录日志属于证据，采用 append-only，不追回。
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from zlg_ai_test_engine.domain.plugins.contracts import (
    ChannelConfig,
    DeviceInfo,
    DeviceSDKAdapter,
    EmergencyStopReport,
    Frame,
    SendResult,
)

# ---------------------------------------------------------------------------
# 能力证据等级（Capability Profile 视图）
# ---------------------------------------------------------------------------

#: 判定“支持”的证据等级（未知 ≠ 支持，unknown 必须返回 False）
CAPABLE_EVIDENCE: frozenset[str] = frozenset(
    {"official_declared", "sdk_visible", "verified_on_device"}
)
#: 全部合法证据等级
ALL_EVIDENCE: frozenset[str] = CAPABLE_EVIDENCE | {"unknown"}

#: 内置 mock 设备的默认能力证据
_DEFAULT_CAPABILITIES: dict[str, str] = {
    "basic_tx": "verified_on_device",
    "basic_rx": "verified_on_device",
    "can_fd": "official_declared",
    "multi_channel": "official_declared",
    # 显式声明为 unknown：Mock 未模拟硬件周期发送，必须判为不支持
    "hardware_periodic_tx": "unknown",
}

#: 内置 mock 设备清单（至少 1 台）
BUILTIN_MOCK_DEVICE = DeviceInfo(
    device_id="mock-0",
    model="MOCK-CANFD-200U",
    serial="MOCK-SN-00000001",
    channel_count=2,
    hardware_version="MOCK-A",
    firmware_version="1.0.0",
    driver_version="mock-driver-1.0",
    sdk_version="mock-sdk-1.0",
    extra={"buses": ["CAN", "CAN_FD"]},
)


# ---------------------------------------------------------------------------
# 故障注入异常
# ---------------------------------------------------------------------------


class MockAdapterError(RuntimeError):
    """Mock 适配器错误基类（继承 RuntimeError，与现有契约测试风格一致）。"""


class MockDeviceOfflineError(MockAdapterError):
    """注入故障：设备离线，open_device 抛出。"""


class MockHandleOccupiedError(MockAdapterError):
    """设备句柄被占用（外部进程占用或本适配器重复打开）。"""


class MockReceiveError(MockAdapterError):
    """注入故障：接收失败，回调中断并中止本次步进。"""


class MockTaskCancelledError(MockAdapterError):
    """注入故障：任务被取消，Mock 内部状态回滚到任务开始前。"""


# ---------------------------------------------------------------------------
# 场景配置（冻结 dataclass，构造 MockAdapter(scenario) 时注入）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodicFrameSpec:
    """周期发送帧：第 1 次在 start_delay_ms 后触发，此后每 period_ms 一次。"""

    can_id: int
    data: bytes
    period_ms: int
    start_delay_ms: int = 0
    #: None 表示无限重复
    count: int | None = None
    target_channel: int = 0
    is_fd: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", bytes(self.data))
        if self.period_ms < 1:
            raise ValueError(f"period_ms 必须 >= 1: {self.period_ms}")
        if self.start_delay_ms < 0:
            raise ValueError(f"start_delay_ms 不能为负: {self.start_delay_ms}")
        if self.count is not None and self.count < 1:
            raise ValueError(f"count 必须 >= 1 或为 None: {self.count}")


@dataclass(frozen=True)
class ScheduledFrameSpec:
    """单次定时帧：装配后 delay_ms 触发一次。"""

    delay_ms: int
    can_id: int
    data: bytes
    channel: int = 0
    is_fd: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", bytes(self.data))
        if self.delay_ms < 0:
            raise ValueError(f"delay_ms 不能为负: {self.delay_ms}")


@dataclass(frozen=True)
class MissingRuleSpec:
    """缺帧规则：按出现次序（1 起）丢弃周期帧，用于报文超时/丢帧场景。"""

    can_id: int
    #: 每 N 次丢 1 次（第 N、2N…次）
    every_n: int | None = None
    #: 从第 M 次开始全部丢弃
    drop_from: int | None = None
    #: 限定目标通道；None 表示不限通道
    channel: int | None = None

    def __post_init__(self) -> None:
        if self.every_n is None and self.drop_from is None:
            raise ValueError("missing_rule 必须设置 every_n 或 drop_from 之一")
        if self.every_n is not None and self.every_n < 1:
            raise ValueError(f"every_n 必须 >= 1: {self.every_n}")
        if self.drop_from is not None and self.drop_from < 1:
            raise ValueError(f"drop_from 必须 >= 1: {self.drop_from}")


@dataclass(frozen=True)
class DelayedFrameSpec:
    """延迟到达帧：名义时刻 delay_ms 的帧在 delay_ms + extra_delay_ms 到达。"""

    can_id: int
    data: bytes
    delay_ms: int
    extra_delay_ms: int
    channel: int = 0
    is_fd: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", bytes(self.data))
        if self.delay_ms < 0:
            raise ValueError(f"delay_ms 不能为负: {self.delay_ms}")
        if self.extra_delay_ms < 0:
            raise ValueError(f"extra_delay_ms 不能为负: {self.extra_delay_ms}")


@dataclass(frozen=True)
class ErrorFrameSpec:
    """错误帧注入：在 at_ms（可按 period_ms 周期重复）产生一条 Mock 错误事件。

    Frame 是 frozen dataclass 且无错误标记字段，因此错误帧不产出 Frame，
    而是单独记录为 MockErrorEvent（不破坏 Frame 契约）。
    """

    can_id: int
    data: bytes
    at_ms: int = 0
    #: None 表示单次
    period_ms: int | None = None
    #: None 表示（周期模式下）无限重复
    count: int | None = None
    error_code: int = -4
    message: str = "注入的总线错误帧"
    channel: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", bytes(self.data))
        if self.at_ms < 0:
            raise ValueError(f"at_ms 不能为负: {self.at_ms}")
        if self.period_ms is not None and self.period_ms < 1:
            raise ValueError(f"period_ms 必须 >= 1: {self.period_ms}")
        if self.count is not None and self.count < 1:
            raise ValueError(f"count 必须 >= 1 或为 None: {self.count}")


@dataclass(frozen=True)
class SignalStep:
    """信号序列的一个台阶：虚拟时刻 at_ms 起载荷变为 data。"""

    at_ms: int
    data: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", bytes(self.data))
        if self.at_ms < 0:
            raise ValueError(f"at_ms 不能为负: {self.at_ms}")


@dataclass(frozen=True)
class SignalSequenceSpec:
    """某 can_id 的载荷随虚拟时间变化的序列（供信号类断言）。"""

    can_id: int
    steps: tuple[SignalStep, ...]
    channel: int = 0
    is_fd: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))


@dataclass(frozen=True)
class MockScenario:
    """Mock 场景配置：装配到虚拟时间线上的全部事件来源。

    确定性约定：seed 固定（默认 20240101）、jitter 默认 0；
    jitter_ms > 0 时仅影响周期帧的周期间隔（首帧由 start_delay_ms 精确决定），
    且每次装载时间线都以 seed 重建随机数发生器，保证同一场景多次运行得到
    完全相同的抖动序列。
    """

    periodic_frames: tuple[PeriodicFrameSpec, ...] = ()
    scheduled_frames: tuple[ScheduledFrameSpec, ...] = ()
    missing_rules: tuple[MissingRuleSpec, ...] = ()
    delayed_frames: tuple[DelayedFrameSpec, ...] = ()
    error_frames: tuple[ErrorFrameSpec, ...] = ()
    signal_sequences: tuple[SignalSequenceSpec, ...] = ()
    seed: int = 20240101
    jitter_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "periodic_frames", tuple(self.periodic_frames))
        object.__setattr__(self, "scheduled_frames", tuple(self.scheduled_frames))
        object.__setattr__(self, "missing_rules", tuple(self.missing_rules))
        object.__setattr__(self, "delayed_frames", tuple(self.delayed_frames))
        object.__setattr__(self, "error_frames", tuple(self.error_frames))
        object.__setattr__(self, "signal_sequences", tuple(self.signal_sequences))
        if self.jitter_ms < 0:
            raise ValueError(f"jitter_ms 不能为负: {self.jitter_ms}")
        if self.jitter_ms:
            for spec in self.periodic_frames:
                if spec.period_ms <= self.jitter_ms:
                    raise ValueError(
                        f"period_ms({spec.period_ms}) 必须大于 jitter_ms({self.jitter_ms})，"
                        "否则周期帧时刻可能倒退"
                    )


@dataclass
class FaultInjection:
    """故障注入开关（可运行中通过 adapter.faults 直接切换）。"""

    #: 设备离线：open_device 抛 MockDeviceOfflineError
    offline: bool = False
    #: 发送失败：SendResult.accepted=0 / error_code=-1
    send_fail: bool = False
    #: 接收失败：回调中断，抛 MockReceiveError 并停止该设备接收
    receive_fail: bool = False
    #: 句柄占用冲突：open_device 抛 MockHandleOccupiedError
    handle_occupied: bool = False
    #: 任务取消：run() 推进到取消点后抛 MockTaskCancelledError 并回滚内部状态
    task_cancel: bool = False
    #: 取消点（虚拟毫秒）；None 等价于 0（下一次 run 立即取消）
    task_cancel_at_ms: int | None = None


# ---------------------------------------------------------------------------
# 观测记录（全部为冻结 dataclass；日志 append-only，任务取消不追回）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimestampedFrame:
    """带虚拟时间的帧记录。device_id=None 表示时间线生成帧（尚未确定接收方）。"""

    device_id: str | None
    time_ms: int
    frame: Frame


@dataclass(frozen=True)
class MockErrorEvent:
    """错误帧注入事件：Mock 自有事件，不产出 Frame（不破坏 Frame 契约）。"""

    time_ms: int
    channel: int
    can_id: int
    data: bytes
    error_code: int
    message: str


@dataclass(frozen=True)
class SignalSample:
    """信号序列采样：虚拟时刻 can_id 载荷变为 data。"""

    time_ms: int
    can_id: int
    data: bytes


# 事件队列条目：(触发时刻, 全局序号, 类型, 场景条目下标, 出现次序)
_EventEntry = tuple[int, int, str, int, int]


class MockAdapter(DeviceSDKAdapter):
    """确定性 Mock/Offline 设备适配器。

    虚拟时间驱动：不开线程、不读系统时钟，全部事件在 advance/run 内同步触发，
    以保证同一场景多次运行的帧序列逐字节一致。
    """

    ADAPTER_ID = "mock"
    ADAPTER_VERSION = "1.0.0"

    def __init__(
        self,
        scenario: MockScenario | None = None,
        faults: FaultInjection | None = None,
    ) -> None:
        self._scenario: MockScenario = scenario if scenario is not None else MockScenario()
        self._faults: FaultInjection = faults if faults is not None else FaultInjection()
        # 内置 mock 设备清单
        self._devices: dict[str, DeviceInfo] = {BUILTIN_MOCK_DEVICE.device_id: BUILTIN_MOCK_DEVICE}
        self._capabilities: dict[str, dict[str, str]] = {
            BUILTIN_MOCK_DEVICE.device_id: dict(_DEFAULT_CAPABILITIES)
        }
        # 设备生命周期状态
        self._open: set[str] = set()
        self._configured: dict[str, dict[int, ChannelConfig]] = {}
        self._started: dict[str, set[int]] = {}
        self._receiving: set[str] = set()
        self._rx_callbacks: dict[str, Callable[[Frame], None]] = {}
        # 虚拟时钟与事件时间线
        self._now: int = 0
        self._armed: bool = False
        self._events: list[_EventEntry] = []
        self._seq: int = 0
        self._rng = random.Random(self._scenario.seed)
        self._task_active: bool = False
        # 观测日志（append-only 证据）
        self._frame_log: list[TimestampedFrame] = []
        self._sent: list[TimestampedFrame] = []
        self._received: list[TimestampedFrame] = []
        self._error_events: list[MockErrorEvent] = []
        self._signal_history: list[SignalSample] = []
        self._signal_values: dict[int, bytes] = {}

    # -- 配置与观测辅助 -------------------------------------------------------

    @property
    def scenario(self) -> MockScenario:
        return self._scenario

    @property
    def faults(self) -> FaultInjection:
        """故障注入开关（可在运行中直接修改）。"""
        return self._faults

    @property
    def now_ms(self) -> int:
        """当前虚拟时刻（毫秒）。"""
        return self._now

    @property
    def is_task_active(self) -> bool:
        """当前是否处于 run() 执行期间。"""
        return self._task_active

    @property
    def pending_event_count(self) -> int:
        """时间线上待触发的事件数。"""
        return len(self._events)

    @property
    def frame_log(self) -> tuple[TimestampedFrame, ...]:
        """场景时间线生成的全部帧（含未被任何设备接收的帧）。"""
        return tuple(self._frame_log)

    @property
    def sent_frames(self) -> tuple[TimestampedFrame, ...]:
        """send_frame 成功发送的帧（发送时刻为虚拟时间）。"""
        return tuple(self._sent)

    @property
    def received_frames(self) -> tuple[TimestampedFrame, ...]:
        """已交付给接收回调的帧。"""
        return tuple(self._received)

    @property
    def signal_history(self) -> tuple[SignalSample, ...]:
        """信号序列的全部台阶记录。"""
        return tuple(self._signal_history)

    def signal_value(self, can_id: int) -> bytes | None:
        """某 can_id 在当前虚拟时刻的信号载荷（未出现过则为 None）。"""
        return self._signal_values.get(can_id)

    def drain_error_events(self) -> tuple[MockErrorEvent, ...]:
        """取走并清空已注入的错误帧事件。"""
        events = tuple(self._error_events)
        self._error_events.clear()
        return events

    def add_device(
        self, info: DeviceInfo, capabilities: Mapping[str, str] | None = None
    ) -> None:
        """注册自定义 mock 设备（buses 约定放在 info.extra["buses"]）。"""
        caps = dict(_DEFAULT_CAPABILITIES if capabilities is None else capabilities)
        for capability, level in caps.items():
            if level not in ALL_EVIDENCE:
                raise ValueError(f"非法证据等级 {level!r}（能力 {capability!r}）")
        self._devices[info.device_id] = info
        self._capabilities[info.device_id] = caps

    def is_open(self, device_id: str) -> bool:
        return device_id in self._open

    def is_receiving(self, device_id: str) -> bool:
        return device_id in self._receiving

    def is_channel_started(self, device_id: str, channel_index: int) -> bool:
        return channel_index in self._started.get(device_id, set())

    # -- 契约实现：设备与通道生命周期 ----------------------------------------

    @property
    def adapter_id(self) -> str:
        return self.ADAPTER_ID

    @property
    def adapter_version(self) -> str:
        return self.ADAPTER_VERSION

    def discover_devices(self) -> Sequence[DeviceInfo]:
        """扫描 mock 设备（内存清单，恒非空）。"""
        return list(self._devices.values())

    def get_device_info(self, device_id: str) -> DeviceInfo:
        if device_id not in self._devices:
            raise KeyError(f"设备不存在: {device_id}")
        return self._devices[device_id]

    def open_device(self, device_id: str) -> None:
        self.get_device_info(device_id)
        if self._faults.offline:
            raise MockDeviceOfflineError(f"注入故障：设备离线，无法打开 {device_id}")
        if self._faults.handle_occupied:
            raise MockHandleOccupiedError(f"注入故障：设备句柄被占用 {device_id}")
        if device_id in self._open:
            raise MockHandleOccupiedError(f"设备已被占用: {device_id}")
        self._open.add(device_id)
        if not self._armed:
            # 首次打开时装载场景时间线（事件相对当前虚拟时刻排布）
            self._arm_scenario()

    def close_device(self, device_id: str) -> None:
        """关闭设备并释放句柄；幂等，任何状态下都可调用。"""
        self._open.discard(device_id)
        self._receiving.discard(device_id)
        self._rx_callbacks.pop(device_id, None)
        self._configured.pop(device_id, None)
        self._started.pop(device_id, None)
        if not self._open and self._armed:
            self._disarm_scenario()

    def configure_channel(self, device_id: str, config: ChannelConfig) -> None:
        self._require_open(device_id)
        info = self.get_device_info(device_id)
        if config.bus_type not in ("CAN", "CAN_FD"):
            raise ValueError(f"不支持的总线类型: {config.bus_type}")
        if info.channel_count is not None and not (
            0 <= config.channel_index < info.channel_count
        ):
            raise ValueError(f"通道序号越界: {config.channel_index}")
        self._configured.setdefault(device_id, {})[config.channel_index] = config

    def start_channel(self, device_id: str, channel_index: int) -> None:
        self._require_open(device_id)
        channels = self._configured.get(device_id, {})
        if channel_index not in channels:
            raise RuntimeError("通道未配置")
        self._started.setdefault(device_id, set()).add(channel_index)

    def stop_channel(self, device_id: str, channel_index: int) -> None:
        """停止通道；幂等，未启动/未打开时为无操作。"""
        self._started.get(device_id, set()).discard(channel_index)

    # -- 契约实现：收发 --------------------------------------------------------

    def send_frame(self, device_id: str, frame: Frame) -> SendResult:
        self._require_open(device_id)
        if frame.channel not in self._started.get(device_id, set()):
            raise RuntimeError(f"通道未启动: {frame.channel}")
        if self._faults.send_fail:
            return SendResult(requested=1, accepted=0, error_code=-1)
        self._sent.append(
            TimestampedFrame(device_id=device_id, time_ms=self._now, frame=frame)
        )
        return SendResult(requested=1, accepted=1, tx_echo=frame)

    def start_receiving(self, device_id: str, callback: Callable[[Frame], None]) -> None:
        self._require_open(device_id)
        self._rx_callbacks[device_id] = callback
        self._receiving.add(device_id)

    def stop_receiving(self, device_id: str) -> None:
        """停止接收；幂等。"""
        self._receiving.discard(device_id)
        self._rx_callbacks.pop(device_id, None)

    # -- 契约实现：能力与急停 --------------------------------------------------

    def device_capabilities(self, device_id: str) -> Mapping[str, Any]:
        info = self.get_device_info(device_id)
        buses = list(info.extra.get("buses", ["CAN", "CAN_FD"]))
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "device_id": device_id,
            "model": info.model,
            "buses": buses,
            "channel_count": info.channel_count,
            "evidence": dict(self._capabilities.get(device_id, {})),
        }

    def is_capable(self, device_id: str, capability: str) -> bool:
        """能力判断；未知能力返回 False（未知 ≠ 支持）。"""
        self.get_device_info(device_id)
        level = self._capabilities.get(device_id, {}).get(capability, "unknown")
        return level in CAPABLE_EVIDENCE

    def emergency_stop(self, device_id: str) -> EmergencyStopReport:
        """紧急停止：停发送、清空时间线队列、注销回调、复位通道、关闭设备。"""
        self.get_device_info(device_id)
        cleared_events = len(self._events)
        was_open = device_id in self._open
        had_callback = device_id in self._rx_callbacks
        stopped_channels = sorted(self._started.get(device_id, set()))
        self._open.discard(device_id)
        self._receiving.discard(device_id)
        self._rx_callbacks.pop(device_id, None)
        self._started.pop(device_id, None)
        self._configured.pop(device_id, None)
        # 场景时间线为适配器级：急停清空全部待触发事件（内存操作必然完成）
        self._disarm_scenario()
        return EmergencyStopReport(
            sends_stopped=True,
            queues_cleared=True,
            channels_reset=True,
            device_closed=True,
            details={
                "device_id": device_id,
                "cleared_events": cleared_events,
                "callback_unregistered": had_callback,
                "stopped_channels": stopped_channels,
                "was_open": was_open,
                "virtual_time_ms": self._now,
            },
        )

    # -- 虚拟时间 --------------------------------------------------------------

    def advance(self, duration_ms: int) -> int:
        """手动步进虚拟时钟，按序触发到期事件；返回新的虚拟时刻。"""
        target = self._validated_target(duration_ms)
        self._step_to(target)
        return self._now

    def run(self, duration_ms: int) -> int:
        """运行一段虚拟时间：步进并触发到期事件；返回新的虚拟时间。

        支持任务取消故障：推进到取消点（含该时刻事件）后抛
        MockTaskCancelledError，并把虚拟时钟、事件队列、随机数状态与运行态
        标志回滚到本次 run 开始前（已交付帧与日志不追回）。
        """
        target = self._validated_target(duration_ms)
        self._task_active = True
        snapshot = self._snapshot() if self._faults.task_cancel else None
        try:
            if snapshot is not None:
                cancel_at = (
                    self._faults.task_cancel_at_ms
                    if self._faults.task_cancel_at_ms is not None
                    else 0
                )
                if target >= cancel_at:
                    self._step_to(max(self._now, cancel_at))
                    raise MockTaskCancelledError(
                        f"任务在虚拟时刻 {self._now}ms 被取消（取消点 {cancel_at}ms）"
                    )
            self._step_to(target)
            return self._now
        except MockTaskCancelledError:
            if snapshot is not None:
                self._restore(snapshot)
            raise
        finally:
            self._task_active = False

    def _validated_target(self, duration_ms: int) -> int:
        if duration_ms < 0:
            raise ValueError(f"时长不能为负: {duration_ms}")
        return self._now + duration_ms

    # -- 时间线装配与事件分发 ---------------------------------------------------

    def _arm_scenario(self) -> None:
        """把场景事件装载到时间线（相对当前虚拟时刻；以 seed 重建随机数流）。"""
        self._events.clear()
        self._seq = 0
        self._rng = random.Random(self._scenario.seed)
        base = self._now
        sc = self._scenario
        for index, spec in enumerate(sc.periodic_frames):
            # 首帧由 start_delay 决定（精确）；抖动只作用于后续周期间隔
            self._push(base + spec.start_delay_ms, "periodic", index, 1)
        for index, spec in enumerate(sc.scheduled_frames):
            self._push(base + spec.delay_ms, "scheduled", index, 1)
        for index, spec in enumerate(sc.delayed_frames):
            self._push(base + spec.delay_ms + spec.extra_delay_ms, "delayed", index, 1)
        for index, spec in enumerate(sc.error_frames):
            self._push(base + spec.at_ms, "error", index, 1)
        for index, spec in enumerate(sc.signal_sequences):
            for order, step in enumerate(spec.steps, start=1):
                self._push(base + step.at_ms, "signal", index, order)
        heapq.heapify(self._events)
        self._armed = True

    def _disarm_scenario(self) -> None:
        self._events.clear()
        self._armed = False

    def _push(self, time_ms: int, kind: str, spec_index: int, occurrence: int) -> None:
        self._seq += 1
        heapq.heappush(self._events, (time_ms, self._seq, kind, spec_index, occurrence))

    def _jitter_offset(self) -> int:
        """周期间隔抖动偏移：jitter=0 时恒为 0，否则来自 seeded random。"""
        jitter = self._scenario.jitter_ms
        if not jitter:
            return 0
        return self._rng.randint(-jitter, jitter)

    def _step_to(self, target: int) -> None:
        """把虚拟时钟推进到 target，并按（时间, 序号）顺序触发全部到期事件。"""
        while self._events and self._events[0][0] <= target:
            time_ms, _seq, kind, spec_index, occurrence = heapq.heappop(self._events)
            if time_ms > self._now:
                self._now = time_ms
            self._fire(kind, spec_index, occurrence, time_ms)
        if target > self._now:
            self._now = target

    def _fire(self, kind: str, spec_index: int, occurrence: int, time_ms: int) -> None:
        sc = self._scenario
        if kind == "periodic":
            spec = sc.periodic_frames[spec_index]
            if not self._is_missing(spec, occurrence):
                self._emit_frame(time_ms, spec.target_channel, spec.can_id, spec.data, spec.is_fd)
            next_occurrence = occurrence + 1
            if spec.count is None or next_occurrence <= spec.count:
                # 惰性排布下一次触发（jitter 每次独立抽样，流由 seed 决定）
                self._push(
                    time_ms + spec.period_ms + self._jitter_offset(),
                    "periodic",
                    spec_index,
                    next_occurrence,
                )
        elif kind == "scheduled":
            spec = sc.scheduled_frames[spec_index]
            self._emit_frame(time_ms, spec.channel, spec.can_id, spec.data, spec.is_fd)
        elif kind == "delayed":
            spec = sc.delayed_frames[spec_index]
            self._emit_frame(time_ms, spec.channel, spec.can_id, spec.data, spec.is_fd)
        elif kind == "signal":
            spec = sc.signal_sequences[spec_index]
            step = spec.steps[occurrence - 1]
            self._signal_values[spec.can_id] = step.data
            self._signal_history.append(
                SignalSample(time_ms=time_ms, can_id=spec.can_id, data=step.data)
            )
            self._emit_frame(time_ms, spec.channel, spec.can_id, step.data, spec.is_fd)
        elif kind == "error":
            spec = sc.error_frames[spec_index]
            self._error_events.append(
                MockErrorEvent(
                    time_ms=time_ms,
                    channel=spec.channel,
                    can_id=spec.can_id,
                    data=spec.data,
                    error_code=spec.error_code,
                    message=spec.message,
                )
            )
            if spec.period_ms is not None:
                next_occurrence = occurrence + 1
                if spec.count is None or next_occurrence <= spec.count:
                    self._push(time_ms + spec.period_ms, "error", spec_index, next_occurrence)

    def _is_missing(self, spec: PeriodicFrameSpec, occurrence: int) -> bool:
        """按缺帧规则判断该次周期触发是否被丢弃（次序从 1 开始）。"""
        for rule in self._scenario.missing_rules:
            if rule.can_id != spec.can_id:
                continue
            if rule.channel is not None and rule.channel != spec.target_channel:
                continue
            if rule.every_n is not None and occurrence % rule.every_n == 0:
                return True
            if rule.drop_from is not None and occurrence >= rule.drop_from:
                return True
        return False

    def _emit_frame(
        self, time_ms: int, channel: int, can_id: int, data: bytes, is_fd: bool
    ) -> Frame:
        """生成一帧（时间戳全部来自虚拟时钟）并投递给当前接收中的设备。"""
        frame = Frame(
            channel=channel,
            can_id=can_id,
            data=bytes(data),
            is_fd=is_fd,
            device_timestamp=time_ms,
            device_timestamp_unit="ms",
            host_monotonic_ns=time_ms * 1_000_000,
            # Mock 不写墙上时间：真实时间会破坏确定性
            wall_clock=None,
        )
        self._frame_log.append(TimestampedFrame(device_id=None, time_ms=time_ms, frame=frame))
        self._deliver(frame)
        return frame

    def _deliver(self, frame: Frame) -> None:
        """把帧投递给全部接收中的设备（按 device_id 排序，保证顺序确定）。"""
        for device_id in sorted(self._receiving):
            callback = self._rx_callbacks.get(device_id)
            if callback is None:
                continue
            if self._faults.receive_fail:
                # 接收失败：回调中断，中止本次步进，该设备停止接收
                self._receiving.discard(device_id)
                self._rx_callbacks.pop(device_id, None)
                raise MockReceiveError(f"接收失败：设备 {device_id} 的回调中断")
            time_ms = (
                frame.device_timestamp if frame.device_timestamp is not None else self._now
            )
            self._received.append(
                TimestampedFrame(device_id=device_id, time_ms=time_ms, frame=frame)
            )
            callback(frame)

    # -- 任务取消的状态快照与回滚 ------------------------------------------------

    def _snapshot(self) -> dict[str, Any]:
        return {
            "now": self._now,
            "seq": self._seq,
            "events": list(self._events),
            "rng_state": self._rng.getstate(),
            "signal_values": dict(self._signal_values),
            "open": set(self._open),
            "configured": {d: dict(c) for d, c in self._configured.items()},
            "started": {d: set(s) for d, s in self._started.items()},
            "receiving": set(self._receiving),
            "callbacks": dict(self._rx_callbacks),
        }

    def _restore(self, snapshot: Mapping[str, Any]) -> None:
        self._now = snapshot["now"]
        self._seq = snapshot["seq"]
        self._events = list(snapshot["events"])
        self._rng.setstate(snapshot["rng_state"])
        self._signal_values = dict(snapshot["signal_values"])
        self._open = set(snapshot["open"])
        self._configured = dict(snapshot["configured"])
        self._started = dict(snapshot["started"])
        self._receiving = set(snapshot["receiving"])
        self._rx_callbacks = dict(snapshot["callbacks"])

    # -- 内部校验 ---------------------------------------------------------------

    def _require_open(self, device_id: str) -> None:
        if device_id not in self._open:
            raise RuntimeError("设备未打开")

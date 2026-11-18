"""MOCK-01 测试：确定性 Mock Adapter 的场景时间线、故障注入与契约完整性。"""

from __future__ import annotations

import inspect

import pytest

from zlg_ai_test_engine.domain.plugins.contracts import (
    ChannelConfig,
    DeviceInfo,
    DeviceSDKAdapter,
    Frame,
)
from zlg_ai_test_engine.plugins.mock_adapter import (
    DelayedFrameSpec,
    ErrorFrameSpec,
    FaultInjection,
    MockAdapter,
    MockDeviceOfflineError,
    MockHandleOccupiedError,
    MockReceiveError,
    MockScenario,
    MockTaskCancelledError,
    MissingRuleSpec,
    PeriodicFrameSpec,
    ScheduledFrameSpec,
    SignalSequenceSpec,
    SignalStep,
)


def _open_and_run(scenario: MockScenario, duration_ms: int = 1000, faults=None):
    """新建适配器 → 打开→配置→启动通道→接收→运行；返回（适配器, 接收列表）。"""
    adapter = MockAdapter(scenario, faults)
    received: list[Frame] = []
    adapter.open_device("mock-0")
    adapter.configure_channel("mock-0", ChannelConfig(0, "CAN", 500_000))
    adapter.start_channel("mock-0", 0)
    adapter.start_receiving("mock-0", received.append)
    adapter.run(duration_ms)
    return adapter, received


def _sequence(entries) -> list[tuple[int, int, bytes]]:
    """帧记录 → (虚拟时间, can_id, data) 序列，用于逐字节比较。"""
    return [(e.time_ms, e.frame.can_id, e.frame.data) for e in entries]


# ---------------------------------------------------------------------------
# 验收 1：确定性 —— 同一 scenario 运行 2 次，帧序列逐字节相同
# ---------------------------------------------------------------------------

DETERMINISM_SCENARIO = MockScenario(
    periodic_frames=(
        PeriodicFrameSpec(can_id=0x100, data=b"\x01\x02", period_ms=100, count=5),
    ),
    scheduled_frames=(
        ScheduledFrameSpec(delay_ms=50, can_id=0x200, data=b"\xaa"),
    ),
    delayed_frames=(
        DelayedFrameSpec(can_id=0x300, data=b"\xbb", delay_ms=200, extra_delay_ms=50),
    ),
    error_frames=(
        ErrorFrameSpec(can_id=0x0, data=b"", at_ms=80),
    ),
    signal_sequences=(
        SignalSequenceSpec(
            can_id=0x400,
            steps=(SignalStep(0, b"\x01"), SignalStep(150, b"\x02"), SignalStep(300, b"\x03")),
        ),
    ),
)


class TestDeterminism:
    def test_same_scenario_two_runs_identical(self):
        adapter1, received1 = _open_and_run(DETERMINISM_SCENARIO)
        adapter2, received2 = _open_and_run(DETERMINISM_SCENARIO)
        seq1 = _sequence(adapter1.frame_log)
        seq2 = _sequence(adapter2.frame_log)
        # 周期5 + 定时1 + 延迟1 + 信号3 = 10 帧（错误帧不产出 Frame）
        assert len(seq1) == 10
        # 时间戳 / can_id / data 逐字节相同
        assert seq1 == seq2
        assert _sequence(adapter1.received_frames) == _sequence(adapter2.received_frames)
        assert [e.frame.device_timestamp for e in adapter1.frame_log] == [
            e.frame.device_timestamp for e in adapter2.frame_log
        ]
        # 发送结果一致
        probe = Frame(channel=0, can_id=0x7FF, data=b"\x10\x20")
        result1 = adapter1.send_frame("mock-0", probe)
        result2 = adapter2.send_frame("mock-0", probe)
        assert (result1.requested, result1.accepted, result1.error_code) == (
            result2.requested,
            result2.accepted,
            result2.error_code,
        )
        assert adapter1.sent_frames == adapter2.sent_frames

    def test_seeded_jitter_deterministic_and_effective(self):
        def scenario(jitter: int) -> MockScenario:
            return MockScenario(
                periodic_frames=(
                    PeriodicFrameSpec(can_id=0x60, data=b"\x06", period_ms=100, count=10),
                ),
                seed=7,
                jitter_ms=jitter,
            )

        a1, _ = _open_and_run(scenario(20), duration_ms=2000)
        a2, _ = _open_and_run(scenario(20), duration_ms=2000)
        jittered = _sequence(a1.frame_log)
        assert jittered == _sequence(a2.frame_log)
        # 抖动确实生效：与 jitter=0 的精确时刻不同
        a0, _ = _open_and_run(scenario(0), duration_ms=2000)
        assert [t for t, _, _ in jittered] != [t for t, _, _ in _sequence(a0.frame_log)]
        # 每个周期间隔的偏移受 jitter_ms 约束
        jittered_times = [t for t, _, _ in jittered]
        increments = [b - a for a, b in zip(jittered_times, jittered_times[1:])]
        assert all(100 - 20 <= d <= 100 + 20 for d in increments)


# ---------------------------------------------------------------------------
# 验收 2：周期帧精确触发 / 缺帧规则 / 延迟帧到达
# ---------------------------------------------------------------------------


class TestScenarioTimeline:
    def test_periodic_exact_period_and_start_delay(self):
        scenario = MockScenario(
            periodic_frames=(
                PeriodicFrameSpec(
                    can_id=0x10,
                    data=b"\x01",
                    period_ms=25,
                    start_delay_ms=10,
                    count=4,
                ),
            )
        )
        adapter, received = _open_and_run(scenario, duration_ms=1000)
        times = [e.time_ms for e in adapter.frame_log]
        # 首帧在 start_delay 后，之后每 period_ms 一次，精确无漂移
        assert times == [10, 35, 60, 85]
        assert all(b - a == 25 for a, b in zip(times, times[1:]))
        assert [f.can_id for f in received] == [0x10] * 4

    def test_missing_rule_every_n(self):
        scenario = MockScenario(
            periodic_frames=(
                PeriodicFrameSpec(
                    can_id=0x20, data=b"\x02", period_ms=100, start_delay_ms=100, count=6
                ),
            ),
            missing_rules=(MissingRuleSpec(can_id=0x20, every_n=3),),
        )
        adapter, _ = _open_and_run(scenario, duration_ms=1000)
        # 第 3、6 次被丢弃
        assert [e.time_ms for e in adapter.frame_log] == [100, 200, 400, 500]

    def test_missing_rule_drop_from(self):
        scenario = MockScenario(
            periodic_frames=(
                PeriodicFrameSpec(
                    can_id=0x20, data=b"\x02", period_ms=100, start_delay_ms=100, count=6
                ),
            ),
            missing_rules=(MissingRuleSpec(can_id=0x20, drop_from=4),),
        )
        adapter, _ = _open_and_run(scenario, duration_ms=1000)
        # 从第 4 次开始全部丢弃
        assert [e.time_ms for e in adapter.frame_log] == [100, 200, 300]

    def test_scheduled_and_delayed_arrival(self):
        scenario = MockScenario(
            scheduled_frames=(ScheduledFrameSpec(delay_ms=50, can_id=0x31, data=b"\x0a"),),
            delayed_frames=(
                DelayedFrameSpec(can_id=0x32, data=b"\x0b", delay_ms=200, extra_delay_ms=50),
            ),
        )
        adapter, received = _open_and_run(scenario, duration_ms=1000)
        # 定时帧按 delay 触发；延迟帧按 delay+extra_delay 到达
        assert [(e.time_ms, e.frame.can_id) for e in adapter.frame_log] == [
            (50, 0x31),
            (250, 0x32),
        ]
        assert [(e.time_ms, e.frame.can_id) for e in adapter.received_frames] == [
            (50, 0x31),
            (250, 0x32),
        ]
        assert [f.can_id for f in received] == [0x31, 0x32]

    def test_error_frames_surface_as_mock_events(self):
        scenario = MockScenario(
            error_frames=(
                ErrorFrameSpec(
                    can_id=0x701,
                    data=b"\x01",
                    at_ms=40,
                    period_ms=30,
                    count=3,
                    error_code=-9,
                    message="CRC 错误",
                ),
            )
        )
        adapter = MockAdapter(scenario)
        adapter.open_device("mock-0")
        adapter.run(1000)
        events = adapter.drain_error_events()
        # 错误帧按时刻/周期注入
        assert [e.time_ms for e in events] == [40, 70, 100]
        assert all(e.error_code == -9 for e in events)
        assert all(e.message == "CRC 错误" for e in events)
        # 错误帧不破坏 Frame 契约：不产出 Frame，事件可取走即清空
        assert adapter.frame_log == ()
        assert adapter.drain_error_events() == ()

    def test_signal_sequence_tracks_virtual_time(self):
        scenario = MockScenario(
            signal_sequences=(
                SignalSequenceSpec(
                    can_id=0x500,
                    steps=(
                        SignalStep(0, b"\x00"),
                        SignalStep(100, b"\x01"),
                        SignalStep(200, b"\x02"),
                    ),
                ),
            )
        )
        adapter = MockAdapter(scenario)
        received: list[Frame] = []
        adapter.open_device("mock-0")
        adapter.start_receiving("mock-0", received.append)
        adapter.run(50)
        assert adapter.signal_value(0x500) == b"\x00"
        adapter.run(60)  # 虚拟时刻 110
        assert adapter.signal_value(0x500) == b"\x01"
        adapter.run(100)  # 虚拟时刻 210
        assert adapter.signal_value(0x500) == b"\x02"
        assert [s.time_ms for s in adapter.signal_history] == [0, 100, 200]
        assert [f.data for f in received] == [b"\x00", b"\x01", b"\x02"]
        # advance 与 run 一样触发到期事件
        assert adapter.now_ms == 210
        adapter.advance(0)
        assert adapter.now_ms == 210


# ---------------------------------------------------------------------------
# 验收 3：故障注入
# ---------------------------------------------------------------------------


class TestFaultInjection:
    def test_offline_open_raises(self):
        adapter = MockAdapter(MockScenario(), FaultInjection(offline=True))
        with pytest.raises(MockDeviceOfflineError, match="离线"):
            adapter.open_device("mock-0")
        assert not adapter.is_open("mock-0")

    def test_send_failure_result(self):
        adapter = MockAdapter(MockScenario(), FaultInjection(send_fail=True))
        adapter.open_device("mock-0")
        adapter.configure_channel("mock-0", ChannelConfig(0, "CAN", 500_000))
        adapter.start_channel("mock-0", 0)
        result = adapter.send_frame("mock-0", Frame(channel=0, can_id=0x123, data=b"\x01"))
        assert result.requested == 1
        assert result.accepted == 0
        assert result.error_code == -1
        assert adapter.sent_frames == ()

    def test_receive_failure_interrupts_callback(self):
        scenario = MockScenario(
            periodic_frames=(PeriodicFrameSpec(can_id=0x11, data=b"\x01", period_ms=100),)
        )
        adapter = MockAdapter(scenario, FaultInjection(receive_fail=True))
        adapter.open_device("mock-0")
        adapter.start_receiving("mock-0", lambda frame: None)
        with pytest.raises(MockReceiveError, match="中断"):
            adapter.run(500)
        # 回调中断后该设备停止接收
        assert adapter.is_receiving("mock-0") is False

    def test_handle_occupied_conflict(self):
        adapter = MockAdapter()
        adapter.open_device("mock-0")
        # 本适配器重复打开 → 句柄占用
        with pytest.raises(MockHandleOccupiedError, match="占用"):
            adapter.open_device("mock-0")
        adapter.close_device("mock-0")
        # 注入故障：外部进程占用句柄
        busy = MockAdapter(MockScenario(), FaultInjection(handle_occupied=True))
        with pytest.raises(MockHandleOccupiedError, match="占用"):
            busy.open_device("mock-0")
        assert not busy.is_open("mock-0")

    def test_task_cancel_restores_state(self):
        scenario = MockScenario(
            periodic_frames=(
                PeriodicFrameSpec(
                    can_id=0x70, data=b"\x07", period_ms=100, start_delay_ms=100, count=10
                ),
            )
        )
        faults = FaultInjection(task_cancel=True, task_cancel_at_ms=150)
        adapter = MockAdapter(scenario, faults)
        received: list[Frame] = []
        adapter.open_device("mock-0")
        adapter.configure_channel("mock-0", ChannelConfig(0, "CAN", 500_000))
        adapter.start_channel("mock-0", 0)
        adapter.start_receiving("mock-0", received.append)
        pre_now = adapter.now_ms
        pre_pending = adapter.pending_event_count
        with pytest.raises(MockTaskCancelledError, match="取消"):
            adapter.run(1000)
        # 状态恢复：虚拟时钟与事件队列回滚，运行态标志一致
        assert adapter.now_ms == pre_now
        assert adapter.pending_event_count == pre_pending
        assert adapter.is_task_active is False
        assert adapter.is_open("mock-0")
        assert adapter.is_receiving("mock-0")
        assert adapter.is_channel_started("mock-0", 0)
        # 已交付帧属于证据（append-only），不追回
        assert len(received) == 1
        # 关闭取消故障后可完整运行并正常收尾
        adapter.faults.task_cancel = False
        adapter.run(1000)
        assert adapter.now_ms == 1000
        assert [e.time_ms for e in adapter.frame_log] == [
            100, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000,
        ]
        adapter.stop_receiving("mock-0")
        adapter.stop_channel("mock-0", 0)
        adapter.close_device("mock-0")

    def test_emergency_stop_all_true_and_reopen(self):
        scenario = MockScenario(
            periodic_frames=(
                PeriodicFrameSpec(
                    can_id=0x40, data=b"\x04", period_ms=100, start_delay_ms=100
                ),
            )
        )
        adapter = MockAdapter(scenario)
        received: list[Frame] = []
        adapter.open_device("mock-0")
        adapter.configure_channel("mock-0", ChannelConfig(0, "CAN", 500_000))
        adapter.start_channel("mock-0", 0)
        adapter.start_receiving("mock-0", received.append)
        adapter.run(350)
        assert len(adapter.frame_log) == 3  # 100 / 200 / 300
        assert adapter.pending_event_count >= 1

        report = adapter.emergency_stop("mock-0")
        # 逐项清理全部成功
        assert report.sends_stopped
        assert report.queues_cleared
        assert report.channels_reset
        assert report.device_closed
        assert report.details["cleared_events"] >= 1
        assert report.details["callback_unregistered"] is True
        assert report.details["stopped_channels"] == [0]
        # 急停后无残留
        assert not adapter.is_open("mock-0")
        assert not adapter.is_receiving("mock-0")
        assert adapter.pending_event_count == 0

        # 急停后可重新打开，时间线重新装载
        adapter.open_device("mock-0")
        adapter.configure_channel("mock-0", ChannelConfig(0, "CAN", 500_000))
        adapter.start_channel("mock-0", 0)
        adapter.start_receiving("mock-0", received.append)
        before = len(adapter.frame_log)
        adapter.run(350)  # 虚拟时刻 350 → 700：新时间线 450 / 550 / 650
        assert len(adapter.frame_log) - before == 3
        adapter.close_device("mock-0")


# ---------------------------------------------------------------------------
# 验收 4/5：契约完整性与能力证据
# ---------------------------------------------------------------------------


class TestContractCompleteness:
    def test_instantiable_full_lifecycle(self):
        # ABC 全部抽象方法已实现：可直接实例化
        assert not inspect.isabstract(MockAdapter)
        scenario = MockScenario(
            scheduled_frames=(ScheduledFrameSpec(delay_ms=10, can_id=0x111, data=b"\x01"),)
        )
        adapter = MockAdapter(scenario)
        assert isinstance(adapter, DeviceSDKAdapter)
        assert adapter.adapter_id == "mock"
        received: list[Frame] = []
        # open → configure → start → send → receive → stop → close
        adapter.open_device("mock-0")
        adapter.configure_channel("mock-0", ChannelConfig(0, "CAN", 500_000))
        adapter.start_channel("mock-0", 0)
        result = adapter.send_frame("mock-0", Frame(channel=0, can_id=0x123, data=b"\x02"))
        assert result.accepted == 1 and result.tx_echo is not None
        adapter.start_receiving("mock-0", received.append)
        adapter.run(20)
        assert [(e.time_ms, e.frame.can_id) for e in adapter.received_frames] == [(10, 0x111)]
        assert len(received) == 1
        adapter.stop_receiving("mock-0")
        adapter.stop_channel("mock-0", 0)
        adapter.close_device("mock-0")
        assert not adapter.is_open("mock-0")

    def test_unknown_capability_is_not_capable(self):
        adapter = MockAdapter()
        # 从未出现的能力 → False（未知 ≠ 支持）
        assert adapter.is_capable("mock-0", "never_seen_capability") is False
        # 显式声明 unknown 的能力 → False
        assert adapter.is_capable("mock-0", "hardware_periodic_tx") is False
        # 有证据的能力 → True
        assert adapter.is_capable("mock-0", "basic_tx") is True
        caps = adapter.device_capabilities("mock-0")
        assert caps["evidence"]["hardware_periodic_tx"] == "unknown"
        assert set(caps["evidence"].values()) <= {
            "official_declared",
            "sdk_visible",
            "verified_on_device",
            "unknown",
        }

    def test_builtin_and_custom_devices(self):
        adapter = MockAdapter()
        devices = adapter.discover_devices()
        assert [d.device_id for d in devices] == ["mock-0"]
        builtin = adapter.get_device_info("mock-0")
        assert builtin.model == "MOCK-CANFD-200U"
        assert builtin.channel_count == 2
        caps = adapter.device_capabilities("mock-0")
        assert caps["buses"] == ["CAN", "CAN_FD"]
        adapter.add_device(
            DeviceInfo(device_id="mock-1", model="MOCK-STD-100", channel_count=1,
                       extra={"buses": ["CAN"]}),
            capabilities={"basic_tx": "sdk_visible", "basic_rx": "sdk_visible"},
        )
        assert [d.device_id for d in adapter.discover_devices()] == ["mock-0", "mock-1"]
        assert adapter.device_capabilities("mock-1")["buses"] == ["CAN"]
        assert adapter.is_capable("mock-1", "basic_tx") is True
        assert adapter.is_capable("mock-1", "can_fd") is False
        with pytest.raises(KeyError):
            adapter.get_device_info("no-such-device")

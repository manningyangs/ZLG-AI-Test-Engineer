# Mock/Offline Adapter v1（MOCK-01）

`src/zlg_ai_test_engine/plugins/mock_adapter.py` 中的 `MockAdapter` 是一个确定性、
完全离线的 `DeviceSDKAdapter` 实现：不加载 ZLG DLL、不联网、不触碰真实设备，
仅靠虚拟时钟驱动场景事件，用于无硬件环境下的开发、测试与演示。

## 场景配置

`MockAdapter(scenario)` 注入冻结 dataclass `MockScenario`（`MockAdapter()` 使用空场景）：

| 字段 | 条目类型 | 含义 |
| --- | --- | --- |
| `periodic_frames` | `PeriodicFrameSpec(can_id, data, period_ms, start_delay_ms, count=None, target_channel, is_fd)` | 周期帧：首帧在 `start_delay_ms` 后触发，此后每 `period_ms` 一次；`count=None` 表示无限 |
| `scheduled_frames` | `ScheduledFrameSpec(delay_ms, can_id, data, channel, is_fd)` | 单次定时帧 |
| `missing_rules` | `MissingRuleSpec(can_id, every_n=None, drop_from=None, channel=None)` | 缺帧规则：每 N 次丢 1 次 / 从第 M 次开始丢弃（按出现次序，1 起），用于报文超时/丢帧场景 |
| `delayed_frames` | `DelayedFrameSpec(can_id, data, delay_ms, extra_delay_ms, channel, is_fd)` | 延迟到达帧：在 `delay_ms + extra_delay_ms` 到达 |
| `error_frames` | `ErrorFrameSpec(can_id, data, at_ms, period_ms, count, error_code, message, channel)` | 错误帧注入：按时刻/周期产生 `MockErrorEvent`（见下） |
| `signal_sequences` | `SignalSequenceSpec(can_id, steps=(SignalStep(at_ms, data), ...), channel, is_fd)` | 某 can_id 载荷随虚拟时间变化的序列，可用 `signal_value(can_id)` 与 `signal_history` 断言 |

- 时间线在 `open_device` 首次打开时装配（事件相对当前虚拟时刻排布）；
  `close_device`（最后一个设备关闭）与 `emergency_stop` 清空时间线。
- 帧记录：`frame_log`（时间线生成的全部帧）、`sent_frames`（`send_frame` 成功记录）、
  `received_frames`（已交付给接收回调的帧）、`drain_error_events()`（错误帧事件）、
  `signal_history`。记录均为 append-only 证据。

### 错误帧为什么不产出 Frame

`Frame` 是 frozen dataclass 且没有错误标记字段，直接扩展会破坏契约。
因此错误帧单独以 `MockErrorEvent(time_ms, channel, can_id, data, error_code, message)`
形式记录，通过 `drain_error_events()` 取走（取走即清空），不会混入 `frame_log`。

## 虚拟时间与确定性保证

- 虚拟时钟：`advance(ms)` 手动步进、`run(duration_ms)` 运行一段虚拟时间，
  二者均按（触发时刻, 装载序号）全序严格触发到期事件；二者都拒绝负数时长。
  realtime 模式为任务书可选项，本版本未实现。
- 时间戳全部来自虚拟时钟：`device_timestamp` = 虚拟毫秒（unit="ms"）、
  `host_monotonic_ns` = 虚拟毫秒换算、`wall_clock` 恒为 `None`（真实墙上时间会破坏确定性）。
- 随机抖动只来自显式 seeded random：`MockScenario.seed` 默认固定、`jitter_ms` 默认 0；
  `jitter_ms > 0` 时仅影响周期帧的**周期间隔**（首帧由 `start_delay_ms` 精确决定），
  且每次装载时间线都以 `seed` 重建随机数发生器。
- 不开线程、不读系统时钟：事件在 `advance`/`run` 内同步分发，接收回调顺序确定
  （多设备按 `device_id` 排序）。
- 因此同一 `MockScenario` 构造的适配器运行两次，`frame_log` 的
  （虚拟时间, can_id, data）序列逐字节相同。

## 故障注入项

`FaultInjection` 可在构造时传入 `MockAdapter(scenario, faults)`，也可运行中通过
`adapter.faults.xxx` 直接切换：

| 开关 | 行为 |
| --- | --- |
| `offline=True` | `open_device` 抛 `MockDeviceOfflineError` |
| `send_fail=True` | `send_frame` 返回 `SendResult(requested=1, accepted=0, error_code=-1)`，不记录 `sent_frames` |
| `receive_fail=True` | 首次投递时抛 `MockReceiveError`（回调中断），该设备停止接收 |
| `handle_occupied=True` | `open_device` 抛 `MockHandleOccupiedError`（重复打开同样抛出） |
| `task_cancel=True` + `task_cancel_at_ms` | `run()` 推进到取消点（含该时刻事件）后抛 `MockTaskCancelledError`，并把虚拟时钟、事件队列、随机数状态与运行态标志回滚到本次 run 开始前；`task_cancel_at_ms=None` 等价于 0（下次 run 立即取消） |

取消回滚的边界：已交付给接收回调的帧与已记录日志不追回（证据 append-only）。

## 能力视图与急停

- `device_capabilities(device_id)` 返回 `buses`、`channel_count` 与
  `evidence`（能力 → 证据等级：`official_declared` / `sdk_visible` /
  `verified_on_device` / `unknown`）。
- `is_capable` 仅对前三种等级返回 `True`；未出现的能力按 `unknown` 处理，
  **未知 ≠ 支持，一律返回 `False`**（内置设备显式声明
  `hardware_periodic_tx: unknown` 即为此设计）。
- 内置设备：`mock-0`（model=`MOCK-CANFD-200U`、2 通道、CAN/CAN_FD）；
  `add_device(info, capabilities)` 可追加自定义设备（buses 放 `info.extra["buses"]`）。
- `emergency_stop(device_id)` 逐项执行并写入 `EmergencyStopReport`：
  停止发送、清空时间线队列、注销接收回调、复位通道、关闭设备
  （`details` 含清除事件数、被停通道、是否曾打开、虚拟时刻）；
  急停后可重新打开，时间线重新装配，无句柄/队列残留。

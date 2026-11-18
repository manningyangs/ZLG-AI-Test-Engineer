# HOST-01：Native Host IPC 协议说明

> 阶段：P1（HOST-01）。本阶段 Host 为 **Fake Host**（纯模拟，无真实 DLL）；
> 真实 `zlgcan.dll` 集成属 P3（SDK-01），届时仅替换 Host 进程内部实现，
> 本协议与 Python 侧代码保持不变。

## 1. 架构定位

```
┌──────────────────────────────┐        Windows Named Pipe         ┌─────────────────────────────┐
│  Python 主进程（UI / 测试引擎）│  ◄──── 4B 大端长度 + UTF-8 JSON ──► │  Host 进程（独立进程）        │
│  HostClient（infrastructure/ │        \\.\pipe\zlg-ate-host-<随机> │  P1: fake_host（Python 模拟）│
│  ipc/client.py）             │                                   │  P3: C++ Native Host + DLL   │
└──────────────────────────────┘                                   └─────────────────────────────┘
```

- 架构红线（任务书 7.2）：厂商 DLL 只能由独立 Host 进程加载，UI/Python 主进程
  **绝不**直接加载或伪造厂商函数。
- 模块位置：`src/zlg_ai_test_engine/infrastructure/ipc/`
  - `protocol.py` —— 帧格式、命令白名单、错误码、消息构造/编解码（传输无关）
  - `client.py` —— `HostClient` / `PipeChannel`（客户端）
  - `fake_host.py` —— `FakeHost` / `CtypesPipeServer`（服务端，可独立进程运行）
  - `__init__.py` —— 公开 API 导出

## 2. 帧格式

**长度前缀消息帧，绝不依赖换行截断：**

```
┌────────────────────────┬──────────────────────────┐
│ 4 字节大端 uint32 长度  │ UTF-8 JSON 消息体（N 字节）│
└────────────────────────┴──────────────────────────┘
```

- 长度值 = JSON 消息体字节数（不含前缀自身）；
- 单帧消息体上限 **1 MiB**（`MAX_MESSAGE_BYTES`）；
- 读写均完整处理短读/短写（`read_exact` / `write_all`）。

### 消息类型

请求（Python → Host）：

```json
{"protocol_version": "1.0", "request_id": "<uuid>", "command": "ping", "payload": {}}
```

响应（Host → Python）：

```json
{"protocol_version": "1.0", "request_id": "<uuid>", "ok": true,  "result": {}, "error": null}
{"protocol_version": "1.0", "request_id": "<uuid>", "ok": false, "result": {}, "error": {"code": "UNKNOWN_COMMAND", "message": "未知命令：..."}}
```

事件（Host → Python，主动推送）：

```json
{"protocol_version": "1.0", "event_id": "<hex>", "event_type": "rx_frame", "payload": {}}
```

- `request_id` 由客户端生成（uuid4 hex），响应按 id 匹配；同一 id **禁止重复发送**；
- 非法 JSON / 结构校验失败的错误响应 `request_id` 为 `null`（无法回显）。

## 3. 版本协商

- 当前 `protocol_version = "1.0"`（major.minor，major 变更不兼容）；
- Host 收到请求先校验版本：**major 不同 → 拒绝并返回
  `PROTOCOL_VERSION_MISMATCH`**，绝不静默继续；
- 客户端同样校验响应版本，双向防护。

## 4. 命令白名单

由 `protocol.HostCommand`（StrEnum）集中定义，`client` 与 `fake_host` 共用；
Host 端用**显式分发表**（`FakeHost._HANDLERS`）查表分发——未知命令返回
`UNKNOWN_COMMAND` 错误，**绝不动态执行任意函数名/路径**。

| 命令 | 说明 | 关键载荷 |
| --- | --- | --- |
| `ping` | 心跳/连通性 | — |
| `get_host_info` | Host 信息（kind/pid/版本/命令集） | — |
| `list_devices` | 设备列表（P1：1 台 mock 设备 `mock-0`） | — |
| `open_device` | 打开设备 | `device_id` |
| `close_device` | 关闭设备（有通道运行时拒绝） | `device_id` |
| `configure_channel` | 配置通道 | `device_id`, `channel_index`, `bitrate_arbitration`… |
| `start_channel` | 启动通道（须先配置） | `device_id`, `channel_index` |
| `stop_channel` | 停止通道 | `device_id`, `channel_index` |
| `send_frame` | 发送一帧（须通道运行中） | `device_id`, `channel_index`, `can_id`, `data_hex` |
| `subscribe_rx` | 订阅模拟接收帧事件 | `device_id`, `interval_ms`? |
| `unsubscribe_rx` | 退订 | — |
| `emergency_stop` | 急停（幂等，停发/复位/关闭收敛到安全态） | — |

### 设备状态机（Fake Host）

```
closed ──open_device──► open ──configure_channel──► configured ──start_channel──► running
   ▲                      │                             ▲                          │
   └────close_device──────┘ ◄──────stop_channel─────────┘ ◄──send_frame(运行中)─────┘
```

非法转移一律返回错误码：`DEVICE_NOT_OPEN` / `DEVICE_ALREADY_OPEN` /
`DEVICE_NOT_FOUND` / `CHANNEL_NOT_CONFIGURED` / `CHANNEL_ALREADY_RUNNING` /
`CHANNEL_NOT_RUNNING` / `INVALID_STATE` / `INVALID_PAYLOAD`。

## 5. 错误码（`protocol.ErrorCode`，StrEnum）

`UNKNOWN_COMMAND`、`INVALID_JSON`、`MESSAGE_TOO_LARGE`、
`PROTOCOL_VERSION_MISMATCH`、`INVALID_REQUEST`、`INVALID_PAYLOAD`、
`INVALID_STATE`、`DEVICE_NOT_FOUND`、`DEVICE_ALREADY_OPEN`、`DEVICE_NOT_OPEN`、`CHANNEL_NOT_CONFIGURED`、`CHANNEL_ALREADY_RUNNING`、`CHANNEL_NOT_RUNNING`、
`SUBSCRIPTION_NOT_FOUND`、`INTERNAL_ERROR`。

异常处理约定：
- 客户端 `request()` 在 `ok=false` 时抛 `HostCommandError(code, message)`；
- 超时抛 `TimeoutError`；重复 `request_id` 抛 `DuplicateRequestError`；
  断开抛 `ConnectionError`；
- Host 收到**非法 JSON**：回错误响应后继续服务；收到**超长消息**（>1 MiB）：
  回错误后**断开该连接**（帧体未读取，字节流已失步，无法安全恢复）；
- 所有异常路径输出中文日志，不含敏感信息。

## 6. HostClient 能力

```python
from zlg_ai_test_engine.infrastructure.ipc import HostClient

client = HostClient(spawn_host=True, ping_interval=5.0, connect_timeout=15)
client.start()                                  # 拉起子进程 + 连接 + 读线程 + 心跳
client.request("ping", timeout=5.0)             # 同步往返，按 request_id 匹配
client.subscribe("rx_frame", lambda payload: ...)  # 事件订阅（后台读线程分发）
client.on_disconnected = on_lost                # 崩溃检测回调（EOF/IOError 触发）
client.restart()                                # 重新拉起 host 子进程并重连
client.close()                                  # 关闭连接、回收子进程（默认 kill_host=True）
```

- **心跳**：`ping_interval > 0` 时后台线程定期 `ping`（`<=0` 关闭）；
- **崩溃检测**：读线程遇管道 EOF/IOError → 唤醒所有等待中的请求
  （`ConnectionError`）→ 触发一次 `on_disconnected`（主动 `close()`/`restart()`
  过程中不触发）；
- **事件回调在读线程执行**：回调不得阻塞、不得在回调内调用 `request()`；
- **子进程拉起命令**：
  `[sys.executable, "-m", "zlg_ai_test_engine.infrastructure.ipc.fake_host", "--pipe", <名字>]`，
  并注入 `PYTHONPATH=<src>` 保证 src 布局可导入。

独立运行 Fake Host：

```powershell
python -m zlg_ai_test_engine.infrastructure.ipc.fake_host --pipe zlg-ate-host-xxx
```

## 7. 实现取舍：ctypes vs `multiprocessing.connection`

| 方案 | 结论 |
| --- | --- |
| **ctypes 直调 Win32**（选定） | 满足任务书要求：原生 Named Pipe、仅标准库、帧格式完全自控（4B 长度前缀），与将来 C++ Native Host 的对接语义一致。 |
| `multiprocessing.connection`（兜底，未采用） | 属标准库且实现省力，但帧格式是其私有握手（先魔数/版本/authkey 握手 + 长度前缀），与任务书规定的 JSON 帧格式不同，且协议细节受库版本牵制；仅在 ctypes 被证不可用时兜底。**实测 ctypes 方案可行**，故未启用。 |

ctypes 方案落地时的三个实测要点（Windows 10/11 + Python 3.12）：

1. **`CreateNamedPipeW` 是 8 参数 API**（含 `nMaxInstances`）——漏传会导致参数
   错位、返回 `ERROR_INVALID_PARAMETER(87)`；
2. **同步（非 OVERLAPPED）句柄上并发读写会串行化死锁**：一个线程阻塞在
   `ReadFile` 等数据时，另一线程的 `WriteFile` 会一直等待（本机实测复现）。
   因此服务端与客户端的**读侧均改为 `PeekNamedPipe` 短周期（5 ms）轮询 +
   数据就绪后瞬时读取**，读路径永不长时间挂起，写路径始终畅通。代价是
   idle 时约 200 次/秒的 peek 调用（可忽略）与 ≤5 ms 的数据检测延迟；
3. 客户端用 `open(r"\\.\pipe\...")`（CRT）同样触发上述串行化死锁
   （实测 `FileIO.write` 无限阻塞），故客户端亦统一为 ctypes
   （`CreateFileW/ReadFile/WriteFile/CloseHandle`），两端语义对称。

> 备选演进（如需更低延迟/零轮询）：以 `FILE_FLAG_OVERLAPPED` 打开句柄并用
> OVERLAPPED 事件驱动读写。当前骨架的负载（控制命令 + 模拟事件）下轮询方案
> 已足够，故未引入该复杂度。

## 8. 测试

`tests/integration/test_ipc_host.py`（11 项）覆盖任务书验收 1-7：

1. roundtrip（ping/get_host_info/list_devices，子进程 fixture teardown 强杀回收）；
2. 未知命令 → `ok=false` + `UNKNOWN_COMMAND`，Host 仍存活；
3. Host 故意不回 → `TimeoutError`（限时段内验证）；
4. 重复 `request_id` → `DuplicateRequestError`；
5. kill 子进程 → `on_disconnected` 触发；`restart()` 后 ping 恢复；
6. 协议版本 major 不匹配 → `PROTOCOL_VERSION_MISMATCH`；
7. 订阅 `rx_frame` 事件收到模拟帧。

另覆盖：设备状态机非法转移、非法 JSON 帧、超长帧（>1 MiB）、心跳保活。
测试不依赖第三方超时插件——所有等待均带显式超时（自控限时），保证用例不会
无限挂起；所有子进程在 fixture/finally 中回收，不留孤儿进程。

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_ipc_host.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

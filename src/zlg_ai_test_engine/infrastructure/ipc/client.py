"""HOST-01：HostClient —— Python 主进程侧的 Native Host 管道客户端。

能力（任务书 HOST-01）：
- 同步 ``request()``：按 ``request_id`` 匹配响应，超时抛 ``TimeoutError``；
- 重复 ``request_id`` 防护（同 id 二次发送抛 ``DuplicateRequestError``）；
- 事件订阅：后台读线程按 ``event_type`` 分发到订阅回调；
- 心跳：可配置间隔定期 ``ping``；
- 崩溃检测：pipe EOF/IOError → 触发 ``on_disconnected`` 回调；
- ``restart()``：重新拉起 Fake Host 子进程并重连。

传输实现：客户端用 **ctypes 调 Win32 CreateFileW/ReadFile/WriteFile/CloseHandle**
（标准库，无 pywin32），读写经 ``protocol.read_message`` / ``write_message``
完整处理短读短写。取舍说明见 ``docs/api/ipc.md``：实测 CRT
``open(r"\\\\.\\pipe\\...")`` 在本机并发读写下写入阻塞，故两端统一走 ctypes。
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Mapping

from zlg_ai_test_engine.infrastructure.ipc.protocol import (
    PROTOCOL_VERSION,
    DuplicateRequestError,
    ErrorCode,
    HostClientError,
    HostCommand,
    HostCommandError,
    ProtocolError,
    build_request,
    generate_pipe_name,
    is_version_compatible,
    normalize_pipe_name,
    read_message,
    write_message,
)

logger = logging.getLogger(__name__)

#: Fake Host 目标模块（restart/首次拉起子进程用）
FAKE_HOST_MODULE = "zlg_ai_test_engine.infrastructure.ipc.fake_host"

#: src 布局根目录（ipc -> infrastructure -> zlg_ai_test_engine -> src）
_SRC_DIR = Path(__file__).resolve().parents[3]

#: 事件回调：接收事件 payload 字典。
EventCallback = Callable[[dict[str, Any]], None]
#: 断开回调：无参。
DisconnectedCallback = Callable[[], None]

# ---------------------------------------------------------------------------
# Win32 客户端通道（仅标准库 ctypes）
# ---------------------------------------------------------------------------

_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_ERROR_FILE_NOT_FOUND = 2
_ERROR_PIPE_BUSY = 231
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_NOT_CONNECTED = 223
_ERROR_NO_DATA = 232
_ERROR_INVALID_HANDLE = 6
#: 对端关闭/失效类错误码（读返回 EOF、写抛 ConnectionError）
_DISCONNECT_ERRORS = frozenset(
    {_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED, _ERROR_NO_DATA, _ERROR_INVALID_HANDLE}
)

_KERNEL32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_KERNEL32.CreateFileW.restype = wintypes.HANDLE
_KERNEL32.ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_KERNEL32.ReadFile.restype = wintypes.BOOL
_KERNEL32.WriteFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_KERNEL32.WriteFile.restype = wintypes.BOOL
_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
_KERNEL32.CloseHandle.restype = wintypes.BOOL
_KERNEL32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPDWORD,
    wintypes.LPDWORD,
    wintypes.LPVOID,
]
_KERNEL32.PeekNamedPipe.restype = wintypes.BOOL

#: 读侧无数据时的轮询间隔（秒）
_POLL_INTERVAL = 0.005


class PipeChannel:
    """客户端侧的 Named Pipe 通道（ctypes Win32，读/写接口与服务端对齐）。"""

    def __init__(self, pipe_name: str) -> None:
        self.pipe_name = normalize_pipe_name(pipe_name)
        handle = _KERNEL32.CreateFileW(
            self.pipe_name,
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle in (None, _INVALID_HANDLE_VALUE):
            err = ctypes.get_last_error()
            if err == _ERROR_FILE_NOT_FOUND:
                raise FileNotFoundError(2, "Named Pipe 尚不存在", self.pipe_name)
            if err == _ERROR_PIPE_BUSY:
                raise OSError(err, "Named Pipe 忙（实例已占满）")
            raise OSError(err, f"打开 Named Pipe 失败（错误码 {err}）：{self.pipe_name}")
        self._handle = handle
        self._closed = False

    def _peek_available(self) -> int | None:
        """返回 pipe 中可用字节数；对端断开返回 ``None``（EOF）。"""
        n_avail = wintypes.DWORD(0)
        ok = _KERNEL32.PeekNamedPipe(self._handle, None, 0, None, ctypes.byref(n_avail), None)
        if not ok:
            err = ctypes.get_last_error()
            if err in _DISCONNECT_ERRORS:
                return None  # 对端关闭 → EOF（触发崩溃检测）
            raise OSError(err, f"PeekNamedPipe 失败（错误码 {err}）")
        return n_avail.value

    def read(self, size: int) -> bytes:
        """读取至多 ``size`` 字节。返回 ``b''`` 表示对端关闭（EOF）。

        实现说明：同步（非 OVERLAPPED）句柄上阻塞 ``ReadFile`` 会与并发
        ``WriteFile`` 互相串行化死锁（本机实测），因此以 ``PeekNamedPipe``
        短周期轮询等待数据，数据就绪后再瞬时读取，读路径永不长时间挂起。
        """
        if size <= 0:
            return b""
        while True:
            available = self._peek_available()
            if available is None:
                return b""
            if available > 0:
                break
            time.sleep(_POLL_INTERVAL)
        # 请求量不超过已就绪字节数，ReadFile 立即返回（短读由 read_exact 循环处理）
        to_read = size if available >= size else available
        buffer = ctypes.create_string_buffer(to_read)
        n_read = wintypes.DWORD(0)
        ok = _KERNEL32.ReadFile(self._handle, buffer, to_read, ctypes.byref(n_read), None)
        if not ok:
            err = ctypes.get_last_error()
            if err in _DISCONNECT_ERRORS:
                return b""
            raise OSError(err, f"读取 Named Pipe 失败（错误码 {err}）")
        return buffer.raw[: n_read.value]

    def write(self, data: bytes) -> int:
        """写出至多 ``len(data)`` 字节，返回实际写入数（短写由 write_all 循环）。"""
        if not data:
            return 0
        n_written = wintypes.DWORD(0)
        ok = _KERNEL32.WriteFile(self._handle, data, len(data), ctypes.byref(n_written), None)
        if not ok:
            err = ctypes.get_last_error()
            if err in _DISCONNECT_ERRORS:
                raise ConnectionError("写入 Named Pipe 失败：Host 已断开")
            raise OSError(err, f"写入 Named Pipe 失败（错误码 {err}）")
        return n_written.value

    def close(self) -> None:
        """关闭句柄（幂等）；可打断对端与本端的阻塞读写。"""
        if self._closed:
            return
        self._closed = True
        if self._handle not in (None, _INVALID_HANDLE_VALUE):
            _KERNEL32.CloseHandle(self._handle)


class HostClient:
    """与 Native Host（P1 为 Fake Host）通信的同步客户端。"""

    def __init__(
        self,
        pipe_name: str | None = None,
        *,
        spawn_host: bool = False,
        protocol_version: str = PROTOCOL_VERSION,
        connect_timeout: float = 10.0,
        default_timeout: float = 5.0,
        ping_interval: float = 0.0,
        on_disconnected: DisconnectedCallback | None = None,
    ) -> None:
        """
        Args:
            pipe_name: pipe 短名或完整路径；缺省生成随机 ``zlg-ate-host-<随机>``。
            spawn_host: 是否由本客户端拉起 fake_host 子进程。
            protocol_version: 客户端发送的协议版本（major 必须与 Host 一致）。
            connect_timeout: 连接等待上限（秒）。
            default_timeout: ``request()`` 默认超时（秒）。
            ping_interval: 心跳间隔（秒），<=0 关闭心跳。
            on_disconnected: 崩溃/断开回调（读线程触发，勿在其中调用 request）。
        """
        self.pipe_name = normalize_pipe_name(pipe_name) if pipe_name else normalize_pipe_name(
            generate_pipe_name()
        )
        self.spawn_host = spawn_host
        self.protocol_version = protocol_version
        self.connect_timeout = connect_timeout
        self.default_timeout = default_timeout
        self.ping_interval = ping_interval
        #: 断开回调（可在 start() 前后随时赋值）
        self.on_disconnected = on_disconnected
        #: 由本客户端拉起的 host 子进程句柄（外部启动时为 None）
        self.host_process: subprocess.Popen[bytes] | None = None

        self._lock = threading.Lock()
        #: request_id -> {"event": Event, "response": dict|None, "error": str|None}
        self._pending: dict[str, dict[str, Any]] = {}
        #: 所有历史发送过的 request_id（防重放/重复）
        self._issued: set[str] = set()
        self._subscriptions: dict[str, list[EventCallback]] = {}

        self._channel: PipeChannel | None = None
        #: 保护同方向并发写（多线程 request + 心跳共用一个 pipe）
        self._write_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._stop = threading.Event()
        self._closing = False
        self._connected = False
        self._disconnect_reported = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> "HostClient":
        """（可选）拉起 host 子进程、连接管道并启动读线程/心跳。"""
        if self._connected:
            raise HostClientError("客户端已连接，无需重复 start()")
        if self.spawn_host:
            self._spawn_host()
        self._connect_with_retry(self.connect_timeout)
        self._start_reader()
        self._start_heartbeat()
        logger.info("已连接 Host 管道：%s", self.pipe_name)
        return self

    def _spawn_host(self) -> None:
        """以 ``[sys.executable, -m, fake_host, --pipe, name]`` 拉起子进程。"""
        env = os.environ.copy()
        # 保证子进程可导入 src 布局（不依赖 pip install 状态，不引入新依赖）
        src = str(_SRC_DIR)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
        self.host_process = subprocess.Popen(
            [sys.executable, "-m", FAKE_HOST_MODULE, "--pipe", self.pipe_name],
            env=env,
        )
        logger.info("已拉起 Host 子进程（pid=%d）：%s", self.host_process.pid, self.pipe_name)

    def _connect_with_retry(self, timeout: float) -> None:
        """在 ``timeout`` 秒内重试打开管道（host 创建 pipe 需要时间）。"""
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            if self.host_process is not None and self.host_process.poll() is not None:
                raise HostClientError(
                    f"Host 子进程已退出（code={self.host_process.returncode}），无法连接"
                )
            try:
                self._channel = PipeChannel(self.pipe_name)
                self._connected = True
                self._disconnect_reported = False
                self._stop.clear()
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.05)
        raise TimeoutError(
            f"连接 Named Pipe 超时（{timeout:.1f}s）：{self.pipe_name}（最后错误：{last_error}）"
        )

    def close(self, *, kill_host: bool = True) -> None:
        """关闭连接与线程；``kill_host`` 时终止自己拉起的子进程（不留孤儿）。"""
        self._closing = True
        self._stop.set()
        if kill_host:
            self._terminate_host()
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=3.0)
            if reader.is_alive():
                # 外部 host 仍占着管道：强关句柄打断阻塞读
                self._close_channel()
                reader.join(timeout=2.0)
        else:
            self._close_channel()
        heartbeat = self._heartbeat
        if heartbeat is not None and heartbeat.is_alive():
            heartbeat.join(timeout=2.0)
        self._close_channel()
        self._wake_all_pending(ConnectionError("客户端已关闭"))
        self._connected = False
        logger.info("客户端已关闭：%s", self.pipe_name)

    def restart(self, *, spawn_timeout: float | None = None) -> None:
        """断开旧连接、重新拉起（或重连）Host 并恢复读线程与心跳。"""
        timeout = spawn_timeout if spawn_timeout is not None else self.connect_timeout
        logger.info("开始重启 Host 连接：%s", self.pipe_name)
        self._closing = True  # 抑制重启过程触发 on_disconnected
        self._stop.set()

        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=3.0)
            if reader.is_alive():
                self._close_channel()
                reader.join(timeout=2.0)
            if reader.is_alive():
                raise HostClientError("旧读线程无法退出，restart 中止")
        heartbeat = self._heartbeat
        if heartbeat is not None and heartbeat.is_alive():
            heartbeat.join(timeout=2.0)
        self._close_channel()
        self._terminate_host()
        self._wake_all_pending(ConnectionError("连接已重启，原请求作废"))

        if self.spawn_host:
            self._spawn_host()
        self._closing = False
        self._connect_with_retry(timeout)
        self._start_reader()
        self._start_heartbeat()
        logger.info("Host 连接已重启：%s", self.pipe_name)

    def _terminate_host(self) -> None:
        proc = self.host_process
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                logger.error("Host 子进程（pid=%d）无法终止", proc.pid)
        logger.info("Host 子进程已终止（pid=%d）", proc.pid)

    def _close_channel(self) -> None:
        channel, self._channel = self._channel, None
        if channel is not None:
            try:
                channel.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 同步请求
    # ------------------------------------------------------------------

    def request(
        self,
        command: str | HostCommand,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """同步发送请求并等待匹配响应。

        Returns:
            响应 ``result`` 字典（``ok=true``）。

        Raises:
            DuplicateRequestError: ``request_id``（含自动生成的）重复使用。
            TimeoutError: 超时未收到响应。
            ConnectionError: 发送/等待期间连接断开。
            HostCommandError: Host 返回 ``ok=false`` 或协议版本不兼容。
        """
        if not self._connected or self._channel is None:
            raise HostClientError("尚未连接 Host，请先调用 start()（或 restart()）")
        wait_timeout = self.default_timeout if timeout is None else timeout
        rid = request_id if request_id is not None else uuid.uuid4().hex
        if not isinstance(rid, str) or not rid:
            raise HostClientError("request_id 必须是非空字符串")
        command_name = command.value if isinstance(command, HostCommand) else command

        entry: dict[str, Any] = {"event": threading.Event(), "response": None, "error": None}
        with self._lock:
            if rid in self._issued:
                raise DuplicateRequestError(f"禁止重复使用 request_id：{rid}")
            self._issued.add(rid)
            self._pending[rid] = entry

        message = build_request(
            command_name, rid, payload, protocol_version=self.protocol_version
        )
        try:
            with self._write_lock:
                write_message(self._channel.write, message)
        except (OSError, ValueError, AttributeError, ConnectionError) as exc:
            with self._lock:
                self._pending.pop(rid, None)
            raise ConnectionError(f"发送请求失败（连接可能已断开）：{exc}") from exc

        if not entry["event"].wait(wait_timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"请求 {command_name} 超时（{wait_timeout:.1f}s），request_id={rid}")
        with self._lock:
            self._pending.pop(rid, None)
        if entry["error"] is not None:
            raise ConnectionError(f"连接已断开，请求 {command_name} 未完成")

        response: dict[str, Any] = entry["response"] or {}
        # 双向版本防护：响应 major 与本地不同 → 明确错误码，不静默继续
        if not is_version_compatible(response.get("protocol_version"), self.protocol_version):
            raise HostCommandError(
                str(ErrorCode.PROTOCOL_VERSION_MISMATCH),
                f"协议版本不兼容：响应 {response.get('protocol_version')!r}，"
                f"本地 {self.protocol_version!r}",
            )
        if not response.get("ok"):
            error = response.get("error") or {}
            raise HostCommandError(
                str(error.get("code", ErrorCode.INTERNAL_ERROR)),
                str(error.get("message", "Host 返回错误（无详情）")),
            )
        result = response.get("result")
        return dict(result) if isinstance(result, dict) else {}

    # ------------------------------------------------------------------
    # 事件订阅
    # ------------------------------------------------------------------

    def subscribe(self, event_type: str, callback: EventCallback) -> None:
        """订阅事件。回调在读线程执行：勿阻塞、勿在回调中调用 ``request()``。"""
        with self._lock:
            callbacks = self._subscriptions.setdefault(event_type, [])
            if callback not in callbacks:
                callbacks.append(callback)

    def unsubscribe(self, event_type: str, callback: EventCallback | None = None) -> None:
        """退订事件；``callback`` 为 None 时移除该类型的全部回调。"""
        with self._lock:
            if callback is None:
                self._subscriptions.pop(event_type, None)
                return
            callbacks = self._subscriptions.get(event_type)
            if callbacks and callback in callbacks:
                callbacks.remove(callback)

    # ------------------------------------------------------------------
    # 读线程与分发
    # ------------------------------------------------------------------

    def _start_reader(self) -> None:
        self._reader = threading.Thread(
            target=self._reader_loop, name="host-client-reader", daemon=True
        )
        self._reader.start()

    def _reader_loop(self) -> None:
        channel = self._channel
        try:
            while not self._stop.is_set() and channel is not None:
                try:
                    message = read_message(channel.read)
                except ProtocolError as exc:
                    if exc.code == str(ErrorCode.INVALID_JSON):
                        # 帧边界完好：忽略坏帧继续读
                        logger.warning("Host 发来非法 JSON 帧，已忽略：%s", exc.message)
                        continue
                    # 超长/截断等：帧流已不可信，必须断开
                    logger.warning("Host 发来协议违规帧，断开连接：%s", exc.message)
                    break
                if message is None:
                    logger.warning("Host 管道已关闭（EOF），检测到断开。")
                    break
                self._dispatch(message)
        except OSError as exc:
            logger.warning("读取 Host 消息发生 IO 错误：%s", exc)
        finally:
            self._on_reader_exit()

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "event_type" in message:
            self._dispatch_event(message)
            return
        if "request_id" in message and "ok" in message:
            rid = message.get("request_id")
            if not isinstance(rid, str) or not rid:
                logger.warning("Host 返回缺失 request_id 的响应（协议错误通告），已忽略。")
                return
            with self._lock:
                entry = self._pending.get(rid)
            if entry is None:
                logger.warning("收到未知 request_id 的响应，已忽略。")
                return
            entry["response"] = message
            entry["event"].set()
            return
        logger.warning("收到无法识别的消息结构，已忽略。")

    def _dispatch_event(self, message: dict[str, Any]) -> None:
        event_type = message.get("event_type")
        if not isinstance(event_type, str):
            logger.warning("事件缺少 event_type，已忽略。")
            return
        with self._lock:
            callbacks = list(self._subscriptions.get(event_type, ()))
        payload = message.get("payload")
        payload = dict(payload) if isinstance(payload, dict) else {}
        for callback in callbacks:
            try:
                callback(payload)
            except Exception:
                logger.exception("事件回调执行异常（event_type=%s），已忽略", event_type)

    def _on_reader_exit(self) -> None:
        """读线程退出：唤醒等待者、标记断开、按需触发 on_disconnected。"""
        with self._lock:
            for entry in self._pending.values():
                if entry["error"] is None:
                    entry["error"] = "disconnected"
                    entry["event"].set()
            self._connected = False
            should_report = (
                self.on_disconnected is not None
                and not self._closing
                and not self._disconnect_reported
            )
            self._disconnect_reported = True
        if should_report:
            logger.warning("检测到 Host 断开，通知 on_disconnected 回调。")
            try:
                self.on_disconnected()  # type: ignore[misc]
            except Exception:
                logger.exception("on_disconnected 回调执行异常")

    def _wake_all_pending(self, error: Exception) -> None:
        with self._lock:
            for entry in self._pending.values():
                if entry["error"] is None:
                    entry["error"] = str(error)
                    entry["event"].set()

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        if self.ping_interval <= 0:
            return
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="host-client-heartbeat", daemon=True
        )
        self._heartbeat.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.ping_interval):
            try:
                self.request("ping")
            except (TimeoutError, ConnectionError, HostClientError, HostCommandError) as exc:
                logger.warning("心跳 ping 失败：%s", exc)
            except Exception:
                logger.exception("心跳线程发生未知异常")

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """当前是否处于已连接状态。"""
        return self._connected

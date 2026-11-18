"""HOST-01：Fake Host —— 独立进程的 Named Pipe 服务端（模拟设备，无真实 DLL）。

运行方式（独立进程）::

    python -m zlg_ai_test_engine.infrastructure.ipc.fake_host --pipe zlg-ate-host-xxx

实现要点：
- 服务端管道用 **ctypes 直调 Win32**（``CreateNamedPipeW`` / ``ConnectNamedPipe`` /
  ``ReadFile`` / ``WriteFile`` / ``CloseHandle``），仅标准库，无 pywin32；
- 帧格式为 4 字节大端长度前缀 + UTF-8 JSON（见 ``protocol.py``）；
- 命令走白名单分发表，未知命令返回 ``UNKNOWN_COMMAND``，绝不动态执行任意函数；
- 进程内维护 mock 设备状态机，非法状态返回明确错误码；
- ``subscribe_rx`` 后周期推送模拟 ``rx_frame`` 事件。

红线：本模块**不加载任何厂商 DLL、不伪造厂商函数**（真实 SDK 属 P3 SDK-01）。
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Mapping

from zlg_ai_test_engine.infrastructure.ipc.protocol import (
    HOST_COMMANDS,
    PROTOCOL_VERSION,
    ErrorCode,
    EventType,
    HostCommand,
    ProtocolError,
    build_error_response,
    build_event,
    build_response,
    normalize_pipe_name,
    read_message,
    write_message,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 常量与 ctypes 声明（仅标准库，禁止 pywin32）
# ---------------------------------------------------------------------------

_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

PIPE_ACCESS_DUPLEX = 0x00000003
#: 字节模式 / 非阻塞读模式位均为 0（PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT）
PIPE_MODE_BYTE = 0x00000000
#: 最大实例数（PIPE_UNLIMITED_INSTANCES）
PIPE_UNLIMITED_INSTANCES = 255
BUFFER_SIZE = 64 * 1024
DEFAULT_TIMEOUT_MS = 0

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

ERROR_NO_DATA = 232
ERROR_PIPE_CONNECTED = 535
ERROR_BROKEN_PIPE = 109
ERROR_PIPE_NOT_CONNECTED = 223
ERROR_INVALID_HANDLE = 6
#: 对端关闭/失效类错误码（读返回 EOF）
_DISCONNECT_ERRORS = frozenset(
    {ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED, ERROR_NO_DATA, ERROR_INVALID_HANDLE}
)
#: 读侧无数据时的轮询间隔（秒）
_POLL_INTERVAL = 0.005

_KERNEL32.CreateNamedPipeW.argtypes = [
    wintypes.LPCWSTR,  # lpName
    wintypes.DWORD,  # dwOpenMode
    wintypes.DWORD,  # dwPipeMode
    wintypes.DWORD,  # nMaxInstances
    wintypes.DWORD,  # nOutBufferSize
    wintypes.DWORD,  # nInBufferSize
    wintypes.DWORD,  # nDefaultTimeout
    wintypes.LPVOID,  # lpSecurityAttributes
]
_KERNEL32.CreateNamedPipeW.restype = wintypes.HANDLE

_KERNEL32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
_KERNEL32.ConnectNamedPipe.restype = wintypes.BOOL

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


class CtypesPipeServer:
    """基于 ctypes Win32 API 的 Named Pipe 服务端单实例（字节模式）。"""

    def __init__(self, pipe_name: str) -> None:
        self.pipe_name = normalize_pipe_name(pipe_name)
        self._handle = _KERNEL32.CreateNamedPipeW(
            self.pipe_name,
            PIPE_ACCESS_DUPLEX,
            PIPE_MODE_BYTE,
            PIPE_UNLIMITED_INSTANCES,
            BUFFER_SIZE,
            BUFFER_SIZE,
            DEFAULT_TIMEOUT_MS,
            None,
        )
        if self._handle in (None, INVALID_HANDLE_VALUE):
            err = ctypes.get_last_error()
            raise OSError(err, f"创建 Named Pipe 失败（错误码 {err}）：{self.pipe_name}")
        self._closed = False

    def connect(self) -> bool:
        """阻塞等待客户端连接。返回 False 表示实例已被关闭/失效。"""
        ok = _KERNEL32.ConnectNamedPipe(self._handle, None)
        if ok:
            return True
        err = ctypes.get_last_error()
        if err == ERROR_PIPE_CONNECTED:
            # 客户端在 ConnectNamedPipe 之前已连上，视为成功
            return True
        if err in (ERROR_INVALID_HANDLE, ERROR_NO_DATA):
            logger.warning("Named Pipe 等待连接被中止（错误码 %d）：%s", err, self.pipe_name)
            return False
        raise OSError(err, f"连接 Named Pipe 失败（错误码 {err}）：{self.pipe_name}")

    def _peek_available(self) -> int | None:
        """返回 pipe 可用字节数；对端断开返回 ``None``（EOF）。"""
        n_avail = wintypes.DWORD(0)
        ok = _KERNEL32.PeekNamedPipe(self._handle, None, 0, None, ctypes.byref(n_avail), None)
        if not ok:
            err = ctypes.get_last_error()
            if err in _DISCONNECT_ERRORS:
                return None
            raise OSError(err, f"PeekNamedPipe 失败（错误码 {err}）")
        return n_avail.value

    def read(self, size: int) -> bytes:
        """读取至多 ``size`` 字节。返回 ``b''`` 表示对端关闭（EOF）。

        同步句柄上阻塞 ``ReadFile`` 会与并发 ``WriteFile``（rx 事件线程）
        串行化互相阻塞，故以 ``PeekNamedPipe`` 轮询等待数据后再瞬时读取。
        """
        if size <= 0:
            return b""
        while True:
            available = self._peek_available()
            if available is None:
                return b""  # 对端关闭 → EOF
            if available > 0:
                break
            time.sleep(_POLL_INTERVAL)
        to_read = size if available >= size else available
        buffer = ctypes.create_string_buffer(to_read)
        n_read = wintypes.DWORD(0)
        ok = _KERNEL32.ReadFile(self._handle, buffer, to_read, ctypes.byref(n_read), None)
        if not ok:
            err = ctypes.get_last_error()
            if err in _DISCONNECT_ERRORS:
                return b""  # 对端关闭 → EOF
            raise OSError(err, f"读取 Named Pipe 失败（错误码 {err}）")
        return buffer.raw[: n_read.value]

    def write(self, data: bytes) -> int:
        """写出至多 ``len(data)`` 字节，返回实际写入字节数（由上层处理短写）。"""
        if not data:
            return 0
        n_written = wintypes.DWORD(0)
        ok = _KERNEL32.WriteFile(
            self._handle, data, len(data), ctypes.byref(n_written), None
        )
        if not ok:
            err = ctypes.get_last_error()
            if err in (ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED, ERROR_NO_DATA, ERROR_INVALID_HANDLE):
                raise ConnectionError("写入 Named Pipe 失败：客户端已断开")
            raise OSError(err, f"写入 Named Pipe 失败（错误码 {err}）")
        return n_written.value

    def close(self) -> None:
        """关闭管道句柄（幂等）。"""
        if self._closed:
            return
        self._closed = True
        if self._handle not in (None, INVALID_HANDLE_VALUE):
            _KERNEL32.CloseHandle(self._handle)
        logger.debug("Named Pipe 实例已关闭：%s", self.pipe_name)


# ---------------------------------------------------------------------------
# Fake Host：mock 设备状态机
# ---------------------------------------------------------------------------

#: 唯一的 mock 设备（P3 前不接触任何真实硬件/DLL）
MOCK_DEVICE: dict[str, Any] = {
    "device_id": "mock-0",
    "model": "FAKE-CAN (HOST-01)",
    "serial": "FAKE0000",
    "channel_count": 2,
    "hardware_version": None,
    "firmware_version": None,
    "driver_version": None,
    "sdk_version": None,
    "extra": {"kind": "fake", "dll_loaded": False},
}


class _CommandError(Exception):
    """命令处理失败（映射为 ok=false 响应）。"""

    def __init__(self, code: ErrorCode | str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = message


class FakeHost:
    """Fake Host 服务端：单客户端串行服务 + 可选 rx 事件推送线程。"""

    def __init__(self, pipe_name: str, *, rx_interval: float = 0.05) -> None:
        self.pipe_name = normalize_pipe_name(pipe_name)
        self.rx_interval = max(0.01, rx_interval)
        self._stop = threading.Event()
        self._server: CtypesPipeServer | None = None
        self._write_lock = threading.Lock()

        # 模拟设备状态机
        self._device_open = False
        self._channels: dict[int, dict[str, bool]] = {}

        # rx 订阅与推送线程
        self._rx_subscribed = False
        self._rx_stop = threading.Event()
        self._rx_thread: threading.Thread | None = None
        self._rx_seq = 0

    # ------------------------------------------------------------------
    # 服务主循环
    # ------------------------------------------------------------------

    def serve(self) -> None:
        """接受连接并服务，客户端断开后回到等待（进程由外部终止）。"""
        logger.info("Fake Host 启动（pid=%d），管道：%s", os.getpid(), self.pipe_name)
        consecutive_failures = 0
        while not self._stop.is_set():
            server: CtypesPipeServer | None = None
            try:
                server = CtypesPipeServer(self.pipe_name)
                self._server = server
                if not server.connect():
                    break
                logger.info("客户端已连接：%s", self.pipe_name)
                consecutive_failures = 0
                self._serve_connection(server)
            except OSError as exc:
                if self._stop.is_set():
                    break
                consecutive_failures += 1
                logger.error("Named Pipe 服务异常：%s", exc)
                if consecutive_failures >= 5:
                    logger.error("连续 %d 次创建管道失败，Fake Host 退出。", consecutive_failures)
                    break
                time.sleep(0.1)
            finally:
                self._reset_session()
                if server is not None:
                    server.close()
                self._server = None
            if self._stop.is_set():
                break
        logger.info("Fake Host 已退出。")

    def stop(self) -> None:
        """停止服务（解除阻塞中的 accept/read）。"""
        self._stop.set()
        self._stop_rx()
        server = self._server
        if server is not None:
            server.close()

    # ------------------------------------------------------------------
    # 单连接读写循环
    # ------------------------------------------------------------------

    def _serve_connection(self, server: CtypesPipeServer) -> None:
        while not self._stop.is_set():
            try:
                message = read_message(server.read)
            except ProtocolError as exc:
                if exc.code == str(ErrorCode.MESSAGE_TOO_LARGE):
                    # 帧体未读取，流已失步：回错误后必须断开
                    logger.warning("收到超长消息，回错误并断开连接：%s", exc.message)
                    self._try_send(
                        server,
                        build_error_response(None, ErrorCode.MESSAGE_TOO_LARGE, exc.message),
                    )
                    return
                if exc.code == str(ErrorCode.INVALID_JSON):
                    # 帧边界完好，可回错误后继续
                    logger.warning("收到非法 JSON 帧，已拒绝：%s", exc.message)
                    self._try_send(
                        server,
                        build_error_response(None, ErrorCode.INVALID_JSON, exc.message),
                    )
                    continue
                logger.warning("读取帧失败，断开连接：%s", exc.message)
                return
            except (ConnectionError, OSError) as exc:
                logger.info("连接读取中断：%s", exc)
                return

            if message is None:
                logger.info("客户端已断开（EOF）。")
                return

            response = self._handle_request(message)
            try:
                with self._write_lock:
                    write_message(server.write, response)
            except (ConnectionError, OSError) as exc:
                logger.warning("响应写入失败，断开连接：%s", exc)
                return

    def _try_send(self, server: CtypesPipeServer, message: Mapping[str, Any]) -> None:
        try:
            with self._write_lock:
                write_message(server.write, message)
        except (ConnectionError, OSError) as exc:
            logger.debug("错误响应发送失败（客户端可能已离开）：%s", exc)

    # ------------------------------------------------------------------
    # 请求校验与分发（白名单，绝不动态执行任意函数）
    # ------------------------------------------------------------------

    #: 显式命令分发表（非法/未知命令不会落入任何 handler）
    _HANDLERS: "dict[HostCommand, Any]" = {}

    def _handle_request(self, message: Mapping[str, Any]) -> dict[str, Any]:
        request_id = message.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return build_error_response(None, ErrorCode.INVALID_REQUEST, "request_id 缺失或非法")

        # 协议版本检查：major 不同必须明确拒绝，不得静默继续
        version = message.get("protocol_version")
        if not isinstance(version, str):
            return build_error_response(
                request_id, ErrorCode.INVALID_REQUEST, "protocol_version 缺失或非法"
            )
        local_major = PROTOCOL_VERSION.split(".")[0]
        if version.split(".")[0] != local_major:
            return build_error_response(
                request_id,
                ErrorCode.PROTOCOL_VERSION_MISMATCH,
                f"协议版本不兼容：对端 {version}，本端 {PROTOCOL_VERSION}",
            )

        command = message.get("command")
        if not isinstance(command, str) or not command:
            return build_error_response(request_id, ErrorCode.INVALID_REQUEST, "command 缺失或非法")
        if command not in HOST_COMMANDS:
            logger.warning("拒绝未知命令：%s", command)
            return build_error_response(
                request_id, ErrorCode.UNKNOWN_COMMAND, f"未知命令：{command}"
            )

        payload = message.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return build_error_response(request_id, ErrorCode.INVALID_PAYLOAD, "payload 必须是对象")

        handler = self._HANDLERS[HostCommand(command)]
        try:
            result = handler(self, payload)
        except _CommandError as exc:
            logger.warning("命令 %s 执行失败：%s（%s）", command, exc.message, exc.code)
            return build_error_response(request_id, exc.code, exc.message)
        except Exception:
            logger.exception("命令 %s 执行发生内部错误", command)
            return build_error_response(
                request_id, ErrorCode.INTERNAL_ERROR, "Host 内部错误，详见 Host 日志"
            )
        return build_response(request_id, result=result)

    # ------------------------------------------------------------------
    # 命令实现（mock 设备状态机）
    # ------------------------------------------------------------------

    def _cmd_ping(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"pong": True, "host_time": time.time()}

    def _cmd_get_host_info(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "host_kind": "fake",
            "pid": os.getpid(),
            "protocol_version": PROTOCOL_VERSION,
            "commands": sorted(HOST_COMMANDS),
            "dll_loaded": False,
        }

    def _cmd_list_devices(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"devices": [dict(MOCK_DEVICE)]}

    def _require_open(self) -> None:
        if not self._device_open:
            raise _CommandError(ErrorCode.DEVICE_NOT_OPEN, "设备未打开，请先 open_device")

    def _require_device_id(self, payload: Mapping[str, Any]) -> str:
        device_id = payload.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            raise _CommandError(ErrorCode.INVALID_PAYLOAD, "缺少 device_id 参数")
        if device_id != MOCK_DEVICE["device_id"]:
            raise _CommandError(ErrorCode.DEVICE_NOT_FOUND, f"设备不存在：{device_id}")
        return device_id

    def _require_channel_index(self, payload: Mapping[str, Any]) -> int:
        index = payload.get("channel_index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise _CommandError(ErrorCode.INVALID_PAYLOAD, "channel_index 必须是非负整数")
        count = MOCK_DEVICE["channel_count"]
        if index >= count:
            raise _CommandError(
                ErrorCode.INVALID_PAYLOAD, f"channel_index 超出范围（0..{count - 1}）"
            )
        return index

    def _cmd_open_device(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_device_id(payload)
        if self._device_open:
            raise _CommandError(ErrorCode.DEVICE_ALREADY_OPEN, "设备已处于打开状态")
        self._device_open = True
        self._channels = {}
        logger.info("mock 设备已打开：%s", MOCK_DEVICE["device_id"])
        return {"device_id": MOCK_DEVICE["device_id"], "state": "open"}

    def _cmd_close_device(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_device_id(payload)
        self._require_open()
        running = [i for i, st in self._channels.items() if st["running"]]
        if running:
            raise _CommandError(
                ErrorCode.INVALID_STATE,
                f"仍有通道运行中（通道 {running}），请先 stop_channel",
            )
        self._device_open = False
        self._channels = {}
        logger.info("mock 设备已关闭：%s", MOCK_DEVICE["device_id"])
        return {"device_id": MOCK_DEVICE["device_id"], "state": "closed"}

    def _cmd_configure_channel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_device_id(payload)
        self._require_open()
        index = self._require_channel_index(payload)
        state = self._channels.get(index)
        if state and state["running"]:
            raise _CommandError(
                ErrorCode.INVALID_STATE, f"通道 {index} 正在运行，请先 stop_channel"
            )
        bitrate = payload.get("bitrate_arbitration")
        if bitrate is not None and (
            not isinstance(bitrate, int) or isinstance(bitrate, bool) or bitrate <= 0
        ):
            raise _CommandError(ErrorCode.INVALID_PAYLOAD, "bitrate_arbitration 必须是正整数")
        self._channels[index] = {"configured": True, "running": False}
        logger.info("通道 %d 已配置", index)
        return {"channel_index": index, "state": "configured"}

    def _cmd_start_channel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_device_id(payload)
        self._require_open()
        index = self._require_channel_index(payload)
        state = self._channels.get(index)
        if state is None or not state["configured"]:
            raise _CommandError(
                ErrorCode.CHANNEL_NOT_CONFIGURED, f"通道 {index} 未配置，请先 configure_channel"
            )
        if state["running"]:
            raise _CommandError(ErrorCode.CHANNEL_ALREADY_RUNNING, f"通道 {index} 已在运行")
        state["running"] = True
        logger.info("通道 %d 已启动", index)
        return {"channel_index": index, "state": "running"}

    def _cmd_stop_channel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_device_id(payload)
        self._require_open()
        index = self._require_channel_index(payload)
        state = self._channels.get(index)
        if state is None or not state["running"]:
            raise _CommandError(ErrorCode.CHANNEL_NOT_RUNNING, f"通道 {index} 未在运行")
        state["running"] = False
        logger.info("通道 %d 已停止", index)
        return {"channel_index": index, "state": "stopped"}

    def _cmd_send_frame(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_device_id(payload)
        self._require_open()
        index = self._require_channel_index(payload)
        state = self._channels.get(index)
        if state is None or not state["running"]:
            raise _CommandError(
                ErrorCode.CHANNEL_NOT_RUNNING, f"通道 {index} 未运行，无法发送帧"
            )
        can_id = payload.get("can_id")
        if not isinstance(can_id, int) or isinstance(can_id, bool) or can_id < 0:
            raise _CommandError(ErrorCode.INVALID_PAYLOAD, "can_id 必须是非负整数")
        data_hex = payload.get("data_hex")
        if not isinstance(data_hex, str) or len(data_hex) % 2 != 0:
            raise _CommandError(ErrorCode.INVALID_PAYLOAD, "data_hex 必须是偶数长度的十六进制字符串")
        try:
            bytes.fromhex(data_hex)
        except ValueError as exc:
            raise _CommandError(ErrorCode.INVALID_PAYLOAD, "data_hex 含非法字符") from exc
        return {"requested": 1, "accepted": 1, "channel_index": index}

    def _cmd_subscribe_rx(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_device_id(payload)
        self._require_open()
        interval_ms = payload.get("interval_ms")
        if interval_ms is not None:
            if not isinstance(interval_ms, (int, float)) or isinstance(interval_ms, bool):
                raise _CommandError(ErrorCode.INVALID_PAYLOAD, "interval_ms 必须是数字")
            self.rx_interval = min(1.0, max(0.01, float(interval_ms) / 1000.0))
        if not self._rx_subscribed:
            self._rx_subscribed = True
            self._rx_stop.clear()
            self._rx_thread = threading.Thread(
                target=self._rx_loop, name="fake-host-rx", daemon=True
            )
            self._rx_thread.start()
            logger.info("rx 订阅已建立（周期 %.3fs）", self.rx_interval)
        return {"subscribed": True, "event_type": EventType.RX_FRAME.value}

    def _cmd_unsubscribe_rx(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self._rx_subscribed:
            raise _CommandError(ErrorCode.SUBSCRIPTION_NOT_FOUND, "当前没有 rx 订阅")
        self._stop_rx()
        logger.info("rx 订阅已取消")
        return {"subscribed": False}

    def _cmd_emergency_stop(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        # 急停：无论当前状态如何都收敛到安全态（幂等）
        had_running = any(st["running"] for st in self._channels.values())
        for state in self._channels.values():
            state["running"] = False
        was_open = self._device_open
        self._device_open = False
        self._channels = {}
        self._stop_rx()
        logger.warning("执行急停：停止发送=%s，复位通道=%s，关闭设备=%s", had_running, had_running, was_open)
        return {
            "sends_stopped": True,
            "queues_cleared": True,
            "channels_reset": had_running,
            "device_closed": was_open,
        }

    _HANDLERS = {
        HostCommand.PING: _cmd_ping,
        HostCommand.GET_HOST_INFO: _cmd_get_host_info,
        HostCommand.LIST_DEVICES: _cmd_list_devices,
        HostCommand.OPEN_DEVICE: _cmd_open_device,
        HostCommand.CLOSE_DEVICE: _cmd_close_device,
        HostCommand.CONFIGURE_CHANNEL: _cmd_configure_channel,
        HostCommand.START_CHANNEL: _cmd_start_channel,
        HostCommand.STOP_CHANNEL: _cmd_stop_channel,
        HostCommand.SEND_FRAME: _cmd_send_frame,
        HostCommand.SUBSCRIBE_RX: _cmd_subscribe_rx,
        HostCommand.UNSUBSCRIBE_RX: _cmd_unsubscribe_rx,
        HostCommand.EMERGENCY_STOP: _cmd_emergency_stop,
    }

    # ------------------------------------------------------------------
    # rx 事件推送
    # ------------------------------------------------------------------

    def _rx_loop(self) -> None:
        """周期推送模拟接收帧，写失败即自停。"""
        while not self._rx_stop.wait(self.rx_interval):
            server = self._server
            if server is None or not self._rx_subscribed:
                return
            self._rx_seq += 1
            event = build_event(
                EventType.RX_FRAME.value,
                {
                    "device_id": MOCK_DEVICE["device_id"],
                    "channel": 0,
                    "can_id": 0x100 + (self._rx_seq % 0x700),
                    "data_hex": f"{self._rx_seq:016x}",
                    "is_fd": False,
                    "is_extended": False,
                    "device_timestamp": self._rx_seq * 1000,
                    "device_timestamp_unit": "us",
                    "host_monotonic_ns": time.monotonic_ns(),
                    "wall_clock": time.time(),
                },
            )
            try:
                with self._write_lock:
                    write_message(server.write, event)
            except (ConnectionError, OSError) as exc:
                logger.info("rx 事件推送失败，自动停止订阅：%s", exc)
                self._rx_subscribed = False
                return

    def _stop_rx(self) -> None:
        self._rx_subscribed = False
        self._rx_stop.set()
        thread = self._rx_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._rx_thread = None

    def _reset_session(self) -> None:
        """连接断开后复位会话状态（新客户端从干净状态开始）。"""
        self._stop_rx()
        self._device_open = False
        self._channels = {}
        self._rx_seq = 0


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m zlg_ai_test_engine.infrastructure.ipc.fake_host --pipe <名字>``"""
    parser = argparse.ArgumentParser(description="HOST-01 Fake Host（模拟设备，无真实 DLL）")
    parser.add_argument(
        "--pipe",
        required=True,
        help="Named Pipe 名称（短名或完整 \\\\.\\pipe\\... 路径）",
    )
    parser.add_argument(
        "--rx-interval",
        type=float,
        default=0.05,
        help="模拟 rx 事件推送周期（秒，默认 0.05）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s [fake-host %(process)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    host = FakeHost(args.pipe, rx_interval=args.rx_interval)
    try:
        host.serve()
    except KeyboardInterrupt:
        logger.info("收到中断信号，Fake Host 停止。")
        host.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

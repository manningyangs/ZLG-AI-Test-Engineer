"""HOST-01：Native Host IPC 消息协议（长度前缀帧 + UTF-8 JSON）。

架构背景（任务书 7.2）：zlgcan.dll 只能由独立 Native Host 进程加载，
Python 主进程经 Windows Named Pipe 与 Host 通信。本模块只定义
**传输无关** 的消息协议：帧格式、命令白名单、错误码、编解码辅助。

安全红线：
- 命令走白名单（``HostCommand``），未知命令一律拒绝，绝不按名字
  动态执行任意函数/路径；
- 单帧消息体上限 1 MiB，超长直接判定为协议违规；
- 协议版本 major 不同即拒绝（``PROTOCOL_VERSION_MISMATCH``），不静默继续。

不加载任何厂商 DLL，不依赖第三方库（仅标准库）。
"""

from __future__ import annotations

import json
import struct
import uuid
from enum import StrEnum
from typing import Any, Callable, Mapping

#: 协议版本。语义：major 变更不兼容，minor 向后兼容。
PROTOCOL_VERSION = "1.0"

#: 长度前缀：4 字节大端（不含前缀自身）。
_LENGTH_STRUCT = struct.Struct(">I")
LENGTH_PREFIX_SIZE = _LENGTH_STRUCT.size

#: 单帧消息体（UTF-8 JSON）上限：1 MiB。
MAX_MESSAGE_BYTES = 1 * 1024 * 1024

#: Named Pipe 设备目录（不含结尾反斜杠）。
_PIPE_ROOT = r"\\.\pipe"


# ---------------------------------------------------------------------------
# 命令白名单与事件类型
# ---------------------------------------------------------------------------


class HostCommand(StrEnum):
    """Host 命令白名单（client 与 fake_host 共用，集中定义）。

    不在本枚举中的命令名必须被拒绝；禁止把任意字符串映射到任意调用。
    """

    PING = "ping"
    GET_HOST_INFO = "get_host_info"
    LIST_DEVICES = "list_devices"
    OPEN_DEVICE = "open_device"
    CLOSE_DEVICE = "close_device"
    CONFIGURE_CHANNEL = "configure_channel"
    START_CHANNEL = "start_channel"
    STOP_CHANNEL = "stop_channel"
    SEND_FRAME = "send_frame"
    SUBSCRIBE_RX = "subscribe_rx"
    UNSUBSCRIBE_RX = "unsubscribe_rx"
    EMERGENCY_STOP = "emergency_stop"


#: 白名单集合（str 值），成员判断用 ``in``。
HOST_COMMANDS: frozenset[str] = frozenset(cmd.value for cmd in HostCommand)


class EventType(StrEnum):
    """Host 主动推送的事件类型。"""

    #: 模拟接收帧（subscribe_rx 订阅后周期推送）
    RX_FRAME = "rx_frame"


# ---------------------------------------------------------------------------
# 错误码
# ---------------------------------------------------------------------------


class ErrorCode(StrEnum):
    """协议/命令错误码（响应 error.code 取值）。"""

    #: 未知命令（不在白名单）
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    #: JSON 解析失败
    INVALID_JSON = "INVALID_JSON"
    #: 消息体超过 1 MiB
    MESSAGE_TOO_LARGE = "MESSAGE_TOO_LARGE"
    #: 协议版本 major 不兼容
    PROTOCOL_VERSION_MISMATCH = "PROTOCOL_VERSION_MISMATCH"
    #: 请求结构不合法（缺字段/类型错误）
    INVALID_REQUEST = "INVALID_REQUEST"
    #: 载荷参数不合法
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    #: 设备状态机不允许该操作
    INVALID_STATE = "INVALID_STATE"
    #: 设备不存在
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    #: 设备已打开
    DEVICE_ALREADY_OPEN = "DEVICE_ALREADY_OPEN"
    #: 设备未打开
    DEVICE_NOT_OPEN = "DEVICE_NOT_OPEN"
    #: 通道未配置
    CHANNEL_NOT_CONFIGURED = "CHANNEL_NOT_CONFIGURED"
    #: 通道已在运行
    CHANNEL_ALREADY_RUNNING = "CHANNEL_ALREADY_RUNNING"
    #: 通道未运行
    CHANNEL_NOT_RUNNING = "CHANNEL_NOT_RUNNING"
    #: 未订阅 rx 事件
    SUBSCRIPTION_NOT_FOUND = "SUBSCRIPTION_NOT_FOUND"
    #: Host 内部错误
    INTERNAL_ERROR = "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    """编解码层错误（帧非法、JSON 非法、消息超限等）。"""

    def __init__(self, code: ErrorCode | str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = str(code)
        self.message = message


class HostCommandError(Exception):
    """Host 返回 ``ok=false`` 时抛出，携带错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class HostClientError(Exception):
    """HostClient 本地错误基类。"""


class DuplicateRequestError(HostClientError):
    """重复使用同一 request_id 发送请求。"""


# ---------------------------------------------------------------------------
# Pipe 名称辅助
# ---------------------------------------------------------------------------


def normalize_pipe_name(name: str) -> str:
    """把短名（如 ``zlg-ate-host-x``）补全为完整路径 ``\\\\.\\pipe\\...``。"""
    if name.startswith(_PIPE_ROOT + "\\"):
        return name
    if name.startswith(_PIPE_ROOT):
        return name
    return _PIPE_ROOT + "\\" + name.lstrip("\\")


def generate_pipe_name() -> str:
    """生成随机 pipe 名：``zlg-ate-host-<随机>``（避免多实例冲突）。"""
    return f"zlg-ate-host-{uuid.uuid4().hex[:12]}"


def protocol_major(version: Any) -> str | None:
    """取版本号 major 部分；非法输入返回 None。"""
    if not isinstance(version, str):
        return None
    major = version.split(".")[0]
    return major if major.isdigit() else None


def is_version_compatible(version: Any, local: str = PROTOCOL_VERSION) -> bool:
    """major 相同即兼容；无法解析视为不兼容。"""
    return protocol_major(version) == protocol_major(local)


# ---------------------------------------------------------------------------
# 消息构造
# ---------------------------------------------------------------------------


def build_request(
    command: str,
    request_id: str,
    payload: Mapping[str, Any] | None = None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
) -> dict[str, Any]:
    """构造请求消息。"""
    return {
        "protocol_version": protocol_version,
        "request_id": request_id,
        "command": command,
        "payload": dict(payload) if payload else {},
    }


def build_response(
    request_id: str | None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    result: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造响应消息（ok 由 error 是否存在决定）。"""
    return {
        "protocol_version": protocol_version,
        "request_id": request_id,
        "ok": error is None,
        "result": dict(result) if result else {},
        "error": dict(error) if error else None,
    }


def build_error_response(
    request_id: str | None,
    code: ErrorCode | str,
    message: str,
    *,
    protocol_version: str = PROTOCOL_VERSION,
) -> dict[str, Any]:
    """构造错误响应。"""
    return build_response(
        request_id,
        protocol_version=protocol_version,
        error={"code": str(code), "message": message},
    )


def build_event(
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    event_id: str | None = None,
) -> dict[str, Any]:
    """构造事件消息。"""
    return {
        "protocol_version": protocol_version,
        "event_id": event_id or uuid.uuid4().hex,
        "event_type": event_type,
        "payload": dict(payload) if payload else {},
    }


# ---------------------------------------------------------------------------
# 帧编解码（4 字节大端长度 + UTF-8 JSON，绝不依赖换行截断）
# ---------------------------------------------------------------------------


def encode_frame(message: Mapping[str, Any]) -> bytes:
    """把消息编码为完整帧字节（含长度前缀）。超限抛 ``ProtocolError``。"""
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            ErrorCode.MESSAGE_TOO_LARGE, f"消息体 {len(body)} 字节超过上限 {MAX_MESSAGE_BYTES}"
        )
    return _LENGTH_STRUCT.pack(len(body)) + body


def decode_frame_body(body: bytes) -> Any:
    """解析帧体（UTF-8 JSON）。非法 JSON 抛 ``ProtocolError``。"""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(ErrorCode.INVALID_JSON, "帧体不是合法的 UTF-8") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ErrorCode.INVALID_JSON, "帧体不是合法的 JSON") from exc


def read_exact(read_fn: Callable[[int], bytes], size: int) -> bytes | None:
    """从 ``read_fn`` 精确读取 ``size`` 字节（处理短读）。

    返回 ``None`` 表示对端关闭（EOF，读到 0 字节）。
    """
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = read_fn(remaining)
        if not chunk:
            if chunks:
                raise ProtocolError(ErrorCode.INVALID_REQUEST, "帧在读取中途被截断")
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def write_all(write_fn: Callable[[bytes], Any], data: bytes) -> None:
    """经 ``write_fn`` 完整写出 ``data``（处理短写）。"""
    view = memoryview(data)
    while view:
        written = write_fn(bytes(view))
        if not written:
            raise ConnectionError("写入 Named Pipe 失败：对端已关闭")
        view = view[written:]


def read_message(read_fn: Callable[[int], bytes]) -> dict[str, Any] | None:
    """读取一帧并解析为 dict。

    - 返回 ``None``：对端关闭（EOF）；
    - 抛 ``ProtocolError(MESSAGE_TOO_LARGE)``：长度前缀超限（帧体不读，流已失步，调用方应断开）；
    - 抛 ``ProtocolError(INVALID_JSON)``：帧体非法（帧边界完好，可继续）。
    """
    header = read_exact(read_fn, LENGTH_PREFIX_SIZE)
    if header is None:
        return None
    (length,) = _LENGTH_STRUCT.unpack(header)
    if length > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            ErrorCode.MESSAGE_TOO_LARGE, f"帧体声明 {length} 字节，超过上限 {MAX_MESSAGE_BYTES}"
        )
    body = read_exact(read_fn, length)
    if body is None:
        raise ProtocolError(ErrorCode.INVALID_REQUEST, "帧体缺失（对端提前关闭）")
    message = decode_frame_body(body)
    if not isinstance(message, dict):
        raise ProtocolError(ErrorCode.INVALID_REQUEST, "消息必须是 JSON 对象")
    return message


def write_message(write_fn: Callable[[bytes], Any], message: Mapping[str, Any]) -> None:
    """编码并完整写出一帧消息。"""
    write_all(write_fn, encode_frame(message))

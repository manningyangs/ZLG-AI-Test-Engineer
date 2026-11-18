"""HOST-01：Native Host IPC（Windows Named Pipe + 长度前缀 JSON 帧）。

Python 主进程与独立 Host 进程的通信骨架（P1 为 Fake Host，无真实 DLL）。
协议说明见 ``docs/api/ipc.md``。

服务端以独立进程运行（不在此导入，避免 ``-m`` 运行时的重复导入警告）::

    python -m zlg_ai_test_engine.infrastructure.ipc.fake_host --pipe zlg-ate-host-xxx
"""

from __future__ import annotations

from zlg_ai_test_engine.infrastructure.ipc.client import (
    FAKE_HOST_MODULE,
    DisconnectedCallback,
    EventCallback,
    HostClient,
    PipeChannel,
)
from zlg_ai_test_engine.infrastructure.ipc.protocol import (
    HOST_COMMANDS,
    LENGTH_PREFIX_SIZE,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    DuplicateRequestError,
    ErrorCode,
    EventType,
    HostClientError,
    HostCommand,
    HostCommandError,
    ProtocolError,
    build_error_response,
    build_event,
    build_request,
    build_response,
    decode_frame_body,
    encode_frame,
    generate_pipe_name,
    is_version_compatible,
    normalize_pipe_name,
    read_message,
    write_message,
)

__all__ = [
    "HOST_COMMANDS",
    "LENGTH_PREFIX_SIZE",
    "MAX_MESSAGE_BYTES",
    "PROTOCOL_VERSION",
    "DisconnectedCallback",
    "DuplicateRequestError",
    "ErrorCode",
    "EventCallback",
    "EventType",
    "FAKE_HOST_MODULE",
    "HostClient",
    "HostClientError",
    "HostCommand",
    "HostCommandError",
    "ProtocolError",
    "build_error_response",
    "build_event",
    "build_request",
    "build_response",
    "decode_frame_body",
    "encode_frame",
    "generate_pipe_name",
    "is_version_compatible",
    "normalize_pipe_name",
    "read_message",
    "write_message",
    "PipeChannel",
]

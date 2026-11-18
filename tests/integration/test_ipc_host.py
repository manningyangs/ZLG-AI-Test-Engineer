"""集成测试：HOST-01 Native Host IPC（Named Pipe + Fake Host）。

覆盖任务书 HOST-01 验收项 1-7，另覆盖协议要求的非法 JSON / 超长帧 /
心跳。每条用例通过内部超时原语限时（request timeout、事件 wait 上限、
连接重试上限），所有子进程在 fixture teardown 中回收，不留孤儿进程。
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from zlg_ai_test_engine.infrastructure.ipc import (
    FAKE_HOST_MODULE,
    MAX_MESSAGE_BYTES,
    DuplicateRequestError,
    ErrorCode,
    EventType,
    HostClient,
    HostCommandError,
    PipeChannel,
    generate_pipe_name,
    read_message,
)
from zlg_ai_test_engine.infrastructure.ipc.fake_host import CtypesPipeServer

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

#: 单个用例内部等待上限（自控限时，无第三方超时插件）
_WAIT_SHORT = 5.0
_WAIT_LONG = 15.0


def _spawn_host(pipe_name: str) -> subprocess.Popen[bytes]:
    """独立拉起 fake_host 子进程（用于不经 HostClient 的裸协议测试）。"""
    env = os.environ.copy()
    src = str(SRC_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    return subprocess.Popen(
        [sys.executable, "-m", FAKE_HOST_MODULE, "--pipe", pipe_name],
        env=env,
    )


def _kill_proc(proc: subprocess.Popen[bytes] | None) -> None:
    """强制回收子进程（fixture/finally 用，保证不留孤儿）。"""
    if proc is None:
        return
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _open_channel(pipe_name: str, timeout: float = _WAIT_LONG) -> PipeChannel:
    """带重试地裸连管道（host 创建 pipe 需要时间）。"""
    deadline = time.monotonic() + timeout
    last: OSError | None = None
    while time.monotonic() < deadline:
        try:
            return PipeChannel(pipe_name)
        except OSError as exc:
            last = exc
            time.sleep(0.05)
    raise TimeoutError(f"裸连管道超时：{pipe_name}（{last}）")


@pytest.fixture
def host_client():
    """拉起 fake_host 子进程的客户端；teardown 必杀子进程。"""
    client = HostClient(spawn_host=True, connect_timeout=_WAIT_LONG, default_timeout=_WAIT_SHORT)
    try:
        client.start()
        yield client
    finally:
        client.close()
        _kill_proc(client.host_process)


class _SilentHost:
    """收下请求但故意不回的最小服务端（超时验收用）。"""

    def __init__(self) -> None:
        self.pipe_name = generate_pipe_name()
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.received: list[dict] = []
        self._server: CtypesPipeServer | None = None
        self._thread = threading.Thread(target=self._run, name="silent-host", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        server = CtypesPipeServer(self.pipe_name)
        self._server = server
        self.ready.set()  # 管道实例已创建，客户端此时即可连上
        try:
            if not server.connect():
                return
            message = read_message(server.read)  # 读走请求，但永不回复
            if message is not None:
                self.received.append(message)
            self.stop.wait(10.0)
        except OSError:
            pass
        finally:
            server.close()

    def close(self) -> None:
        self.stop.set()
        server = self._server
        if server is not None:
            server.close()  # 解除可能阻塞中的 connect/read
        self._thread.join(timeout=3.0)


@pytest.fixture
def silent_host():
    host = _SilentHost()
    try:
        yield host
    finally:
        host.close()


# ---------------------------------------------------------------------------
# 1. roundtrip：ping / get_host_info / list_devices
# ---------------------------------------------------------------------------


def test_roundtrip_ping_host_info_list_devices(host_client):
    pong = host_client.request("ping", timeout=_WAIT_SHORT)
    assert pong["pong"] is True

    info = host_client.request("get_host_info", timeout=_WAIT_SHORT)
    assert info["host_kind"] == "fake"
    assert info["protocol_version"].split(".")[0] == "1"
    assert "ping" in info["commands"]
    assert info["dll_loaded"] is False  # P1 未加载任何真实 DLL

    devices = host_client.request("list_devices", timeout=_WAIT_SHORT)["devices"]
    assert len(devices) == 1
    assert devices[0]["device_id"] == "mock-0"

    # 子进程由客户端管理，用例结束前应存活
    assert host_client.host_process is not None
    assert host_client.host_process.poll() is None


# ---------------------------------------------------------------------------
# 2. 未知命令被拒绝且 host 仍存活
# ---------------------------------------------------------------------------


def test_unknown_command_rejected_host_survives(host_client):
    with pytest.raises(HostCommandError) as exc_info:
        host_client.request("definitely_not_a_command", timeout=_WAIT_SHORT)
    assert exc_info.value.code == ErrorCode.UNKNOWN_COMMAND
    # host 进程仍存活且可继续服务
    assert host_client.host_process is not None
    assert host_client.host_process.poll() is None
    assert host_client.request("ping", timeout=_WAIT_SHORT)["pong"] is True


# ---------------------------------------------------------------------------
# 3. 请求超时（host 故意不回）→ TimeoutError
# ---------------------------------------------------------------------------


def test_request_timeout_raises_timeout_error(silent_host):
    assert silent_host.ready.wait(_WAIT_LONG), "静默 host 未进入等待状态"
    client = HostClient(pipe_name=silent_host.pipe_name, connect_timeout=_WAIT_LONG)
    try:
        client.start()
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            client.request("ping", timeout=0.8)
        elapsed = time.monotonic() - started
        # 限时断言：确实等到超时，且绝未无限挂起
        assert 0.5 <= elapsed < 10.0
        # 请求确实送达了静默 host（只是没有响应）
        assert silent_host.received, "静默 host 未收到请求"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 4. 重复 request_id → 报错
# ---------------------------------------------------------------------------


def test_duplicate_request_id_rejected(host_client):
    rid = "dup-request-id-fixed"
    assert host_client.request("ping", request_id=rid, timeout=_WAIT_SHORT)["pong"] is True
    with pytest.raises(DuplicateRequestError):
        host_client.request("ping", request_id=rid, timeout=_WAIT_SHORT)
    # 重复检测不影响正常服务
    assert host_client.request("ping", timeout=_WAIT_SHORT)["pong"] is True


# ---------------------------------------------------------------------------
# 5. 崩溃检测：kill host → on_disconnected；restart() 后恢复
# ---------------------------------------------------------------------------


def test_crash_detection_and_restart(host_client):
    disconnected = threading.Event()
    host_client.on_disconnected = disconnected.set

    proc = host_client.host_process
    assert proc is not None and proc.poll() is None
    proc.kill()
    assert disconnected.wait(_WAIT_LONG), "kill host 子进程后 on_disconnected 未触发"

    host_client.restart(spawn_timeout=_WAIT_LONG)
    assert host_client.connected
    assert host_client.request("ping", timeout=_WAIT_SHORT)["pong"] is True
    # restart 拉起的新子进程由 teardown 回收


# ---------------------------------------------------------------------------
# 6. 协议版本 major 不匹配 → PROTOCOL_VERSION_MISMATCH
# ---------------------------------------------------------------------------


def test_protocol_version_major_mismatch():
    client = HostClient(
        spawn_host=True,
        protocol_version="2.0",
        connect_timeout=_WAIT_LONG,
        default_timeout=_WAIT_SHORT,
    )
    try:
        client.start()
        with pytest.raises(HostCommandError) as exc_info:
            client.request("ping", timeout=_WAIT_SHORT)
        assert exc_info.value.code == ErrorCode.PROTOCOL_VERSION_MISMATCH
    finally:
        client.close()
        _kill_proc(client.host_process)


# ---------------------------------------------------------------------------
# 7. 事件订阅收到 rx 事件
# ---------------------------------------------------------------------------


def test_rx_event_subscription(host_client):
    events: list[dict] = []
    got = threading.Event()

    def on_rx(payload: dict) -> None:
        events.append(payload)
        got.set()

    host_client.subscribe(EventType.RX_FRAME, on_rx)
    try:
        host_client.request("open_device", {"device_id": "mock-0"}, timeout=_WAIT_SHORT)
        host_client.request(
            "subscribe_rx", {"device_id": "mock-0", "interval_ms": 30}, timeout=_WAIT_SHORT
        )
        assert got.wait(_WAIT_LONG), "订阅后未收到 rx_frame 事件"
        payload = events[0]
        assert payload["device_id"] == "mock-0"
        assert isinstance(payload["can_id"], int)
        assert isinstance(payload["data_hex"], str)
        host_client.request("unsubscribe_rx", {"device_id": "mock-0"}, timeout=_WAIT_SHORT)
        # 退订后不再产生新事件
        count_after_unsub = len(events)
        time.sleep(0.2)
        assert len(events) == count_after_unsub
    finally:
        host_client.unsubscribe(EventType.RX_FRAME, on_rx)


# ---------------------------------------------------------------------------
# 补充：设备状态机（非法状态返回错误码）
# ---------------------------------------------------------------------------


def test_device_state_machine_errors(host_client):
    # 未打开设备就启动通道
    with pytest.raises(HostCommandError) as exc_info:
        host_client.request(
            "start_channel", {"device_id": "mock-0", "channel_index": 0}, timeout=_WAIT_SHORT
        )
    assert exc_info.value.code == ErrorCode.DEVICE_NOT_OPEN

    host_client.request("open_device", {"device_id": "mock-0"}, timeout=_WAIT_SHORT)
    # 重复打开
    with pytest.raises(HostCommandError) as exc_info:
        host_client.request("open_device", {"device_id": "mock-0"}, timeout=_WAIT_SHORT)
    assert exc_info.value.code == ErrorCode.DEVICE_ALREADY_OPEN
    # 未知设备
    with pytest.raises(HostCommandError) as exc_info:
        host_client.request("open_device", {"device_id": "no-such"}, timeout=_WAIT_SHORT)
    assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND
    # 未配置就启动
    with pytest.raises(HostCommandError) as exc_info:
        host_client.request(
            "start_channel", {"device_id": "mock-0", "channel_index": 0}, timeout=_WAIT_SHORT
        )
    assert exc_info.value.code == ErrorCode.CHANNEL_NOT_CONFIGURED
    # 配置 → 启动 → 发送 → 停止 → 关闭 全链路
    host_client.request(
        "configure_channel",
        {"device_id": "mock-0", "channel_index": 0, "bitrate_arbitration": 500000},
        timeout=_WAIT_SHORT,
    )
    host_client.request(
        "start_channel", {"device_id": "mock-0", "channel_index": 0}, timeout=_WAIT_SHORT
    )
    with pytest.raises(HostCommandError) as exc_info:
        host_client.request(
            "stop_channel", {"device_id": "mock-0", "channel_index": 1}, timeout=_WAIT_SHORT
        )
    assert exc_info.value.code == ErrorCode.CHANNEL_NOT_RUNNING
    sent = host_client.request(
        "send_frame",
        {"device_id": "mock-0", "channel_index": 0, "can_id": 0x123, "data_hex": "deadbeef"},
        timeout=_WAIT_SHORT,
    )
    assert sent["accepted"] == 1
    # 有通道运行时关闭设备 → INVALID_STATE
    with pytest.raises(HostCommandError) as exc_info:
        host_client.request("close_device", {"device_id": "mock-0"}, timeout=_WAIT_SHORT)
    assert exc_info.value.code == ErrorCode.INVALID_STATE
    host_client.request(
        "stop_channel", {"device_id": "mock-0", "channel_index": 0}, timeout=_WAIT_SHORT
    )
    host_client.request("close_device", {"device_id": "mock-0"}, timeout=_WAIT_SHORT)


# ---------------------------------------------------------------------------
# 补充：非法 JSON / 超长帧（协议要求：返回错误或断开，host 不得崩溃）
# ---------------------------------------------------------------------------


def test_invalid_json_frame_rejected_host_survives():
    pipe_name = generate_pipe_name()
    proc = _spawn_host(pipe_name)
    try:
        channel = _open_channel(pipe_name)
        raw = b"{this is not json"
        channel.write(struct.pack(">I", len(raw)) + raw)
        response = read_message(channel.read)
        assert response is not None, "非法 JSON 后未收到错误响应"
        assert response["ok"] is False
        assert response["error"]["code"] == ErrorCode.INVALID_JSON
        channel.close()

        # host 进程仍存活：重新连接仍可 ping
        client = HostClient(pipe_name=pipe_name, connect_timeout=_WAIT_LONG)
        try:
            client.start()
            assert client.request("ping", timeout=_WAIT_SHORT)["pong"] is True
        finally:
            client.close()
    finally:
        _kill_proc(proc)


def test_oversized_frame_rejected_host_survives():
    pipe_name = generate_pipe_name()
    proc = _spawn_host(pipe_name)
    try:
        channel = _open_channel(pipe_name)
        # 只发长度头（声明 2 MiB > 1 MiB 上限），不发帧体
        channel.write(struct.pack(">I", MAX_MESSAGE_BYTES + 1024))
        response = read_message(channel.read)
        if response is not None:
            # 允许行为一：回错误响应
            assert response["ok"] is False
            assert response["error"]["code"] == ErrorCode.MESSAGE_TOO_LARGE
        # 允许行为二：直接断开（read_message 返回 None）
        channel.close()

        # host 进程仍存活：重新连接仍可 ping
        client = HostClient(pipe_name=pipe_name, connect_timeout=_WAIT_LONG)
        try:
            client.start()
            assert client.request("ping", timeout=_WAIT_SHORT)["pong"] is True
        finally:
            client.close()
    finally:
        _kill_proc(proc)


# ---------------------------------------------------------------------------
# 补充：心跳（可配置间隔定期 ping，连接保持可用）
# ---------------------------------------------------------------------------


def test_heartbeat_keeps_connection_alive():
    client = HostClient(
        spawn_host=True, ping_interval=0.1, connect_timeout=_WAIT_LONG, default_timeout=_WAIT_SHORT
    )
    try:
        client.start()
        time.sleep(0.6)  # 心跳线程完成数轮 ping
        assert client.connected
        assert client.request("ping", timeout=_WAIT_SHORT)["pong"] is True
    finally:
        client.close()
        _kill_proc(client.host_process)

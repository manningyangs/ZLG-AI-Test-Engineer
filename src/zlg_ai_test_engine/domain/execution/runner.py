"""EXEC-01a：执行计划运行器（PlanRunner）。

消费 COMP-01 编译出的计划命令序列，在 DeviceSDKAdapter（MVP 为 MockAdapter）
上确定性执行，并采集断言所需证据（帧、信号采样、监听窗口）。

前提：设备已由编排器在 PreparingDevice 阶段 open_device。

要点：
- 虚拟时间全部来自 adapter.now_ms / adapter.run / adapter.advance（Mock），不读系统时钟；
- 每条命令与周期发送循环都轮询 cancel_check，保证可取消；
- 计划 cleanup 四件套逐项执行，单项清理失败**单独记录**（cleanup_errors）；
- 周期发送次数受计划 resources.max_frames 上限保护。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from zlg_ai_test_engine.domain.plugins.contracts import ChannelConfig, DeviceSDKAdapter, Frame

NS_PER_MS = 1_000_000


class RunnerError(Exception):
    """计划执行期错误（设备/发送/参数类失败）。"""


@dataclass
class RunnerEvidence:
    """一次运行采集的原始证据（虚拟时间 ms；窗口换算为 ns 槽位）。"""

    frames: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    #: step_id → (start_ns, end_ns)，来自 listen/monitor/wait 步骤
    windows: dict[str, tuple[int, int]] = field(default_factory=dict)

    def combined_window(self) -> tuple[int, int] | None:
        """全部监听窗口的并集（assert_timeout 判定用）。"""
        if not self.windows:
            return None
        return (
            min(w[0] for w in self.windows.values()),
            max(w[1] for w in self.windows.values()),
        )

    def arrivals_ns(self, can_id: int) -> list[int]:
        """指定 CAN ID 的全部到达时刻（ns）。"""
        return sorted(f["t_ns"] for f in self.frames if f["can_id"] == can_id)


@dataclass
class RunnerOutcome:
    status: str  # completed | cancelled | error
    evidence: RunnerEvidence
    executed_seqs: list[int] = field(default_factory=list)
    error: str | None = None
    cleanup_errors: list[str] = field(default_factory=list)
    clock: str = "host_monotonic_ns(mock_virtual)"


class PlanRunner:
    """把计划命令翻译为 DeviceSDKAdapter 调用并采集证据。"""

    def __init__(
        self,
        adapter: DeviceSDKAdapter,
        *,
        device_id: str = "mock-0",
        message_ids: Mapping[str, int] | None = None,
    ) -> None:
        self._adapter = adapter
        self._device_id = device_id
        #: message 名 → CAN ID（COMP-01 对 message_ref 型发送不产出 can_id，
        #: 名称解析由执行侧负责；未注册的引用必须报错而不是猜测）
        self._message_ids = dict(message_ids or {})

    def _resolve_can_id(self, params: Mapping[str, Any]) -> int:
        if "can_id" in params and params["can_id"] is not None:
            return int(params["can_id"])
        ref = params.get("message_ref")
        if isinstance(ref, Mapping) and ref.get("message"):
            name = str(ref["message"])
            if name in self._message_ids:
                return int(self._message_ids[name])
            raise RunnerError(
                f"message 引用 {name!r} 未解析（需 message_ids 映射，"
                "通常由 NetworkModel/VALID-01 提供），禁止猜测 CAN ID"
            )
        raise RunnerError("发送命令缺少 can_id 或 message_ref")

    # -- 主循环 ---------------------------------------------------------------

    def run(
        self, plan: Mapping[str, Any], *, cancel_check: Callable[[], bool] | None = None
    ) -> RunnerOutcome:
        evidence = RunnerEvidence()
        outcome = RunnerOutcome(status="completed", evidence=evidence)
        adapter = self._adapter
        cancel_check = cancel_check or (lambda: False)
        receiving = False
        started_channels: set[int] = set()

        max_frames = int((plan.get("resources") or {}).get("max_frames") or 1_000_000)

        def ensure_receiving() -> None:
            nonlocal receiving
            if not receiving:
                adapter.start_receiving(self._device_id, lambda _f: None)
                receiving = True

        def collect_frames(mark: int) -> None:
            for tf in adapter.received_frames[mark:]:
                evidence.frames.append(
                    {
                        "t_ms": tf.time_ms,
                        "t_ns": tf.time_ms * NS_PER_MS,
                        "channel": tf.frame.channel,
                        "can_id": tf.frame.can_id,
                        "data_hex": tf.frame.data.hex(),
                        "is_fd": tf.frame.is_fd,
                    }
                )

        try:
            for cmd in plan.get("commands", []):
                if cancel_check():
                    outcome.status = "cancelled"
                    break
                seq = cmd.get("seq")
                ctype = cmd.get("type")
                params = cmd.get("params", {}) or {}
                step_id = cmd.get("step_id")
                outcome.executed_seqs.append(seq)

                if ctype == "configure_channel":
                    bus = str(params.get("bus_type", "CAN"))
                    adapter.configure_channel(
                        self._device_id,
                        ChannelConfig(
                            channel_index=int(params.get("channel_index", 0)),
                            bus_type=bus,
                            bitrate_arbitration=500_000,
                            bitrate_data=2_000_000 if bus == "CAN_FD" else None,
                            listen_only=bool(params.get("listen_only", True)),
                        ),
                    )
                elif ctype == "start_channel":
                    ch = int(params.get("channel_index", 0))
                    adapter.start_channel(self._device_id, ch)
                    started_channels.add(ch)
                elif ctype in ("listen", "monitor", "wait"):
                    ensure_receiving()
                    duration = int(params.get("duration_ms") or params.get("timeout_ms") or 0)
                    frame_mark = len(adapter.received_frames)
                    sig_mark = len(adapter.signal_history)
                    t0 = adapter.now_ms
                    if duration > 0:
                        adapter.run(duration)
                    t1 = adapter.now_ms
                    collect_frames(frame_mark)
                    for ts in adapter.signal_history[sig_mark:]:
                        evidence.signals.append(
                            {
                                "t_ms": ts.time_ms,
                                "t_ns": ts.time_ms * NS_PER_MS,
                                "can_id": ts.can_id,
                                "data_hex": ts.data.hex(),
                            }
                        )
                    if step_id and ctype in ("listen", "monitor"):
                        evidence.windows[str(step_id)] = (t0 * NS_PER_MS, t1 * NS_PER_MS)
                elif ctype == "send_frame":
                    frame = Frame(
                        channel=int(params.get("channel", 0)),
                        can_id=self._resolve_can_id(params),
                        data=bytes.fromhex(str(params.get("payload_hex", ""))),
                        is_fd=bool(params.get("is_fd", False)),
                    )
                    result = adapter.send_frame(self._device_id, frame)
                    if result.accepted < 1:
                        raise RunnerError(
                            f"send_frame 被设备拒绝（seq={seq}, error_code={result.error_code}）"
                        )
                elif ctype == "send_periodic":
                    self._run_periodic(params, outcome, cancel_check, max_frames)
                    if outcome.status == "cancelled":
                        break
                elif ctype == "assert":
                    continue  # 断言在 DeterministicEvaluation 阶段由判定引擎消费
                else:
                    raise RunnerError(f"未知计划命令类型: {ctype}（seq={seq}）")

        except Exception as exc:  # noqa: BLE001 — 收敛为 outcome，由编排器定级
            outcome.status = "error"
            outcome.error = f"{type(exc).__name__}: {exc}"
        finally:
            outcome.cleanup_errors = self._run_cleanup(receiving, started_channels)

        return outcome

    # -- 周期发送 --------------------------------------------------------------

    def _run_periodic(
        self,
        params: Mapping[str, Any],
        outcome: RunnerOutcome,
        cancel_check: Callable[[], bool],
        max_frames: int,
    ) -> None:
        period = int(params.get("period_ms", 0))
        if period <= 0:
            raise RunnerError(f"send_periodic 非法 period_ms={period}")
        count = params.get("count")
        duration = params.get("duration_ms")
        start_delay = int(params.get("start_delay_ms", 0) or 0)
        can_id = self._resolve_can_id(params)
        payload = bytes.fromhex(str(params.get("payload_hex", "")))
        channel = int(params.get("channel", 0))

        planned: int | None = int(count) if count is not None else None
        if duration is not None:
            by_duration = int(duration) // period + 1
            planned = by_duration if planned is None else min(planned, by_duration)
        if planned is None:
            raise RunnerError("send_periodic 缺少 count 或 duration_ms（无界发送被拒绝）")
        if planned > max_frames:
            raise RunnerError(
                f"send_periodic 计划发送 {planned} 帧超过计划上限 max_frames={max_frames}"
            )

        if start_delay > 0:
            self._adapter.advance(start_delay)
        for _ in range(planned):
            if cancel_check():
                outcome.status = "cancelled"
                return
            result = self._adapter.send_frame(
                self._device_id,
                Frame(channel=channel, can_id=can_id, data=payload),
            )
            if result.accepted < 1:
                raise RunnerError(f"send_periodic 发送被拒绝（error_code={result.error_code}）")
            self._adapter.advance(period)

    # -- 清理 -----------------------------------------------------------------

    def _run_cleanup(self, receiving: bool, started_channels: set[int]) -> list[str]:
        """执行计划 cleanup 四件套；每项独立 try/except，失败单独记录。"""
        errors: list[str] = []
        adapter = self._adapter
        if receiving:
            try:
                adapter.stop_receiving(self._device_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"stop_receiving: {type(exc).__name__}: {exc}")
        for ch in sorted(started_channels):
            try:
                adapter.stop_channel(self._device_id, ch)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"reset_channel[{ch}]: {type(exc).__name__}: {exc}")
        try:
            adapter.close_device(self._device_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"close_device: {type(exc).__name__}: {exc}")
        return errors

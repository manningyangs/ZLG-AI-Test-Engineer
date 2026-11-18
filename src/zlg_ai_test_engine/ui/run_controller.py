"""UI-02：运行控制器（orchestrator.start 的后台执行与信号中继）。

- ``state_listener`` 回调可能发生在工作线程：控制器以 Qt Signal 发出
  ``state_changed``，由 Qt 自动按接收方线程投递（QueuedConnection），
  保证 UI 刷新始终在主线程；
- ``start_run`` 在 QThread 中执行阻塞的 ``orch.start``；
- ``run_blocking`` 提供同一完成路径的同步执行（供测试同步调用）；
- ``cancel`` 线程安全置取消标志（orchestrator.cancel）。

红线：控制器不判定结果、不触碰设备，仅编排与转发。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from zlg_ai_test_engine.domain.execution.orchestrator import ExecutionOrchestrator


class _StartThread(QThread):
    """后台执行 ``orch.start`` 的工作线程。"""

    succeeded = Signal(object)  # RunRecord
    errored = Signal(str)

    def __init__(
        self,
        orchestrator: ExecutionOrchestrator,
        run_id: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._run_id = run_id

    def run(self) -> None:  # noqa: D102 — QThread 工作函数
        try:
            rec = self._orchestrator.start(self._run_id)
        except Exception as exc:  # noqa: BLE001 — 执行异常收敛为 failed 信号
            self.errored.emit(f"{type(exc).__name__}: {exc}")
            return
        self.succeeded.emit(rec)


class RunController(QObject):
    """运行控制器：提交后台 start、转发状态迁移、处理取消。"""

    #: (run_id, state) —— 每次状态迁移（可能来自工作线程）转主线程
    state_changed = Signal(str, str)
    #: 运行到达终态（或 start 正常返回）
    finished = Signal(object)
    #: start 抛出异常 / 取消失败等错误
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: _StartThread | None = None
        self._orchestrator: ExecutionOrchestrator | None = None
        self._run_id: str = ""

    # ------------------------------------------------------------------ 状态

    def on_record(self, rec: Any) -> None:
        """orchestrator 的 state_listener 回调（工作线程安全：Signal 跨线程投递）。"""
        self.state_changed.emit(str(rec.run_id), str(rec.state))

    @property
    def is_busy(self) -> bool:
        """是否有后台运行中的 start。"""
        return self._thread is not None and self._thread.isRunning()

    @property
    def current_run_id(self) -> str:
        return self._run_id

    # ------------------------------------------------------------------ 执行

    def start_run(self, orchestrator: ExecutionOrchestrator, run_id: str) -> bool:
        """在 QThread 后台执行 ``orchestrator.start``（阻塞调用不卡 UI）。

        Returns:
            是否成功启动（已有运行进行中时为 False）。
        """
        if self.is_busy:
            return False
        self._orchestrator = orchestrator
        self._run_id = run_id
        thread = _StartThread(orchestrator, run_id)
        thread.succeeded.connect(self._handle_finished)
        thread.errored.connect(self._handle_failed)
        thread.finished.connect(lambda t=thread: self._on_thread_finished(t))
        self._thread = thread
        thread.start()
        return True

    def run_blocking(self, orchestrator: ExecutionOrchestrator, run_id: str) -> Any:
        """同步执行路径（测试用）：与后台路径共用 ``finished``/``failed`` 完成处理。

        Returns:
            终态 RunRecord；异常时返回 None 并发出 ``failed``。
        """
        self._orchestrator = orchestrator
        self._run_id = run_id
        try:
            rec = orchestrator.start(run_id)
        except Exception as exc:  # noqa: BLE001 — 收敛为 failed 信号
            self._handle_failed(f"{type(exc).__name__}: {exc}")
            return None
        self._handle_finished(rec)
        return rec

    def cancel(self, run_id: str, reason: str = "用户急停") -> bool:
        """线程安全置取消标志（Running）或直接迁移（未启动）。"""
        if self._orchestrator is None:
            return False
        try:
            self._orchestrator.cancel(run_id, reason=reason)
        except Exception as exc:  # noqa: BLE001 — 已终态等取消失败
            self._handle_failed(f"取消失败：{exc}")
            return False
        return True

    # ------------------------------------------------------------------ 内部

    def _handle_finished(self, rec: Any) -> None:
        self.finished.emit(rec)

    def _handle_failed(self, message: str) -> None:
        self.failed.emit(message)

    def _on_thread_finished(self, thread: QThread) -> None:
        if thread is self._thread:
            self._thread = None


__all__ = ["RunController"]

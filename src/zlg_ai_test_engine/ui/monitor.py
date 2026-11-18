"""UI-02：运行监控页（替换 execution 占位页）。

结构（任务书 P2-D UI-02）：
- 顶栏：run_id、状态、耗时计时、运行按钮、急停按钮；
- 左：步骤树（plan commands，运行后叠加断言 verdict 图标）；
- 中：Trace 表（运行中展示状态历史，运行结束填充 evidence.frames）；
- 右：统计（summary 四色计数）；
- 日志复用 MainWindow.append_log（底部抽屉“运行日志”）。

运行入口：编辑器当前 IR → orchestrator.submit →（AwaitingApproval 弹审批）
→ 后台 orchestrator.start（经 RunController）。急停接 orchestrator.cancel
并联动 MainWindow.set_emergency_enabled（运行中 True / 终态 False）。

红线：UI 不判定测试结果、不直接调 DLL/轮询设备，运行一律走 orchestrator。
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from zlg_ai_test_engine.domain.execution.orchestrator import TERMINAL_STATES
from zlg_ai_test_engine.ui.run_controller import RunController
from zlg_ai_test_engine.ui.theme import (
    COLOR_FAIL,
    COLOR_GRAPHITE,
    COLOR_PASS,
    COLOR_PENDING,
)

#: 终态 → 状态标签颜色
_STATE_COLORS: dict[str, str] = {
    "Completed": COLOR_PASS,
    "Failed": COLOR_FAIL,
    "Blocked": COLOR_PENDING,
    "Cancelled": COLOR_PENDING,
}

#: 断言 verdict → 颜色/字符（仅展示，不参与判定）
_VERDICT_STYLE: dict[str, tuple[str, str]] = {
    "Pass": (COLOR_PASS, "✓"),
    "Fail": (COLOR_FAIL, "✗"),
    "Blocked": (COLOR_PENDING, "!"),
    "NotEvaluated": ("#64748b", "—"),
}


class MonitorPage(QWidget):
    """运行监控页：状态条 / 步骤树 / Trace / 统计 / 急停。"""

    page_title = "执行中心"

    #: 运行到达终态（RunRecord）→ 结果页渲染
    run_finished = Signal(object)

    def __init__(
        self,
        *,
        controller: RunController,
        main_window: Any,
        editor: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._window = main_window
        self._editor = editor
        self._orchestrator: Any = None

        self._history: list[tuple[str, str, float]] = []  # (run_id, state, monotonic)
        self._current_run_id: str = ""
        self._current_rec: Any = None
        self._run_anchor: float | None = None
        self._assert_items: dict[str, QTreeWidgetItem] = {}

        self._build_ui()
        self.controller.state_changed.connect(self._on_state)
        self.controller.finished.connect(self._on_finished)
        self.controller.failed.connect(self._on_failed)

    # ------------------------------------------------------------------ 布局

    def _build_ui(self) -> None:
        # 顶栏状态条
        top = QHBoxLayout()
        top.setSpacing(12)

        self.run_id_label = QLabel("run: —")
        self.run_id_label.setStyleSheet(f"color: {COLOR_GRAPHITE}; background: transparent;")
        self.state_label = QLabel("空闲")
        self.state_label.setStyleSheet(f"color: {COLOR_GRAPHITE}; font-weight: bold; background: transparent;")
        self.elapsed_label = QLabel("00:00")
        self.elapsed_label.setStyleSheet(
            f"color: {COLOR_GRAPHITE}; background: #e9edf3; border-radius: 4px; padding: 3px 10px;"
        )

        self.run_button = QPushButton("▶ 运行当前用例")
        self.run_button.setObjectName("runButton")
        self.emergency_button = QPushButton("■ 急停")
        self.emergency_button.setObjectName("monitorEmergencyButton")
        self.emergency_button.setEnabled(False)
        self.emergency_button.setStyleSheet(
            "QPushButton { background-color: #dc2626; color: #ffffff;"
            " border: 1px solid #b91c1c; border-radius: 4px;"
            " padding: 5px 16px; font-weight: bold; }"
            "QPushButton:disabled { background-color: #f3b7b7; color: #f9e4e4; border: 1px solid #eab5b5; }"
        )

        top.addWidget(self.run_id_label)
        top.addWidget(self.state_label)
        top.addWidget(self.elapsed_label)
        top.addStretch(1)
        top.addWidget(self.run_button)
        top.addWidget(self.emergency_button)

        # 状态提示行
        self.status_label = QLabel("")
        self.status_label.setObjectName("monitorStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {COLOR_GRAPHITE}; background: transparent;")

        # 左：步骤树
        tree_group = QGroupBox("步骤树（计划命令 / 断言判定）")
        tree_layout = QVBoxLayout(tree_group)
        tree_layout.setContentsMargins(8, 10, 8, 8)
        self.step_tree = QTreeWidget()
        self.step_tree.setObjectName("stepTree")
        self.step_tree.setHeaderLabels(["计划命令", "步骤", "判定"])
        tree_layout.addWidget(self.step_tree)

        # 中：Trace / 状态历史
        trace_group = QGroupBox("报文 Trace / 状态历史")
        trace_layout = QVBoxLayout(trace_group)
        trace_layout.setContentsMargins(8, 10, 8, 8)
        self.trace_table = QTableWidget()
        self.trace_table.setObjectName("traceTable")
        self.trace_table.setColumnCount(4)
        self.trace_table.setHorizontalHeaderLabels(["t_ms", "通道", "CAN ID", "数据 / 状态"])
        self.trace_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.trace_table.verticalHeader().setVisible(False)
        trace_layout.addWidget(self.trace_table)

        # 右：统计
        stats_group = QGroupBox("统计（summary 四色计数）")
        stats_layout = QVBoxLayout(stats_group)
        stats_layout.setContentsMargins(8, 10, 8, 8)
        stats_layout.setSpacing(8)

        def stat_row(name: str, state: str) -> QLabel:
            label = QLabel(name)
            label.setObjectName(state)
            label.setProperty("state", state)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            stats_layout.addWidget(label)
            return label

        self.pass_label = stat_row("Pass: 0", "pass")
        self.fail_label = stat_row("Fail: 0", "fail")
        self.blocked_label = stat_row("Blocked: 0", "pending")
        self.ne_label = stat_row("NotEvaluated: 0", "neutral")
        self.total_label = QLabel("断言总数: 0")
        stats_layout.addWidget(self.total_label)
        stats_layout.addStretch(1)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(tree_group)
        body.addWidget(trace_group)
        body.addWidget(stats_group)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 5)
        body.setStretchFactor(2, 2)
        body.setSizes([340, 560, 220])

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addLayout(top)
        root.addWidget(self.status_label)
        root.addWidget(body, 1)

        # 计时器
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

        # 信号
        self.run_button.clicked.connect(self._on_run_clicked)
        self.emergency_button.clicked.connect(self.emergency_stop)

    # ------------------------------------------------------------------ 运行

    def set_orchestrator(self, orchestrator: Any) -> None:
        """注入装配好的离线管线（create_pages 时调用）。"""
        self._orchestrator = orchestrator

    def _ensure_orchestrator(self) -> Any:
        if self._orchestrator is not None:
            return self._orchestrator
        try:
            from zlg_ai_test_engine.application.pipeline import (  # noqa: PLC0415
                build_mock_orchestrator,
            )

            self._orchestrator = build_mock_orchestrator(
                state_listener=self.controller.on_record
            )
        except Exception as exc:  # noqa: BLE001 — 管线缺失降级提示
            self._set_status(f"执行管线不可用：{exc}", COLOR_FAIL)
            return None
        return self._orchestrator

    def _on_run_clicked(self) -> None:
        ir = self._editor.current_ir() if self._editor is not None else None
        if not ir:
            self._set_status("无可用用例：请先在用例编辑器中选择用例", COLOR_PENDING)
            return
        self.launch(dict(ir))

    def launch(self, ir: dict[str, Any], *, background: bool = True) -> Any:
        """提交 IR 并启动运行。

        Args:
            ir: Test IR 文档。
            background: True 走 QThread 后台执行；False 同步执行（测试用）。

        Returns:
            RunRecord（已启动运行）；不满足运行条件时为 None 并给出原因。
        """
        orchestrator = self._ensure_orchestrator()
        if orchestrator is None:
            return None
        if self.controller.is_busy:
            self._set_status("已有运行进行中，请先等待或急停", COLOR_PENDING)
            return None

        self._history.clear()
        self._current_rec = None
        self._run_anchor = None
        self._current_run_id = ""

        try:
            rec = orchestrator.submit(ir)
        except Exception as exc:  # noqa: BLE001 — 提交异常提示而非崩溃
            self._set_status(f"提交失败：{type(exc).__name__}: {exc}", COLOR_FAIL)
            return None

        if rec.state == "AwaitingApproval":
            if not self._ask_approval(rec):
                self._set_status("审批未通过，不运行", COLOR_PENDING)
                self._log(f"[审批] 用户拒绝 {rec.run_id[:8]}，不运行")
                return None
            rec = orchestrator.approve(rec.run_id, approver="user")

        if rec.state in ("ClarificationRequired", "Failed"):
            self._set_status(
                f"不满足运行条件（{rec.state}）：{rec.error or '未知原因'}", COLOR_FAIL
            )
            self._log(f"[运行] 提交被阻断 {rec.run_id[:8]}：{rec.error}")
            return None
        if rec.state != "Queued":
            self._set_status(f"当前状态不可启动：{rec.state}", COLOR_FAIL)
            return None
        return self._begin_run(orchestrator, rec, background=background)

    def _ask_approval(self, rec: Any) -> bool:
        """L1+ 审批确认框。"""
        title = rec.ir.get("title", rec.ir.get("case_id", "?"))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("运行审批")
        box.setText(f"用例「{title}」需要审批（受控发送，L1+）。是否批准运行？")
        approve_btn = box.addButton("批准", QMessageBox.ButtonRole.AcceptRole)
        deny_btn = box.addButton("拒绝", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(approve_btn)  # type: ignore[arg-type]
        box.exec()
        return box.clickedButton() == approve_btn

    def _begin_run(self, orchestrator: Any, rec: Any, *, background: bool) -> Any:
        """进入运行态：清空展示、启用急停、计时、启动执行。"""
        self._current_rec = rec
        self._current_run_id = str(rec.run_id)
        self._run_anchor = time.monotonic()
        self._assert_items = {}

        self.run_id_label.setText(f"run: {rec.run_id[:8]}…")
        self.elapsed_label.setText("00:00")
        self.trace_table.setRowCount(0)
        self._reset_stats()
        self._show_plan(rec)
        self._set_state(str(rec.state))

        # 急停联动：运行中启用（页面按钮 + 顶栏常驻急停）
        self.emergency_button.setEnabled(True)
        self.emergency_button.setToolTip("立即停止当前运行任务")
        self._window.set_emergency_enabled(True, "运行中：可随时急停")
        self._timer.start()

        self._log(
            f"[运行] 启动 {rec.run_id[:8]}：{rec.ir.get('title', rec.ir.get('case_id', ''))}"
        )
        self._set_status("运行中…", COLOR_PASS)

        if background:
            ok = self.controller.start_run(orchestrator, rec.run_id)
            if not ok:
                self._set_status("启动失败：后台执行器忙", COLOR_FAIL)
                self._end_run_ui("运行启动失败")
        else:
            self.controller.run_blocking(orchestrator, rec.run_id)
        return rec

    def emergency_stop(self) -> None:
        """急停：置取消标志（orchestrator.cancel）。"""
        if not self.emergency_button.isEnabled():
            self._set_status("急停无效：当前无运行任务", COLOR_PENDING)
            return
        run_id = self._current_run_id
        if not run_id:
            self._set_status("当前无运行任务", COLOR_PENDING)
            return
        ok = self.controller.cancel(run_id, reason="用户急停")
        if ok:
            self._set_status("已发送急停：取消标志已置位", COLOR_PENDING)
            self._log(f"[急停] {run_id[:8]} 取消标志已置位")
        else:
            self._set_status("急停失败：运行可能已结束", COLOR_FAIL)

    # ------------------------------------------------------------- 状态回调

    def _on_state(self, run_id: str, state: str) -> None:
        """状态迁移（可能来自工作线程 → Signal 已投递到主线程）。"""
        now = time.monotonic()
        self._history.append((run_id, state, now))
        if len(self._history) > 500:
            self._history = self._history[-500:]
        self._log(f"[状态] {run_id[:8]} → {state}")
        if run_id != self._current_run_id:
            return
        self._set_state(state)
        if state not in TERMINAL_STATES:
            self._append_state_row(state, now)

    def _on_finished(self, rec: Any) -> None:
        """运行到达终态：填充 Trace / 统计 / 步骤树判定，复位急停。"""
        if self._current_run_id and rec.run_id != self._current_run_id:
            return
        self._current_rec = rec
        self._timer.stop()
        self._set_state(str(rec.state))
        self.elapsed_label.setText(self._format_elapsed(rec))
        self._end_run_ui(f"运行结束：{rec.state} · {rec.summary}")
        self._fill_trace_from_evidence(rec)
        self._fill_stats(rec)
        self._overlay_verdicts(rec)
        self._log(f"[运行] {rec.run_id[:8]} 结束于 {rec.state}（{rec.summary}）")
        if rec.error:
            self._log(f"[运行] 错误：{rec.error}")
        self.run_finished.emit(rec)

    def _on_failed(self, message: str) -> None:
        self._timer.stop()
        self._end_run_ui(f"运行失败：{message}")
        self._set_status(f"运行失败：{message}", COLOR_FAIL)
        self._log(f"[运行] 失败：{message}")

    def _end_run_ui(self, status: str) -> None:
        """终态复位：禁用急停（页面 + 顶栏）并提示状态。"""
        self.emergency_button.setEnabled(False)
        self.emergency_button.setToolTip("当前无运行任务")
        self._window.set_emergency_enabled(False, "任务已结束")
        if status:
            self._set_status(status, COLOR_PENDING)

    def _on_tick(self) -> None:
        if self._run_anchor is None:
            return
        seconds = int(time.monotonic() - self._run_anchor)
        self.elapsed_label.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")

    # ------------------------------------------------------------- 展示填充

    def _show_plan(self, rec: Any) -> None:
        """步骤树：plan commands（断言命令登记以便叠加 verdict）。"""
        self.step_tree.clear()
        plan = rec.plan or {}
        commands = list(plan.get("commands", []))
        root = QTreeWidgetItem(
            [f"计划命令 {len(commands)} 条", str(plan.get("plan_version", "—")), "—"]
        )
        self.step_tree.addTopLevelItem(root)
        for cmd in commands:
            seq = cmd.get("seq", "?")
            ctype = str(cmd.get("type", "?"))
            step_id = str(cmd.get("step_id") or "—")
            action = cmd.get("action")
            label = f"{seq}. {ctype}" + (f"（{action}）" if action else "")
            item = QTreeWidgetItem([label, step_id, "—"])
            root.addChild(item)
            if ctype == "assert" and step_id != "—":
                self._assert_items[step_id] = item
        root.setExpanded(True)
        self.step_tree.expandAll()

    def _overlay_verdicts(self, rec: Any) -> None:
        """运行后：断言命令节点叠加 verdict 颜色与字符（仅展示）。"""
        for result in rec.results:
            step_id = str(result.step_id)
            item = self._assert_items.get(step_id)
            if item is None:
                continue
            verdict = str(result.verdict.value)
            color, glyph = _VERDICT_STYLE.get(verdict, ("#64748b", "?"))
            item.setText(2, f"{glyph} {verdict}")
            for column in range(3):
                item.setForeground(column, QBrush(QColor(color)))

    def _append_state_row(self, state: str, now: float) -> None:
        """运行中：Trace 表先展示状态历史行。"""
        anchor = self._run_anchor if self._run_anchor is not None else now
        elapsed = int((now - anchor) * 1000)
        row = self.trace_table.rowCount()
        self.trace_table.insertRow(row)
        for column, value in enumerate((str(elapsed), "—", "—", f"状态: {state}")):
            item = QTableWidgetItem(value)
            if column == 3:
                item.setForeground(QBrush(QColor(_STATE_COLORS.get(state, COLOR_GRAPHITE))))
            self.trace_table.setItem(row, column, item)

    def _fill_trace_from_evidence(self, rec: Any) -> None:
        """运行结束：有 evidence.frames 则填充报文行，否则保留状态历史。"""
        evidence = rec.evidence
        frames = list(getattr(evidence, "frames", []) or [])
        if not frames:
            # 无场景帧：用完整状态历史（含提交期状态）回填 Trace
            self._rebuild_state_history()
            return
        self.trace_table.setRowCount(0)
        for frame in frames:
            row = self.trace_table.rowCount()
            self.trace_table.insertRow(row)
            values = (
                str(frame.get("t_ms", "—")),
                str(frame.get("channel", "—")),
                f"0x{int(frame.get('can_id', 0)):X}",
                str(frame.get("data_hex", "")),
            )
            for column, value in enumerate(values):
                self.trace_table.setItem(row, column, QTableWidgetItem(value))
        self.trace_table.resizeColumnsToContents()

    def _rebuild_state_history(self) -> None:
        """无场景帧时：用完整状态历史回填 Trace 表（含提交期状态）。"""
        self.trace_table.setRowCount(0)
        entries = [entry for entry in self._history if entry[0] == self._current_run_id]
        if not entries:
            return
        anchor = entries[0][2]
        for _run_id, state, stamp in entries:
            elapsed = int((stamp - anchor) * 1000)
            row = self.trace_table.rowCount()
            self.trace_table.insertRow(row)
            for column, value in enumerate((str(elapsed), "—", "—", f"状态: {state}")):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setForeground(
                        QBrush(QColor(_STATE_COLORS.get(state, COLOR_GRAPHITE)))
                    )
                self.trace_table.setItem(row, column, item)

    def _reset_stats(self) -> None:
        self.pass_label.setText("Pass: 0")
        self.fail_label.setText("Fail: 0")
        self.blocked_label.setText("Blocked: 0")
        self.ne_label.setText("NotEvaluated: 0")
        self.total_label.setText("断言总数: 0")

    def _fill_stats(self, rec: Any) -> None:
        summary = dict(rec.summary)
        self.pass_label.setText(f"Pass: {summary.get('Pass', 0)}")
        self.fail_label.setText(f"Fail: {summary.get('Fail', 0)}")
        self.blocked_label.setText(f"Blocked: {summary.get('Blocked', 0)}")
        self.ne_label.setText(f"NotEvaluated: {summary.get('NotEvaluated', 0)}")
        self.total_label.setText(f"断言总数: {len(rec.results)}")

    # ------------------------------------------------------------------ 工具

    @staticmethod
    def _format_elapsed(rec: Any) -> str:
        if rec.started_at and rec.ended_at:
            seconds = max(0.0, (rec.ended_at - rec.started_at).total_seconds())
            return f"{seconds:.1f}s"
        return "—"

    def _set_state(self, state: str) -> None:
        self.state_label.setText(state)
        color = _STATE_COLORS.get(state, COLOR_GRAPHITE)
        self.state_label.setStyleSheet(
            f"color: {color}; font-weight: bold; background: transparent;"
        )

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background: transparent;")

    def _log(self, text: str) -> None:
        window = self._window
        append = getattr(window, "append_log", None)
        if callable(append):
            append(text)

    @property
    def state_history(self) -> list[tuple[str, str]]:
        """(run_id, state) 状态历史（测试与日志展示用）。"""
        return [(run_id, state) for run_id, state, _ in self._history]


__all__ = ["MonitorPage"]

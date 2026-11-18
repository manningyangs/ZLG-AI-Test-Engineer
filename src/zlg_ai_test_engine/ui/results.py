"""UI-02：结果页（替换 results 占位页）。

结构（任务书 P2-D UI-02）：
- 总览卡片：四色 verdict 计数、状态、耗时、adapter/device 信息；
- 断言表：step / verdict / reason / threshold / computed / evidence_refs
  （Fail 红、Pass 绿、Blocked 橙——仅展示颜色，不参与判定）；
- AI 解释区：标题注明“建议/推测，不覆盖机器判定”；
- 证据区：evidence.frames 行数与窗口时间；
- “导出 HTML 报告”：按 REPORT-01 契约 ``application.reporting`` 尝试导入，
  导入失败降级提示“报告模块未就绪”，绝不崩溃。

红线：UI 不判定测试结果、不触碰设备。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from zlg_ai_test_engine.ui.theme import (
    COLOR_FAIL,
    COLOR_GRAPHITE,
    COLOR_PASS,
    COLOR_PENDING,
)

#: verdict → 展示颜色（仅展示层着色）
_VERDICT_COLORS: dict[str, str] = {
    "Pass": COLOR_PASS,
    "Fail": COLOR_FAIL,
    "Blocked": COLOR_PENDING,
    "NotEvaluated": "#64748b",
}

#: 断言表列
_ASSERTION_COLUMNS = ("步骤", "判定", "原因", "阈值", "计算值", "证据引用")


def _load_reporting() -> Any:
    """按契约尝试导入 REPORT-01 模块。

    Returns:
        ``(build_run_result, HtmlReportExporter)``；模块未就绪时为 None。
    """
    try:
        from zlg_ai_test_engine.application.reporting import (  # noqa: PLC0415
            HtmlReportExporter,
            build_run_result,
        )
    except ImportError:
        return None
    return build_run_result, HtmlReportExporter


class ResultsPage(QWidget):
    """结果页：总览 / 断言表 / AI 解释 / 证据 / 报告导出。"""

    page_title = "结果中心"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rec: Any = None
        #: 显式导出目标（测试/自动化可预设；None 时弹保存对话框）
        self.export_target: Path | None = None
        self._build_ui()

    # ------------------------------------------------------------------ 布局

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # -- 总览卡片 -------------------------------------------------------
        overview = QHBoxLayout()
        overview.setSpacing(8)

        # 计数卡片（四色）
        count_group = QGroupBox("判定计数")
        count_group.setObjectName("countCard")
        count_layout = QHBoxLayout(count_group)
        count_layout.setContentsMargins(10, 12, 10, 10)
        count_layout.setSpacing(12)

        def count_label(text: str, state: str) -> QLabel:
            label = QLabel(text)
            label.setObjectName(f"overview_{state}")
            label.setProperty("state", state)
            font = label.font()
            font.setPointSize(12)
            font.setBold(True)
            label.setFont(font)
            count_layout.addWidget(label)
            return label

        self.pass_label = count_label("Pass 0", "pass")
        self.fail_label = count_label("Fail 0", "fail")
        self.blocked_label = count_label("Blocked 0", "pending")
        self.ne_label = count_label("NotEvaluated 0", "neutral")

        state_group = QGroupBox("状态 / 耗时")
        state_group.setObjectName("stateCard")
        state_layout = QVBoxLayout(state_group)
        state_layout.setContentsMargins(10, 12, 10, 10)
        self.state_label = QLabel("—")
        self.state_label.setWordWrap(True)
        font = self.state_label.font()
        font.setBold(True)
        self.state_label.setFont(font)
        self.elapsed_label = QLabel("耗时：—")
        state_layout.addWidget(self.state_label)
        state_layout.addWidget(self.elapsed_label)

        device_group = QGroupBox("适配器 / 设备")
        device_group.setObjectName("deviceCard")
        device_layout = QVBoxLayout(device_group)
        device_layout.setContentsMargins(10, 12, 10, 10)
        self.adapter_label = QLabel("adapter：—\ndevice：—")
        self.adapter_label.setWordWrap(True)
        device_layout.addWidget(self.adapter_label)

        overview.addWidget(count_group, 3)
        overview.addWidget(state_group, 2)
        overview.addWidget(device_group, 2)
        root.addLayout(overview)

        # -- 断言表 ---------------------------------------------------------
        assert_group = QGroupBox("断言结果（仅展示机器判定）")
        assert_layout = QVBoxLayout(assert_group)
        assert_layout.setContentsMargins(8, 10, 8, 8)
        self.assertion_table = QTableWidget()
        self.assertion_table.setObjectName("assertionTable")
        self.assertion_table.setColumnCount(len(_ASSERTION_COLUMNS))
        self.assertion_table.setHorizontalHeaderLabels(list(_ASSERTION_COLUMNS))
        self.assertion_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.assertion_table.verticalHeader().setVisible(False)
        assert_layout.addWidget(self.assertion_table)
        root.addWidget(assert_group, 3)

        # -- AI 解释 --------------------------------------------------------
        ai_group = QGroupBox("AI 解释（建议/推测，不覆盖机器判定）")
        ai_group.setObjectName("aiExplainGroup")
        ai_layout = QVBoxLayout(ai_group)
        ai_layout.setContentsMargins(8, 10, 8, 8)
        self.ai_text = QPlainTextEdit()
        self.ai_text.setObjectName("aiExplainText")
        self.ai_text.setReadOnly(True)
        self.ai_text.setPlaceholderText("本次运行未产生 AI 解释。")
        ai_layout.addWidget(self.ai_text)
        root.addWidget(ai_group, 2)

        # -- 证据 + 导出 ----------------------------------------------------
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        evidence_group = QGroupBox("证据")
        evidence_group.setObjectName("evidenceGroup")
        evidence_layout = QVBoxLayout(evidence_group)
        evidence_layout.setContentsMargins(10, 12, 10, 10)
        self.evidence_frames_label = QLabel("帧数：—")
        self.evidence_frames_label.setWordWrap(True)
        self.evidence_frames_label.setObjectName("evidenceFrames")
        self.evidence_window_label = QLabel("窗口：—")
        self.evidence_window_label.setWordWrap(True)
        self.evidence_window_label.setObjectName("evidenceWindow")
        evidence_layout.addWidget(self.evidence_frames_label)
        evidence_layout.addWidget(self.evidence_window_label)

        export_box = QGroupBox("报告")
        export_box.setObjectName("exportGroup")
        export_layout = QVBoxLayout(export_box)
        export_layout.setContentsMargins(10, 12, 10, 10)
        self.export_button = QPushButton("导出 HTML 报告")
        self.export_button.setObjectName("exportButton")
        self.export_button.setEnabled(False)
        self.export_status = QLabel("")
        self.export_status.setObjectName("exportStatus")
        self.export_status.setWordWrap(True)
        export_layout.addWidget(self.export_button)
        export_layout.addWidget(self.export_status)

        bottom.addWidget(evidence_group, 3)
        bottom.addWidget(export_box, 2)
        root.addLayout(bottom, 1)

        self.export_button.clicked.connect(self._on_export)

    # ------------------------------------------------------------------ 渲染

    def set_record(self, rec: Any) -> None:
        """渲染一次终态/进行中 RunRecord（由监控页 run_finished 驱动）。"""
        self._rec = rec

        # 计数
        summary = dict(rec.summary)
        self.pass_label.setText(f"Pass {summary.get('Pass', 0)}")
        self.fail_label.setText(f"Fail {summary.get('Fail', 0)}")
        self.blocked_label.setText(f"Blocked {summary.get('Blocked', 0)}")
        self.ne_label.setText(f"NotEvaluated {summary.get('NotEvaluated', 0)}")

        # 状态 / 耗时
        self.state_label.setText(str(rec.state))
        self.elapsed_label.setText(f"耗时：{self._format_elapsed(rec)}")

        # 适配器 / 设备
        self.adapter_label.setText(
            f"adapter：{rec.adapter_id or '—'}\ndevice：{rec.device_id or '—'}"
        )

        # 断言表
        results = list(rec.results or [])
        self.assertion_table.setRowCount(len(results))
        for row, result in enumerate(results):
            verdict = str(result.verdict.value)
            values = (
                str(result.step_id),
                verdict,
                str(result.reason or ""),
                json.dumps(dict(result.threshold), ensure_ascii=False, default=str),
                json.dumps(dict(result.computed), ensure_ascii=False, default=str),
                "、".join(result.evidence_refs) or "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setForeground(
                        QBrush(QColor(_VERDICT_COLORS.get(verdict, "#64748b")))
                    )
                self.assertion_table.setItem(row, column, item)
        self.assertion_table.resizeColumnsToContents()

        # AI 解释
        explanation = getattr(rec, "ai_explanation", None)
        self.ai_text.setPlainText(
            explanation or "本次运行未产生 AI 解释（AI-01 未接入或运行未进入解释阶段）。"
        )

        # 证据
        evidence = getattr(rec, "evidence", None)
        frames = list(getattr(evidence, "frames", []) or [])
        self.evidence_frames_label.setText(f"帧数：{len(frames)}")
        windows = dict(getattr(evidence, "windows", {}) or {})
        combined = None
        combined_call = getattr(evidence, "combined_window", None)
        if callable(combined_call):
            try:
                combined = combined_call()
            except Exception:  # noqa: BLE001 — 证据缺失时保持“—”
                combined = None
        lines: list[str] = []
        for step_id, window in windows.items():
            try:
                start_ms, end_ms = window[0] / 1e6, window[1] / 1e6
                lines.append(f"{step_id}: {start_ms:.1f} ~ {end_ms:.1f} ms")
            except Exception:  # noqa: BLE001 — 窗口结构异常时跳过
                continue
        if combined is not None:
            lines.append(
                f"合计窗口：{combined[0] / 1e6:.1f} ~ {combined[1] / 1e6:.1f} ms"
            )
        self.evidence_window_label.setText(
            "窗口：" + ("；".join(lines) if lines else "—")
        )

        # 导出可用
        self.export_button.setEnabled(True)
        self.export_status.setText("")
        self._set_state_color(rec.state)

    @staticmethod
    def _format_elapsed(rec: Any) -> str:
        if rec.started_at and rec.ended_at:
            seconds = max(0.0, (rec.ended_at - rec.started_at).total_seconds())
            return f"{seconds:.1f}s"
        return "—"

    def _set_state_color(self, state: str) -> None:
        mapping = {
            "Completed": COLOR_PASS,
            "Failed": COLOR_FAIL,
            "Blocked": COLOR_PENDING,
            "Cancelled": COLOR_PENDING,
        }
        color = mapping.get(state, COLOR_GRAPHITE)
        self.state_label.setStyleSheet(f"color: {color}; font-weight: bold; background: transparent;")

    # ------------------------------------------------------------------ 导出

    def _on_export(self) -> None:
        """按 REPORT-01 契约导出 HTML；模块缺失/失败时降级提示。"""
        if self._rec is None:
            self._set_export_status("尚无可导出的运行结果", COLOR_PENDING)
            return
        loaded = _load_reporting()
        if loaded is None:
            self._set_export_status("报告模块未就绪", COLOR_PENDING)
            return
        build_run_result, exporter_cls = loaded

        if self.export_target is not None:
            path = str(self.export_target)
        else:
            run_id = str(getattr(self._rec, "run_id", "run"))
            chosen, _filter = QFileDialog.getSaveFileName(
                self,
                "导出 HTML 报告",
                f"run_{run_id[:8]}.html",
                "HTML 文件 (*.html)",
            )
            if not chosen:
                self._set_export_status("已取消导出", COLOR_GRAPHITE)
                return
            path = chosen
        try:
            exporter_cls().export(
                run_result=build_run_result(self._rec),
                evidence_paths=[],
                output_path=Path(path),
            )
        except Exception as exc:  # noqa: BLE001 — 导出失败提示而非崩溃
            self._set_export_status(f"导出失败：{type(exc).__name__}: {exc}", COLOR_FAIL)
            return
        self._set_export_status(f"已导出：{path}", COLOR_PASS)

    def _set_export_status(self, text: str, color: str) -> None:
        self.export_status.setText(text)
        self.export_status.setStyleSheet(f"color: {color}; background: transparent;")


__all__ = ["ResultsPage", "_load_reporting"]

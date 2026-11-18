"""P2-D UI-01/UI-02 页面测试（offscreen，不依赖 pytest-qt）。

覆盖：
1. 四个页面替换注册（nav==10、stack==10，原 smoke 不回归）；
2. 工作台离线桩生成流程（三组结果 → 待确认勾选 → 定稿入编辑器）；
3. 用例编辑器 dirty / 保存 / 放弃 / 取消（monkeypatch QMessageBox）；
4. 运行监控同步执行路径（状态序列到终态、Trace/统计、急停状态切换）；
5. 结果页渲染与报告导出降级（reporting 缺失分支）。
"""

from __future__ import annotations

import copy
import os

# 必须在导入 PySide6 之前设置，避免无显示器环境失败
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from typing import Any  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMessageBox,
    QSpinBox,
)

from zlg_ai_test_engine.domain.test_ir import validate_ir  # noqa: E402
from zlg_ai_test_engine.ui import main_window as main_window_mod  # noqa: E402
from zlg_ai_test_engine.ui import pages as pages_mod  # noqa: E402
from zlg_ai_test_engine.ui import results as results_mod  # noqa: E402
from zlg_ai_test_engine.ui.case_editor import CaseEditorPage  # noqa: E402
from zlg_ai_test_engine.ui.main_window import PAGE_DEFS, MainWindow  # noqa: E402
from zlg_ai_test_engine.ui.monitor import MonitorPage  # noqa: E402
from zlg_ai_test_engine.ui.results import ResultsPage  # noqa: E402
from zlg_ai_test_engine.ui.theme import COLOR_PENDING  # noqa: E402
from zlg_ai_test_engine.ui.workbench import AIWorkbenchPage  # noqa: E402

#: 监控流程用小 IR：listen 100ms + assert_cycle（无场景帧 → 预期 Blocked）
SMALL_IR: dict[str, Any] = {
    "schema_version": "1.0",
    "case_id": "UI_SMOKE_001",
    "title": "UI 冒烟：100ms 监听 + 周期断言",
    "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
    "steps": [
        {"id": "S1", "action": "listen_bus", "duration_ms": 100},
        {
            "id": "S2",
            "action": "assert_cycle",
            "message": "VehicleSpeed",
            "expected_period_ms": 20,
            "tolerance_ms": 2,
            "evidence_source": "REQ_UI_SMOKE",
        },
    ],
    "execution_policy": {
        "mode": "offline_only",
        "risk_level": "L0",
        "requires_approval": False,
        "max_duration_ms": 1000,
    },
    "unconfirmed_items": [],
}


@pytest.fixture(scope="session")
def app() -> QApplication:
    """进程内单例 QApplication（session 级复用）。"""
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def _make_window(app: QApplication) -> MainWindow:
    """构造主窗口并注册全部页面。"""
    window = MainWindow()
    pages_mod.create_pages(window)
    return window


def _page(window: MainWindow, page_id: str) -> Any:
    return window.stack.widget(window._page_indices[page_id])


class FakeMessageBox:
    """QMessageBox 测试替身：按类属性 choice 返回对应按钮文本。"""

    choice: str = "保存"
    Icon = QMessageBox.Icon
    ButtonRole = QMessageBox.ButtonRole

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._buttons: list[Any] = []
        self._text = ""

    def setIcon(self, *_args: Any) -> None:
        pass

    def setWindowTitle(self, *_args: Any) -> None:
        pass

    def setText(self, text: str) -> None:
        self._text = text

    def addButton(self, text: Any, role: Any) -> Any:
        self._buttons.append(text)
        return text

    def setDefaultButton(self, _button: Any) -> None:
        pass

    def exec(self) -> None:
        pass

    def clickedButton(self) -> Any:
        return FakeMessageBox.choice


# --------------------------------------------------------------------------- 1. 注册


class TestPageRegistration:
    """四个页面替换注册后导航与页面栈保持 10。"""

    def test_nav_and_stack_counts(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            assert window.nav_list.count() == 10 == len(PAGE_DEFS)
            assert window.stack.count() == 10
        finally:
            window.close()

    def test_four_pages_are_formal_implementations(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            assert isinstance(_page(window, "ai_design"), AIWorkbenchPage)
            assert isinstance(_page(window, "test_management"), CaseEditorPage)
            assert isinstance(_page(window, "execution"), MonitorPage)
            assert isinstance(_page(window, "results"), ResultsPage)
        finally:
            window.close()

    def test_nav_switch_still_works(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            window.nav_list.setCurrentRow(6)  # 结果中心
            assert window.stack.currentIndex() == 6
        finally:
            window.close()


# --------------------------------------------------------------------------- 2. 工作台


class TestWorkbenchFlow:
    """离线桩生成：三组结果 → 待确认全勾选 → 定稿送入编辑器。"""

    def test_cycle_draft_flow(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            workbench = _page(window, "ai_design")
            editor = _page(window, "test_management")
            before = editor.case_count

            workbench.requirement_edit.setPlainText("检查报文周期")
            workbench.generate_button.click()

            # 三组结果区有内容
            assert workbench.read_labels and all(l.text() for l in workbench.read_labels)
            assert workbench.inferred_labels and all(
                l.text() for l in workbench.inferred_labels
            )
            assert len(workbench.pending_checks) >= 1

            # 未勾选待确认 → 定稿按钮禁用
            assert not workbench.create_button.isEnabled()

            # 全部勾选 → 启用 → 定稿
            for box in workbench.pending_checks:
                box.setChecked(True)
            assert workbench.create_button.isEnabled()
            workbench.create_button.click()

            assert editor.case_count == before + 1
            ir = editor.current_ir()
            assert ir is not None
            assert str(ir["case_id"]).startswith("GEN_CYCLE_")
            assert validate_ir(ir).ok
            assert ir["unconfirmed_items"] == []
            # 生成后跳转编辑器页
            assert window.nav_list.currentRow() == PAGE_DEFS.index(("test_management", "测试管理"))
        finally:
            window.close()

    def test_stub_badge_visible(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            workbench = _page(window, "ai_design")
            assert workbench.stub_badge.text() == "离线桩生成（AI-01 未接入）"
        finally:
            window.close()

    def test_no_match_shows_empty_state_and_hint(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            workbench = _page(window, "ai_design")
            workbench.requirement_edit.setPlainText("今天天气不错")
            workbench.generate_button.click()
            assert not workbench.create_button.isEnabled()
            # 三组结果区隐藏 + 空态展示（未 show() 的窗口用显式隐藏标记判断）
            assert workbench.read_group.isHidden()
            assert not workbench.empty_state.isHidden()
            assert "重述" in workbench.status_label.text() or "重述" in (
                workbench.empty_state.description_label.text()
            )
        finally:
            window.close()


# --------------------------------------------------------------------------- 3. 编辑器


def _int_field_widget(editor: CaseEditorPage) -> QSpinBox:
    """取属性面板中第一个整数字段控件（随样例结构自适应）。"""
    for key, widget in editor.field_widgets.items():
        if isinstance(widget, QSpinBox):
            return widget
    raise AssertionError("属性面板中未找到整数字段控件")


class TestEditorDirty:
    """编辑器 dirty 生命周期与保存/放弃/取消三分支。"""

    def test_edit_marks_dirty_and_save(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            editor = _page(window, "test_management")
            assert not editor.is_dirty()
            spin = _int_field_widget(editor)
            spin.setValue(spin.value() + 123)
            assert editor.is_dirty()
            assert editor.save() is True
            assert not editor.is_dirty()
        finally:
            window.close()

    def test_switch_with_save(self, app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        window = _make_window(app)
        try:
            editor = _page(window, "test_management")
            monkeypatch.setattr("zlg_ai_test_engine.ui.case_editor.QMessageBox", FakeMessageBox)
            FakeMessageBox.choice = "保存"
            original = editor.ir_at(0)["steps"][0]["duration_ms"]
            spin = _int_field_widget(editor)
            spin.setValue(original + 50)
            assert editor.is_dirty()
            editor.case_list.setCurrentRow(1)  # 触发确认 → 保存
            assert editor.case_list.currentRow() == 1
            assert not editor.is_dirty()
            assert editor.ir_at(0)["steps"][0]["duration_ms"] == original + 50
        finally:
            window.close()

    def test_switch_with_discard(self, app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        window = _make_window(app)
        try:
            editor = _page(window, "test_management")
            monkeypatch.setattr("zlg_ai_test_engine.ui.case_editor.QMessageBox", FakeMessageBox)
            FakeMessageBox.choice = "放弃"
            original = editor.ir_at(0)["steps"][0]["duration_ms"]
            spin = _int_field_widget(editor)
            spin.setValue(original + 50)
            assert editor.is_dirty()
            editor.case_list.setCurrentRow(1)  # 触发确认 → 放弃
            assert editor.case_list.currentRow() == 1
            assert not editor.is_dirty()
            assert editor.ir_at(0)["steps"][0]["duration_ms"] == original
        finally:
            window.close()

    def test_switch_with_cancel(self, app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        window = _make_window(app)
        try:
            editor = _page(window, "test_management")
            monkeypatch.setattr("zlg_ai_test_engine.ui.case_editor.QMessageBox", FakeMessageBox)
            FakeMessageBox.choice = "取消"
            spin = _int_field_widget(editor)
            spin.setValue(spin.value() + 7)
            assert editor.is_dirty()
            editor.case_list.setCurrentRow(1)  # 触发确认 → 取消
            assert editor.case_list.currentRow() == 0  # 保持原选择
            assert editor.is_dirty()
        finally:
            window.close()

    def test_window_close_prompts_save_for_editor(
        self, app: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(app)
        editor = _page(window, "test_management")
        spin = _int_field_widget(editor)
        spin.setValue(spin.value() + 9)
        assert editor.is_dirty()
        # 当前页切换到编辑器（泛化后的 closeEvent 探测当前页）
        window.nav_list.setCurrentRow(PAGE_DEFS.index(("test_management", "测试管理")))
        monkeypatch.setattr(main_window_mod, "QMessageBox", FakeMessageBox)
        FakeMessageBox.choice = "保存"
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        assert not editor.is_dirty()
        window.close()


# --------------------------------------------------------------------------- 4. 监控


class TestMonitorFlow:
    """同步执行路径：状态序列到终态、Trace/统计填充、急停状态切换。"""

    def test_sync_run_reaches_terminal(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            monitor = _page(window, "execution")
            states: list[str] = []
            estop_at_state: dict[str, bool] = {}

            def _record(run_id: str, state: str) -> None:
                states.append(state)
                estop_at_state[state] = window.estop_button.isEnabled()

            monitor.controller.state_changed.connect(_record)

            rec = monitor.launch(copy.deepcopy(SMALL_IR), background=False)
            assert rec is not None
            assert rec.state == "Blocked", rec.error

            # 状态序列到终态
            assert states[0] == "Validated"
            assert "Queued" in states
            assert "Running" in states
            assert states[-1] == "Blocked"
            assert monitor.state_history[-1][1] == "Blocked"

            # Trace：无场景帧 → 状态历史回填
            assert monitor.trace_table.rowCount() >= 5
            trace_texts = [
                monitor.trace_table.item(row, 3).text()
                for row in range(monitor.trace_table.rowCount())
            ]
            assert any("Queued" in text for text in trace_texts)
            assert any("Blocked" in text for text in trace_texts)

            # 统计填充
            assert monitor.blocked_label.text() == "Blocked: 1"
            assert monitor.pass_label.text() == "Pass: 0"
            assert monitor.fail_label.text() == "Fail: 0"
            assert monitor.total_label.text() == "断言总数: 1"

            # 急停状态切换：运行中启用、终态复位
            assert estop_at_state.get("Running") is True
            assert window.estop_button.isEnabled() is False
            assert monitor.emergency_button.isEnabled() is False
            assert window.estop_button.toolTip() == "任务已结束"

            # 步骤树叠加断言 verdict
            root = monitor.step_tree.topLevelItem(0)
            verdicts = [root.child(i).text(2) for i in range(root.childCount())]
            assert any("Blocked" in text for text in verdicts)
        finally:
            window.close()

    def test_blocked_reason_shown_for_invalid_submit(self, app: QApplication) -> None:
        """不满足运行条件（Failed 提交）→ 显示原因、不运行。"""
        window = _make_window(app)
        try:
            monitor = _page(window, "execution")
            bad_ir = copy.deepcopy(SMALL_IR)
            bad_ir["case_id"] = "UI_SMOKE_BAD"
            bad_ir["unconfirmed_items"] = ["阈值未确认"]
            rec = monitor.launch(bad_ir, background=False)
            assert rec is None
            assert "不满足运行条件" in monitor.status_label.text()
            assert monitor.state_label.text() == "空闲" or not monitor.emergency_button.isEnabled()
        finally:
            window.close()

    def test_emergency_without_run_is_noop(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            monitor = _page(window, "execution")
            monitor.emergency_stop()
            assert "无运行" in monitor.status_label.text()
        finally:
            window.close()


# --------------------------------------------------------------------------- 5. 结果页


def _run_small(window: MainWindow) -> Any:
    monitor = _page(window, "execution")
    rec = monitor.launch(copy.deepcopy(SMALL_IR), background=False)
    assert rec is not None
    return rec


class TestResultsPage:
    """结果页渲染 summary 与断言行；导出按钮降级。"""

    def test_render_summary_and_assertions(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            results = _page(window, "results")
            rec = _run_small(window)  # run_finished 信号自动驱动渲染
            assert results.state_label.text() == "Blocked"
            assert results.pass_label.text() == "Pass 0"
            assert results.fail_label.text() == "Fail 0"
            assert results.blocked_label.text() == "Blocked 1"
            assert results.ne_label.text() == "NotEvaluated 0"
            assert "耗时" in results.elapsed_label.text()

            assert results.assertion_table.rowCount() == len(rec.results) == 1
            verdict_item = results.assertion_table.item(0, 1)
            assert verdict_item.text() == "Blocked"
            # Blocked 橙（仅展示层着色）
            assert verdict_item.foreground().color().name() == COLOR_PENDING
            assert results.assertion_table.item(0, 0).text() == "S2"
            assert results.assertion_table.item(0, 2).text()  # reason 非空

            # 证据区与 AI 解释标题
            assert results.evidence_frames_label.text().startswith("帧数：")
            assert results.export_button.isEnabled()
        finally:
            window.close()

    def test_export_without_record(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            results = _page(window, "results")
            results.export_button.setEnabled(True)  # 无记录分支可达性
            results.export_button.click()
            assert "尚无可导出的运行结果" in results.export_status.text()
        finally:
            window.close()

    def test_export_degrades_when_reporting_missing(
        self, app: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(app)
        try:
            results = _page(window, "results")
            _run_small(window)
            assert results._rec is not None
            # 模拟 reporting 未就绪
            monkeypatch.setattr(results_mod, "_load_reporting", lambda: None)
            results.export_button.click()
            assert "报告模块未就绪" in results.export_status.text()
        finally:
            window.close()

    def test_export_success_with_contract(
        self, app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        window = _make_window(app)
        try:
            results = _page(window, "results")
            _run_small(window)
            target = tmp_path / "report.html"
            results.export_target = target
            calls: list[dict[str, Any]] = []

            class FakeExporter:
                def export(self, *, run_result: Any, evidence_paths: Any, output_path: Any) -> Any:
                    calls.append(
                        {"run_result": run_result, "evidence_paths": evidence_paths,
                         "output_path": str(output_path)}
                    )
                    return "ok"

            monkeypatch.setattr(
                results_mod,
                "_load_reporting",
                lambda: (lambda rec: {"run_id": rec.run_id}, FakeExporter),
            )
            results.export_button.click()
            assert calls and calls[0]["output_path"] == str(target)
            assert "已导出" in results.export_status.text()
        finally:
            window.close()

    def test_export_failure_is_reported(
        self, app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        window = _make_window(app)
        try:
            results = _page(window, "results")
            _run_small(window)
            results.export_target = tmp_path / "boom.html"

            class BoomExporter:
                def export(self, **kwargs: Any) -> Any:
                    raise RuntimeError("磁盘写入失败")

            monkeypatch.setattr(
                results_mod,
                "_load_reporting",
                lambda: (lambda rec: {"run_id": rec.run_id}, BoomExporter),
            )
            results.export_button.click()
            assert "导出失败" in results.export_status.text()
        finally:
            window.close()

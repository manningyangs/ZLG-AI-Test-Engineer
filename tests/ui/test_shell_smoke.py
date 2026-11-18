"""PLAT-01 UI 外壳冒烟测试（offscreen，不依赖 pytest-qt）。

覆盖：主窗口结构、急停默认态、主题切换、导航联动、
设置页保存（mock 配置服务）、非 dirty 正常关闭。
"""

from __future__ import annotations

import os

# 必须在导入 PySide6 之前设置，避免无显示器环境失败
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from typing import Any  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zlg_ai_test_engine.ui import pages as pages_mod  # noqa: E402
from zlg_ai_test_engine.ui.main_window import PAGE_DEFS, MainWindow  # noqa: E402
from zlg_ai_test_engine.ui.theme import DARK_QSS, LIGHT_QSS, apply_theme  # noqa: E402


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


class TestMainWindowShell:
    """主窗口结构：导航 10 项、页面栈 10 页。"""

    def test_nav_and_stack_counts(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            assert window.nav_list.count() == 10 == len(PAGE_DEFS)
            assert window.stack.count() == 10
        finally:
            window.close()

    def test_nav_titles_match_spec(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            titles = [
                window.nav_list.item(i).text() for i in range(window.nav_list.count())
            ]
            assert titles == [title for _, title in PAGE_DEFS]
        finally:
            window.close()


class TestEmergencyButton:
    """急停按钮：默认禁用，可显式启用。"""

    def test_disabled_by_default(self, app: QApplication) -> None:
        window = MainWindow()
        try:
            assert not window.estop_button.isEnabled()
            assert window.estop_button.toolTip() == "当前无运行任务"
        finally:
            window.close()

    def test_enable_and_disable(self, app: QApplication) -> None:
        window = MainWindow()
        try:
            window.set_emergency_enabled(True)
            assert window.estop_button.isEnabled()
            window.set_emergency_enabled(False, "任务已结束")
            assert not window.estop_button.isEnabled()
            assert window.estop_button.toolTip() == "任务已结束"
        finally:
            window.close()


class TestTheme:
    """主题切换不抛异常，未知主题回退 light。"""

    def test_switch_theme(self, app: QApplication) -> None:
        assert apply_theme(app, "dark") == "dark"
        assert app.styleSheet() == DARK_QSS
        assert apply_theme(app, "light") == "light"
        assert app.styleSheet() == LIGHT_QSS

    def test_unknown_theme_falls_back_to_light(self, app: QApplication) -> None:
        assert apply_theme(app, "solarized") == "light"
        assert app.styleSheet() == LIGHT_QSS


class TestNavigation:
    """导航与页面栈联动。"""

    def test_switch_to_third_item(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            window.nav_list.setCurrentRow(2)
            assert window.stack.currentIndex() == 2
        finally:
            window.close()

    def test_page_ids_registered(self, app: QApplication) -> None:
        window = _make_window(app)
        try:
            for page_id, _title in PAGE_DEFS:
                assert window._page_indices.get(page_id) is not None
        finally:
            window.close()


class TestSettingsPage:
    """设置页：保存调用配置服务（mock）、dirty 标记。"""

    def test_save_invokes_settings_service(self, app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        saved: list[dict[str, Any]] = []

        class FakeSettings:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class FakeService:
            def load(self) -> FakeSettings:
                return FakeSettings()

            def save(self, settings: Any) -> None:
                saved.append(dict(vars(settings)))

        monkeypatch.setattr(pages_mod, "_resolve_config", lambda: (FakeSettings, FakeService()))

        page = pages_mod.SettingsPage()
        try:
            page.provider_edit.setText("openai-compatible")
            page.model_edit.setText("gpt-4o")
            assert page.is_dirty()
            assert page.save() is True
            assert not page.is_dirty()
            assert saved, "SettingsService.save 未被调用"
            assert saved[-1]["provider"] == "openai-compatible"
            assert saved[-1]["model"] == "gpt-4o"
        finally:
            page.deleteLater()

    def test_dirty_flag_lifecycle(self, app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeSettings:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        monkeypatch.setattr(
            pages_mod, "_resolve_config", lambda: (FakeSettings, None)
        )
        page = pages_mod.SettingsPage()
        try:
            assert not page.is_dirty()
            page.model_edit.setText("x")
            assert page.is_dirty()
            page.clear_dirty()
            assert not page.is_dirty()
        finally:
            page.deleteLater()


class TestCloseEvent:
    """非 dirty 时 closeEvent 正常关闭（不弹对话框）。"""

    def test_close_when_not_dirty(self, app: QApplication) -> None:
        window = _make_window(app)
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        window.close()

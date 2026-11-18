"""PLAT-01：浅色/深色主题（QSS）。

调色板（第 11 章界面规范）：
- 主色深蓝 ``#1f3a5f``、石墨 ``#334155``；
- 通过绿 ``#16a34a``、失败红 ``#dc2626``、待确认橙 ``#ea580c``。

仅提供样式，不加载任何 DLL、不触碰设备。
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

# -- 调色板常量（供徽标、状态提示等内联样式复用） ---------------------------

COLOR_PRIMARY = "#1f3a5f"  # 深蓝主色
COLOR_GRAPHITE = "#334155"  # 石墨
COLOR_PASS = "#16a34a"  # 通过绿
COLOR_FAIL = "#dc2626"  # 失败红
COLOR_PENDING = "#ea580c"  # 待确认橙

#: 支持的主题名
THEMES: tuple[str, ...] = ("light", "dark")

# -- 浅色主题 ---------------------------------------------------------------

LIGHT_QSS = """
QMainWindow {
    background: #f4f6f9;
}
QToolBar {
    background: #ffffff;
    border: none;
    border-bottom: 1px solid #d9dee6;
    spacing: 8px;
    padding: 2px;
}
QLabel {
    color: #1f2937;
}
/* 左侧一级导航 */
QListWidget {
    background: #ffffff;
    border: 1px solid #d9dee6;
    border-radius: 4px;
    outline: 0;
}
QListWidget::item {
    padding: 8px 10px;
    margin: 2px 4px;
    border-radius: 3px;
}
QListWidget::item:hover {
    background: #eef2f8;
}
QListWidget::item:selected {
    background: #1f3a5f;
    color: #ffffff;
}
/* 工程资源树 */
QTreeWidget {
    background: #ffffff;
    border: 1px solid #d9dee6;
    border-radius: 4px;
    outline: 0;
}
QTreeWidget::item {
    padding: 3px 4px;
}
QTreeWidget::item:hover {
    background: #eef2f8;
}
QTreeWidget::item:selected {
    background: #1f3a5f;
    color: #ffffff;
}
QHeaderView::section {
    background: #f4f6f9;
    color: #475569;
    border: none;
    border-bottom: 1px solid #d9dee6;
    border-right: 1px solid #e5e9ef;
    padding: 4px 6px;
    font-size: 12px;
}
/* 按钮 */
QPushButton {
    background: #ffffff;
    color: #1f2937;
    border: 1px solid #c6cfdb;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover {
    background: #eef2f8;
    border-color: #1f3a5f;
}
QPushButton:pressed {
    background: #dfe6f0;
}
QPushButton:disabled {
    color: #9aa5b3;
    background: #f1f3f6;
    border-color: #dde2e9;
}
/* 右侧上下文面板 / 底部任务与日志 */
QDockWidget {
    color: #1f2937;
}
QDockWidget::title {
    background: #e9edf3;
    color: #1f3a5f;
    padding: 6px;
    font-weight: bold;
    text-align: left;
    border-bottom: 1px solid #d9dee6;
}
QTabWidget::pane {
    border: 1px solid #d9dee6;
    background: #ffffff;
    top: -1px;
}
QTabBar::tab {
    background: #e9edf3;
    color: #475569;
    padding: 6px 14px;
    border: 1px solid #d9dee6;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #1f3a5f;
    font-weight: bold;
}
QTabBar::tab:hover:!selected {
    background: #f2f5f9;
}
/* 日志/占位文本 */
QPlainTextEdit {
    background: #ffffff;
    color: #1f2937;
    border: 1px solid #d9dee6;
    border-radius: 4px;
    font-family: Consolas, "Microsoft YaHei", monospace;
    selection-background-color: #1f3a5f;
    selection-color: #ffffff;
}
/* 表单控件 */
QLineEdit {
    background: #ffffff;
    border: 1px solid #c6cfdb;
    border-radius: 4px;
    padding: 5px 8px;
    selection-background-color: #1f3a5f;
    selection-color: #ffffff;
}
QLineEdit:focus {
    border: 1px solid #1f3a5f;
}
QLineEdit:disabled {
    background: #f1f3f6;
    color: #9aa5b3;
}
QComboBox {
    background: #ffffff;
    border: 1px solid #c6cfdb;
    border-radius: 4px;
    padding: 5px 8px;
}
QComboBox:focus {
    border: 1px solid #1f3a5f;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #c6cfdb;
    selection-background-color: #1f3a5f;
    selection-color: #ffffff;
}
QSpinBox {
    background: #ffffff;
    border: 1px solid #c6cfdb;
    border-radius: 4px;
    padding: 5px 8px;
}
QSpinBox:focus {
    border: 1px solid #1f3a5f;
}
QCheckBox {
    spacing: 6px;
    color: #1f2937;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #c6cfdb;
    border-radius: 3px;
    background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #1f3a5f;
    border-color: #1f3a5f;
}
QSplitter::handle {
    background: #d9dee6;
}
QToolTip {
    background: #1f2937;
    color: #ffffff;
    border: none;
    padding: 4px;
}
/* 状态属性选择器（日志/徽标复用，QLabel.setProperty("state", ...)） */
QLabel[state="pass"] {
    color: #16a34a;
}
QLabel[state="fail"] {
    color: #dc2626;
}
QLabel[state="pending"] {
    color: #ea580c;
}
/* 推断项（比待确认橙更深的琥珀色，用于三态可视化） */
QLabel[state="inferred"] {
    color: #b45309;
}
/* 中性统计（NotEvaluated 等） */
QLabel[state="neutral"] {
    color: #64748b;
}
"""

# -- 深色主题 ---------------------------------------------------------------

DARK_QSS = """
QMainWindow {
    background: #151a21;
}
QToolBar {
    background: #1e242e;
    border: none;
    border-bottom: 1px solid #334155;
    spacing: 8px;
    padding: 2px;
}
QLabel {
    color: #d7dde5;
}
/* 左侧一级导航 */
QListWidget {
    background: #1e242e;
    border: 1px solid #334155;
    border-radius: 4px;
    outline: 0;
    color: #d7dde5;
}
QListWidget::item {
    padding: 8px 10px;
    margin: 2px 4px;
    border-radius: 3px;
}
QListWidget::item:hover {
    background: #262e3a;
}
QListWidget::item:selected {
    background: #2d4f7c;
    color: #ffffff;
}
/* 工程资源树 */
QTreeWidget {
    background: #1e242e;
    border: 1px solid #334155;
    border-radius: 4px;
    outline: 0;
    color: #d7dde5;
}
QTreeWidget::item {
    padding: 3px 4px;
}
QTreeWidget::item:hover {
    background: #262e3a;
}
QTreeWidget::item:selected {
    background: #2d4f7c;
    color: #ffffff;
}
QHeaderView::section {
    background: #1a202a;
    color: #9aa5b3;
    border: none;
    border-bottom: 1px solid #334155;
    border-right: 1px solid #262e3a;
    padding: 4px 6px;
    font-size: 12px;
}
/* 按钮 */
QPushButton {
    background: #262e3a;
    color: #d7dde5;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover {
    background: #2f3948;
    border-color: #3f6ea8;
}
QPushButton:pressed {
    background: #1f2733;
}
QPushButton:disabled {
    color: #6b7686;
    background: #1a202a;
    border-color: #262e3a;
}
/* 右侧上下文面板 / 底部任务与日志 */
QDockWidget {
    color: #d7dde5;
}
QDockWidget::title {
    background: #1a202a;
    color: #9db8dc;
    padding: 6px;
    font-weight: bold;
    text-align: left;
    border-bottom: 1px solid #334155;
}
QTabWidget::pane {
    border: 1px solid #334155;
    background: #1e242e;
    top: -1px;
}
QTabBar::tab {
    background: #1a202a;
    color: #9aa5b3;
    padding: 6px 14px;
    border: 1px solid #334155;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #1e242e;
    color: #9db8dc;
    font-weight: bold;
}
QTabBar::tab:hover:!selected {
    background: #262e3a;
}
/* 日志/占位文本 */
QPlainTextEdit {
    background: #1a202a;
    color: #d7dde5;
    border: 1px solid #334155;
    border-radius: 4px;
    font-family: Consolas, "Microsoft YaHei", monospace;
    selection-background-color: #2d4f7c;
    selection-color: #ffffff;
}
/* 表单控件 */
QLineEdit {
    background: #1a202a;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 5px 8px;
    color: #d7dde5;
    selection-background-color: #2d4f7c;
    selection-color: #ffffff;
}
QLineEdit:focus {
    border: 1px solid #3f6ea8;
}
QLineEdit:disabled {
    background: #151a21;
    color: #6b7686;
}
QComboBox {
    background: #1a202a;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 5px 8px;
    color: #d7dde5;
}
QComboBox:focus {
    border: 1px solid #3f6ea8;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #1e242e;
    border: 1px solid #334155;
    color: #d7dde5;
    selection-background-color: #2d4f7c;
    selection-color: #ffffff;
}
QSpinBox {
    background: #1a202a;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 5px 8px;
    color: #d7dde5;
}
QSpinBox:focus {
    border: 1px solid #3f6ea8;
}
QCheckBox {
    spacing: 6px;
    color: #d7dde5;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #334155;
    border-radius: 3px;
    background: #1a202a;
}
QCheckBox::indicator:checked {
    background: #2d4f7c;
    border-color: #3f6ea8;
}
QSplitter::handle {
    background: #334155;
}
QToolTip {
    background: #334155;
    color: #ffffff;
    border: none;
    padding: 4px;
}
/* 状态属性选择器（日志/徽标复用，QLabel.setProperty("state", ...)） */
QLabel[state="pass"] {
    color: #16a34a;
}
QLabel[state="fail"] {
    color: #dc2626;
}
QLabel[state="pending"] {
    color: #ea580c;
}
/* 推断项（比待确认橙更深的琥珀色，用于三态可视化） */
QLabel[state="inferred"] {
    color: #f59e0b;
}
/* 中性统计（NotEvaluated 等） */
QLabel[state="neutral"] {
    color: #9aa5b3;
}
"""

#: 主题名 -> QSS
_QSS_BY_THEME: dict[str, str] = {"light": LIGHT_QSS, "dark": DARK_QSS}


def apply_theme(app: QApplication, theme: str) -> str:
    """为 QApplication 应用主题样式表。

    Args:
        app: 应用实例。
        theme: 主题名，``"light"`` 或 ``"dark"``；未知值回退 ``"light"``。

    Returns:
        实际生效的主题名。
    """
    resolved = theme if theme in THEMES else "light"
    app.setStyleSheet(_QSS_BY_THEME[resolved])
    return resolved

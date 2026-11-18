"""PLAT-01：通用状态占位组件（Loading / Empty / Error）。

第 11.3 节要求每个页面具备 Loading、Empty、Error 等状态占位。
本模块只提供简单 QWidget 组合的占位控件，不承载任何业务逻辑。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from zlg_ai_test_engine.ui.theme import COLOR_FAIL


def _make_label(text: str, *, size: int, bold: bool, color: str | None = None) -> QLabel:
    """创建统一样式的状态文本标签。"""
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    font = label.font()
    font.setPointSize(size)
    font.setBold(bold)
    label.setFont(font)
    if color is not None:
        label.setStyleSheet(f"color: {color}; background: transparent;")
    else:
        label.setStyleSheet("background: transparent;")
    return label


class _StateBase(QWidget):
    """状态组件基类：标题 + 描述的垂直居中布局。"""

    def __init__(
        self,
        title: str,
        description: str = "",
        *,
        title_color: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.setSpacing(8)

        self.title_label = _make_label(title, size=14, bold=True, color=title_color)
        self.description_label = _make_label(description, size=10, bold=False, color="#64748b")
        self._layout.addWidget(self.title_label)
        self._layout.addWidget(self.description_label)

    def set_content(self, title: str, description: str = "") -> None:
        """更新标题与描述文案。"""
        self.title_label.setText(title)
        self.description_label.setText(description)


class LoadingStateWidget(_StateBase):
    """Loading 占位：加载中提示。"""

    def __init__(
        self,
        title: str = "加载中…",
        description: str = "正在准备数据，请稍候。",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, description, parent=parent)


class EmptyStateWidget(_StateBase):
    """Empty 占位：空数据提示，可选一个操作按钮。"""

    #: 用户点击操作按钮时发出
    action_requested = Signal()

    def __init__(
        self,
        title: str = "暂无数据",
        description: str = "",
        action_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, description, parent=parent)

        self.action_button: QPushButton | None = None
        if action_text:
            self.action_button = QPushButton(action_text)
            self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_button.clicked.connect(self.action_requested.emit)
            row = QGridLayout()
            row.addWidget(self.action_button, 0, 0, Qt.AlignmentFlag.AlignCenter)
            self._layout.addLayout(row)


class ErrorStateWidget(_StateBase):
    """Error 占位：错误提示（红色标题），可选重试按钮。"""

    #: 用户点击操作按钮时发出
    action_requested = Signal()

    def __init__(
        self,
        title: str = "出错了",
        description: str = "",
        action_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, description, title_color=COLOR_FAIL, parent=parent)

        self.action_button: QPushButton | None = None
        if action_text:
            self.action_button = QPushButton(action_text)
            self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_button.clicked.connect(self.action_requested.emit)
            row = QGridLayout()
            row.addWidget(self.action_button, 0, 0, Qt.AlignmentFlag.AlignCenter)
            self._layout.addLayout(row)

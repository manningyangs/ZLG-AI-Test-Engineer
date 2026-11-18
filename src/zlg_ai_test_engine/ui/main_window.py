"""PLAT-01：主窗口外壳。

布局遵循任务书第 11.1 节：
- 顶栏：当前工程、Adapter/SDK、设备、在线/离线、安全模式、常驻急停；
- 左侧：一级导航（10 项）+ 工程资源树（垂直分割）；
- 中央：QStackedWidget 页面栈，与导航联动；
- 右侧 Dock：上下文面板；
- 底部 Dock：任务与日志（运行日志/报文 Trace/任务队列/问题/安全审批）。

红线：本模块不加载 DLL、不判定测试结果、不直接轮询设备。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from zlg_ai_test_engine.ui.theme import COLOR_FAIL, COLOR_GRAPHITE, COLOR_PASS

#: 一级导航定义：页面 id（英文 slug）与显示名，顺序即导航顺序（任务书 PLAT-01）。
PAGE_DEFS: list[tuple[str, str]] = [
    ("workbench", "工作台"),
    ("engineering", "工程管理"),
    ("ai_design", "AI 测试设计"),
    ("test_management", "测试管理"),
    ("script_center", "脚本中心"),
    ("execution", "执行中心"),
    ("results", "结果中心"),
    ("devices", "设备与适配器"),
    ("knowledge", "知识与模板"),
    ("settings", "系统设置"),
]

#: 底部抽屉标签页（任务书第 11.1 节“底部抽屉”）
BOTTOM_TABS: list[str] = ["运行日志", "报文 Trace", "任务队列", "问题", "安全审批"]


def _badge(text: str, background: str, foreground: str) -> QLabel:
    """创建顶栏状态徽标。"""
    badge = QLabel(text)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(
        f"background: {background}; color: {foreground};"
        "border-radius: 9px; padding: 2px 10px; font-weight: bold;"
    )
    return badge


class MainWindow(QMainWindow):
    """应用主窗口：外壳布局、导航路由与常驻急停。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ZLG AI Test Engineer")
        self.resize(1440, 900)

        #: page_id -> QStackedWidget 索引
        self._page_indices: dict[str, int] = {}
        #: 注册的设置页（用于 closeEvent 的未保存检查）
        self._settings_page: QWidget | None = None

        self._build_top_bar()
        self._build_central()
        self._build_docks()

    # ------------------------------------------------------------------ 顶栏

    def _build_top_bar(self) -> None:
        """构建顶栏：工程/Adapter/设备信息 + 状态徽标 + 急停。"""
        toolbar = QToolBar(self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(16)

        def caption(text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet(f"color: {COLOR_GRAPHITE}; font-weight: bold; background: transparent;")
            return label

        # 当前工程（默认“未打开工程”）
        self.project_label = QLabel("未打开工程")
        self.project_label.setStyleSheet("background: transparent;")
        layout.addWidget(caption("工程"))
        layout.addWidget(self.project_label)

        # Adapter/SDK
        self.adapter_label = QLabel("未检测")
        self.adapter_label.setStyleSheet("background: transparent;")
        layout.addWidget(caption("Adapter/SDK"))
        layout.addWidget(self.adapter_label)

        # 设备
        self.device_label = QLabel("未连接")
        self.device_label.setStyleSheet("background: transparent;")
        layout.addWidget(caption("设备"))
        layout.addWidget(self.device_label)

        # 在线/离线徽标（默认离线）
        self.online_badge = _badge("离线", "#e5e7eb", "#374151")
        layout.addWidget(self.online_badge)

        # 安全模式徽标（默认“L0 只读”）
        self.safety_badge = _badge("L0 只读", "#dbeafe", "#1e40af")
        layout.addWidget(self.safety_badge)

        layout.addStretch(1)

        # 常驻急停：红色 QToolButton，默认禁用（无运行任务）
        self.estop_button = QToolButton()
        self.estop_button.setText("■ 急停")
        self.estop_button.setToolTip("当前无运行任务")
        self.estop_button.setEnabled(False)
        self.estop_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.estop_button.setStyleSheet(
            "QToolButton { background-color: #dc2626; color: #ffffff;"
            " border: 1px solid #b91c1c; border-radius: 4px;"
            " padding: 5px 16px; font-weight: bold; }"
            "QToolButton:hover { background-color: #b91c1c; }"
            "QToolButton:pressed { background-color: #991b1b; }"
            "QToolButton:disabled { background-color: #f3b7b7; color: #f9e4e4; border: 1px solid #eab5b5; }"
        )
        layout.addWidget(self.estop_button)

        toolbar.addWidget(bar)

    def set_project_name(self, name: str) -> None:
        """设置顶栏当前工程名。"""
        self.project_label.setText(name)

    def set_adapter_info(self, info: str) -> None:
        """设置顶栏 Adapter/SDK 信息。"""
        self.adapter_label.setText(info)

    def set_device_info(self, info: str) -> None:
        """设置顶栏设备信息。"""
        self.device_label.setText(info)

    def set_online(self, online: bool) -> None:
        """设置在线/离线徽标。"""
        if online:
            self.online_badge.setText("在线")
            self.online_badge.setStyleSheet(
                f"background: {COLOR_PASS}; color: #ffffff;"
                "border-radius: 9px; padding: 2px 10px; font-weight: bold;"
            )
        else:
            self.online_badge.setText("离线")
            self.online_badge.setStyleSheet(
                "background: #e5e7eb; color: #374151;"
                "border-radius: 9px; padding: 2px 10px; font-weight: bold;"
            )

    def set_safety_mode(self, mode: str) -> None:
        """设置安全模式徽标（如 “L0 只读”）。"""
        self.safety_badge.setText(mode)
        if mode.strip().upper().startswith("L0"):
            background, foreground = "#dbeafe", "#1e40af"
        else:
            background, foreground = "#ffedd5", "#c2410c"
        self.safety_badge.setStyleSheet(
            f"background: {background}; color: {foreground};"
            "border-radius: 9px; padding: 2px 10px; font-weight: bold;"
        )

    def set_emergency_enabled(self, enabled: bool, reason: str = "") -> None:
        """启用/禁用急停按钮；禁用时可用 reason 解释原因（第 11.3 节）。"""
        self.estop_button.setEnabled(enabled)
        if enabled:
            self.estop_button.setToolTip(reason or "立即停止当前运行任务")
        else:
            self.estop_button.setToolTip(reason or "当前无运行任务")

    # -------------------------------------------------------------- 中央区域

    def _build_central(self) -> None:
        """构建中央区域：左侧（导航 | 资源树）+ 页面栈。"""
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 左侧垂直分割：上为一级导航，下为工程资源树
        self._left_splitter = QSplitter(Qt.Orientation.Vertical)
        self._left_splitter.setMinimumWidth(200)
        self._left_splitter.setMaximumWidth(320)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        for page_id, title in PAGE_DEFS:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, page_id)
            self.nav_list.addItem(item)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self._left_splitter.addWidget(self.nav_list)

        self.resource_tree = QTreeWidget()
        self.resource_tree.setObjectName("resourceTree")
        self.resource_tree.setHeaderLabels(["工程资源树"])
        self._tree_root = QTreeWidgetItem(["资源"])
        self.resource_tree.addTopLevelItem(self._tree_root)
        self._tree_root.setExpanded(True)
        self._left_splitter.addWidget(self.resource_tree)
        self._left_splitter.setSizes([420, 260])

        # 中央页面栈
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")

        layout.addWidget(self._left_splitter)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # 默认选中第一项（工作台）
        self.nav_list.setCurrentRow(0)

    def _on_nav_changed(self, row: int) -> None:
        """导航切换 → 页面栈联动。"""
        item = self.nav_list.item(row)
        if item is None:
            return
        page_id = item.data(Qt.ItemDataRole.UserRole)
        index = self._page_indices.get(str(page_id))
        if index is not None:
            self.stack.setCurrentIndex(index)

    def register_page(self, page_id: str, widget: QWidget) -> None:
        """注册页面到中央栈；页面 id 与一级导航 slug 对应。"""
        index = self.stack.addWidget(widget)
        self._page_indices[page_id] = index
        if page_id == "settings":
            self._settings_page = widget
        # 注册后与当前导航对齐（例如首项在注册前已选中）
        current = self.nav_list.currentRow()
        if 0 <= current < len(PAGE_DEFS) and PAGE_DEFS[current][0] == page_id:
            self.stack.setCurrentIndex(index)

    def add_tree_item(
        self, text: str, parent: QTreeWidgetItem | None = None
    ) -> QTreeWidgetItem:
        """向工程资源树添加节点（默认挂在根节点“资源”下）。"""
        node = QTreeWidgetItem([text])
        target = parent if parent is not None else self._tree_root
        target.addChild(node)
        target.setExpanded(True)
        return node

    # -------------------------------------------------------------- 右/下 Dock

    def _build_docks(self) -> None:
        """构建右侧上下文面板与底部任务与日志抽屉。"""
        # 右侧：上下文面板
        self.context_dock = QDockWidget("上下文面板", self)
        self.context_dock.setObjectName("contextDock")
        context_body = QLabel("此处显示当前对象的上下文、属性、来源与约束。")
        context_body.setWordWrap(True)
        context_body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        context_body.setContentsMargins(8, 8, 8, 8)
        self.context_dock.setWidget(context_body)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.context_dock)

        # 底部：任务与日志（五个标签页，均为只读占位）
        self.bottom_dock = QDockWidget("任务与日志", self)
        self.bottom_dock.setObjectName("bottomDock")
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setObjectName("bottomTabs")
        self._bottom_edits: list[QPlainTextEdit] = []
        for name in BOTTOM_TABS:
            edit = QPlainTextEdit()
            edit.setReadOnly(True)
            edit.setPlaceholderText(f"{name}占位：P1 阶段暂无数据。")
            self._bottom_edits.append(edit)
            self.bottom_tabs.addTab(edit, name)
        self.bottom_dock.setWidget(self.bottom_tabs)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.bottom_dock)

    def set_context_text(self, text: str) -> None:
        """设置右侧上下文面板文案。"""
        widget = self.context_dock.widget()
        if isinstance(widget, QLabel):
            widget.setText(text)

    def append_log(self, text: str) -> None:
        """向底部“运行日志”标签页追加一行日志。"""
        if self._bottom_edits:
            self._bottom_edits[0].appendPlainText(text)

    # ------------------------------------------------------------------ 关闭

    def _dirty_page(self) -> QWidget | None:
        """探测存在未保存更改的页面：当前页优先，其次系统设置页（去重）。

        泛化自原“设置页 dirty 检查”：凡暴露 ``is_dirty()`` 的页面（设置页、
        用例编辑器等）在关闭时都会被探测到，既有设置页行为保持不变。
        """
        candidates: list[QWidget] = []
        current = self.stack.currentWidget()
        if current is not None:
            candidates.append(current)
        if self._settings_page is not None and self._settings_page is not current:
            candidates.append(self._settings_page)
        for page in candidates:
            checker = getattr(page, "is_dirty", None)
            if callable(checker) and checker():
                return page
        return None

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭时若当前页/设置页有未保存更改，提示保存/放弃/取消（第 11.3 节）。"""
        page = self._dirty_page()
        if page is not None:
            name = getattr(page, "page_title", None) or (
                "系统设置" if page is self._settings_page else "当前页面"
            )
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("未保存的更改")
            box.setText(f"“{name}”存在未保存的更改。关闭前是否保存？")
            save_btn = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
            discard_btn = box.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(save_btn)  # type: ignore[arg-type]
            box.exec()
            clicked = box.clickedButton()
            if clicked == cancel_btn:
                event.ignore()
                return
            if clicked == save_btn:
                save = getattr(page, "save", None)
                if callable(save) and not save():
                    # 保存失败：中止关闭，避免丢失更改
                    event.ignore()
                    return
            elif clicked == discard_btn:
                clear_dirty = getattr(page, "clear_dirty", None)
                if callable(clear_dirty):
                    clear_dirty()
        super().closeEvent(event)


__all__ = ["MainWindow", "PAGE_DEFS", "BOTTOM_TABS"]

"""UI-01：AI 测试设计工作台（替换 ai_design 占位页）。

布局（任务书 P2-D UI-01）：
- 左：工程上下文树（样例 DBC 节点 + load_example_irs 样例用例）；
- 中：需求输入 + “生成草案” + 三组结果区（已读取 / 推断建议 / 待确认）；
- 右：约束面板（总线 / 风险等级 / 来源标签，只读）；
- 底：生成状态（解析文件 → 检索 → 生成 → 静态校验 的步骤感文案）。

生成逻辑走离线桩 ``generation_stub``（页面常驻“离线桩生成（AI-01 未接入）”
徽标）；全部待确认项勾选后“生成 Test IR 草案”按钮才启用，定稿经
``validate_ir`` 通过后经 ``draft_created`` 信号送入用例编辑器。

三态可视化：读取=普通色、推断=橙色（state=inferred）、待确认=待确认橙。

红线：UI 不调用模型、不判定测试结果、不触碰设备。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from zlg_ai_test_engine.domain.test_ir import validate_ir
from zlg_ai_test_engine.ui.generation_stub import (
    GenerationDraft,
    build_confirmed_ir,
    generate_draft,
)
from zlg_ai_test_engine.ui.theme import (
    COLOR_FAIL,
    COLOR_GRAPHITE,
    COLOR_PASS,
    COLOR_PENDING,
)
from zlg_ai_test_engine.ui.widgets import EmptyStateWidget

#: 生成步骤文案（步骤感状态栏前缀）
_STAGE_PREFIX = "解析文件 ✔ → 检索 ✔ → 生成"


def _make_item_group(title: str, *, object_name: str) -> tuple[QGroupBox, QVBoxLayout]:
    """创建一组结果区（QGroupBox + 纵向布局）。"""
    box = QGroupBox(title)
    box.setObjectName(object_name)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(8, 10, 8, 8)
    layout.setSpacing(4)
    return box, layout


class AIWorkbenchPage(QWidget):
    """AI 测试设计工作台页（离线桩生成）。"""

    #: 定稿 Test IR（dict）生成后发出 → 用例编辑器接收
    draft_created = Signal(object)

    #: 页面标题（MainWindow 关闭确认文案使用）
    page_title = "AI 测试设计"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._draft: GenerationDraft | None = None
        self._pending_checks: list[QCheckBox] = []
        self._read_labels: list[QLabel] = []
        self._inferred_labels: list[QLabel] = []
        self._empty_added = False

        self._build_left()
        self._build_center()
        self._build_right()
        self._build_body()

    # ------------------------------------------------------------------ 布局

    def _build_left(self) -> None:
        """左侧：工程上下文树。"""
        group = QGroupBox("工程上下文")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 8)

        self.context_tree = QTreeWidget()
        self.context_tree.setObjectName("contextTree")
        self.context_tree.setHeaderLabels(["对象", "说明"])
        layout.addWidget(self.context_tree)
        self._populate_context_tree()
        self.left_panel = group

    def _populate_context_tree(self) -> None:
        """填充样例 DBC 节点与样例用例节点（离线上下文占位）。"""
        db_root = QTreeWidgetItem(["数据库 vehicle.dbc", "样例 DBC 占位"])
        for text, note in (
            ("报文 VehicleSpeed", "20ms 周期"),
            ("报文 BMS_Status_1", "100ms 周期"),
            ("信号 GearState", "枚举占位"),
        ):
            db_root.addChild(QTreeWidgetItem([text, note]))

        cases_root = QTreeWidgetItem(["样例用例 examples/test-ir", ""])
        try:
            from zlg_ai_test_engine.application.pipeline import (  # noqa: PLC0415
                load_example_irs,
            )

            examples = load_example_irs()
        except Exception:  # noqa: BLE001 — 上下文树容错，缺失不影响页面
            examples = []
        for doc in examples:
            cases_root.addChild(
                QTreeWidgetItem([str(doc.get("case_id", "?")), str(doc.get("title", ""))])
            )
        if not examples:
            cases_root.addChild(QTreeWidgetItem(["（无样例）", "仓库缺失时降级"]))

        req_root = QTreeWidgetItem(["需求文档", "未打开工程（占位）"])
        req_root.addChild(QTreeWidgetItem(["REQ_GEN_CYCLE", "样例需求占位"]))

        self.context_tree.addTopLevelItems([db_root, cases_root, req_root])
        for index in range(self.context_tree.topLevelItemCount()):
            self.context_tree.topLevelItem(index).setExpanded(True)

    def _build_center(self) -> None:
        """中列：徽标 + 需求输入 + 三组结果 + 定稿按钮 + 底部生成状态。"""
        center = QVBoxLayout()
        center.setSpacing(6)

        # 离线桩徽标（常驻，明确 AI-01 未接入）
        self.stub_badge = QLabel("离线桩生成（AI-01 未接入）")
        self.stub_badge.setObjectName("stubBadge")
        self.stub_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stub_badge.setStyleSheet(
            f"background: {COLOR_PENDING}; color: #ffffff;"
            "border-radius: 10px; padding: 4px 12px; font-weight: bold;"
        )
        center.addWidget(self.stub_badge)

        input_group = QGroupBox("需求输入")
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(8, 10, 8, 8)
        self.requirement_edit = QTextEdit()
        self.requirement_edit.setObjectName("requirementEdit")
        self.requirement_edit.setPlaceholderText(
            "例：检查报文周期是否满足 100ms±10ms（支持关键词：周期、范围、枚举、超时、发送）"
        )
        self.requirement_edit.setMaximumHeight(90)
        input_layout.addWidget(self.requirement_edit)

        self.generate_button = QPushButton("生成草案")
        self.generate_button.setObjectName("generateButton")
        input_layout.addWidget(self.generate_button, 0, Qt.AlignmentFlag.AlignLeft)
        center.addWidget(input_group)

        # 三组结果区
        self.read_group, self._read_layout = _make_item_group(
            "✅ 已从工程读取", object_name="readGroup"
        )
        self.inferred_group, self._inferred_layout = _make_item_group(
            "💡 推断建议", object_name="inferredGroup"
        )
        self.pending_group, self._pending_layout = _make_item_group(
            "❓ 待确认（全部勾选后方可生成草案）", object_name="pendingGroup"
        )

        self.results_row = QHBoxLayout()
        self.results_row.addWidget(self.read_group, 1)
        self.results_row.addWidget(self.inferred_group, 1)
        self.results_row.addWidget(self.pending_group, 1)
        center.addLayout(self.results_row, 1)

        # 无匹配空态（复用通用状态组件；首次无匹配时挂入布局）
        self.empty_state = EmptyStateWidget("未匹配到用例模式", "请重述需求后重试。")
        self.empty_state.setObjectName("noMatchState")
        self.empty_state.hide()

        # 定稿按钮 + 底部生成状态
        footer = QHBoxLayout()
        self.create_button = QPushButton("生成 Test IR 草案")
        self.create_button.setObjectName("createIrButton")
        self.create_button.setEnabled(False)
        footer.addWidget(self.create_button)
        footer.addStretch(1)
        center.addLayout(footer)

        self.status_label = QLabel("等待输入需求描述。")
        self.status_label.setObjectName("generationStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("state", "neutral")
        center.addWidget(self.status_label)

        self.center_layout = center
        self.center_widget = QWidget()
        self.center_widget.setLayout(center)

    def _build_right(self) -> None:
        """右侧：约束面板（只读展示）。"""
        group = QGroupBox("约束面板")
        group.setObjectName("constraintGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(8)

        def row(caption: str, name: str) -> QLabel:
            head = QLabel(caption)
            head.setStyleSheet(f"color: {COLOR_GRAPHITE}; background: transparent;")
            layout.addWidget(head)
            value = QLabel("—")
            value.setObjectName(name)
            value.setWordWrap(True)
            value.setStyleSheet(f"color: {COLOR_GRAPHITE}; font-weight: bold; background: transparent;")
            layout.addWidget(value)
            return value

        self.bus_label = row("总线", "busLabel")
        self.risk_label = row("风险等级", "riskLabel")
        self.source_label = row("来源标签", "sourceLabel")
        layout.addStretch(1)
        self.right_panel = group

    def _build_body(self) -> None:
        """整体：左 | 中 | 右 水平分割。"""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.left_panel)
        splitter.addWidget(self.center_widget)
        splitter.addWidget(self.right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([240, 720, 240])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

        self.generate_button.clicked.connect(self._on_generate)
        self.create_button.clicked.connect(self._on_create)

    # -------------------------------------------------------------- 生成流程

    def _clear_results(self) -> None:
        """清空三组结果区内容并恢复可见。"""
        for layout, labels in (
            (self._read_layout, self._read_labels),
            (self._inferred_layout, self._inferred_labels),
        ):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            labels.clear()
        while self._pending_layout.count():
            item = self._pending_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._pending_checks.clear()
        for group in (self.read_group, self.inferred_group, self.pending_group):
            group.show()

    def _show_empty_state(self) -> None:
        """无匹配时：隐藏三组结果区，展示通用空态（含重述建议）。"""
        for group in (self.read_group, self.inferred_group, self.pending_group):
            group.hide()
        if not self._empty_added:
            self.center_layout.insertWidget(2, self.empty_state)
            self._empty_added = True
        self.empty_state.show()

    def _on_generate(self) -> None:
        """离线桩生成：三组结果 + 底部步骤状态。"""
        text = self.requirement_edit.toPlainText().strip()
        self._clear_results()
        self._draft = None
        self.create_button.setEnabled(False)
        self._reset_constraints()

        if not text:
            self._set_status("请输入需求描述后点击“生成草案”。", COLOR_PENDING)
            return

        self._set_status(f"{_STAGE_PREFIX} …", COLOR_PENDING)
        draft = generate_draft(text)
        self._draft = draft

        if draft.kind == "none":
            self.empty_state.set_content("未匹配到用例模式", draft.hint)
            self._show_empty_state()
            self._set_status(f"{_STAGE_PREFIX} → 无匹配 ✘：{draft.hint}", COLOR_PENDING)
            return

        # 三组结果可视化：读取=普通 / 推断=橙色 / 待确认=待确认橙
        for text_item in draft.read_items:
            label = QLabel(text_item)
            label.setWordWrap(True)
            self._read_layout.addWidget(label)
            self._read_labels.append(label)
        for text_item in draft.inferred_items:
            label = QLabel(text_item)
            label.setWordWrap(True)
            label.setProperty("state", "inferred")
            self._inferred_layout.addWidget(label)
            self._inferred_labels.append(label)
        for pending in draft.pending_items:
            box = QCheckBox(pending.label)
            box.setStyleSheet(f"color: {COLOR_PENDING};")
            box.toggled.connect(self._update_create_button)
            self._pending_layout.addWidget(box)
            self._pending_checks.append(box)

        # 约束面板（总线 / 风险等级 / 来源标签）
        policy = draft.base_ir.get("execution_policy", {}) or {}
        self.bus_label.setText(str(draft.base_ir.get("protocol", {}).get("bus", "—")))
        self.risk_label.setText(str(policy.get("risk_level", "—")))
        self.source_label.setText("离线桩（AI-01 未接入）")

        # 静态校验（draft 级）
        result = validate_ir(draft.base_ir)
        if result.ok:
            self._set_status(
                f"{_STAGE_PREFIX} → 静态校验 ✔（草案 {draft.base_ir['case_id']}）",
                COLOR_PASS,
            )
        else:
            first = result.errors[0] if result.errors else None
            detail = f"{first.code}: {first.message}" if first else "未知错误"
            self._set_status(f"{_STAGE_PREFIX} → 静态校验 ✘ {detail}", COLOR_FAIL)
        self._update_create_button()

    def _update_create_button(self, *_args: Any) -> None:
        """全部待确认项勾选且存在草案时才允许定稿。"""
        ready = (
            self._draft is not None
            and self._draft.kind != "none"
            and bool(self._draft.pending_items)
            and bool(self._pending_checks)
            and all(box.isChecked() for box in self._pending_checks)
        )
        self.create_button.setEnabled(ready)

    def _on_create(self) -> None:
        """定稿：应用确认值 → validate_ir → 送入用例编辑器。"""
        draft = self._draft
        if draft is None or not self.create_button.isEnabled():
            return
        confirmed = {item.key: item.value for item in draft.pending_items}
        ir = build_confirmed_ir(draft, confirmed)
        result = validate_ir(ir)
        if not result.ok:
            first = result.errors[0] if result.errors else None
            detail = f"{first.code}: {first.message}" if first else "未知错误"
            self._set_status(f"定稿校验 ✘ {detail}", COLOR_FAIL)
            return
        self.draft_created.emit(ir)
        self._set_status(
            f"已生成并送入用例编辑器：{ir['case_id']}（{ir['title']}）", COLOR_PASS
        )

    # ------------------------------------------------------------------ 工具

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background: transparent;")

    def _reset_constraints(self) -> None:
        self.bus_label.setText("—")
        self.risk_label.setText("—")
        self.source_label.setText("—")

    # 测试/外部可读视图
    @property
    def read_labels(self) -> list[QLabel]:
        return list(self._read_labels)

    @property
    def inferred_labels(self) -> list[QLabel]:
        return list(self._inferred_labels)

    @property
    def pending_checks(self) -> list[QCheckBox]:
        return list(self._pending_checks)


__all__ = ["AIWorkbenchPage"]

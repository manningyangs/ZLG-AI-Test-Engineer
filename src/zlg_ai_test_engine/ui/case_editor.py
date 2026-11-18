"""UI-01：用例编辑器（替换 test_management 占位页）。

布局（任务书 P2-D UI-01）：
- 左：用例列表（load_example_irs 样例 + 工作台生成的草案）；
- 中：步骤表（id / action / 关键参数 / 来源 evidence_source）；
- 右：属性面板（选中步骤全部字段 + ACTION_SPECS 风险等级 + 来源标注）。

改动即 dirty；切换用例或主窗口关闭时 dirty → 保存 / 放弃 / 取消
（关闭路径由 MainWindow.closeEvent 的 is_dirty 探测统一处理）。

红线：UI 不判定测试结果、不直接调用设备。
"""

from __future__ import annotations

import copy
import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from zlg_ai_test_engine.domain.test_ir import ACTION_SPECS
from zlg_ai_test_engine.ui.theme import COLOR_PASS, COLOR_PENDING

#: 整数参数字段（用 QSpinBox 编辑）
_INT_FIELDS = frozenset(
    {
        "duration_ms",
        "expected_period_ms",
        "timeout_ms",
        "period_ms",
        "count",
        "sample_ms",
        "start_delay_ms",
        "dlc",
        "can_id",
    }
)

#: 浮点参数字段（用 QDoubleSpinBox 编辑）
_FLOAT_FIELDS = frozenset({"tolerance_ms", "min", "max"})

#: 只读字段（结构标识，不允许改名/改动作）
_READONLY_FIELDS = frozenset({"id", "action"})

#: 步骤表列
_STEP_COLUMNS = ("步骤 id", "动作", "关键参数", "来源 evidence_source")

#: 整数框范围（覆盖 CAN ID 与常规时长）
_INT_MIN, _INT_MAX = 0, 2_000_000_000


def ask_save_changes(parent: QWidget | None = None, *, what: str = "测试管理") -> str:
    """未保存更改确认框。

    Returns:
        ``"save"`` / ``"discard"`` / ``"cancel"``（关闭对话框按取消处理）。
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("未保存的更改")
    box.setText(f"“{what}”存在未保存的更改。切换前是否保存？")
    save_btn = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
    discard_btn = box.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
    cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(save_btn)  # type: ignore[arg-type]
    box.exec()
    clicked = box.clickedButton()
    if clicked == save_btn:
        return "save"
    if clicked == discard_btn:
        return "discard"
    if clicked == cancel_btn:
        return "cancel"
    return "cancel"


def _key_param_text(step: dict[str, Any]) -> str:
    """生成步骤表“关键参数”单元格文案（省略 id/action/来源/描述）。"""
    parts: list[str] = []
    for key, value in step.items():
        if key in _READONLY_FIELDS or key in ("evidence_source", "description"):
            continue
        parts.append(f"{key}={value}")
        if len(parts) >= 4:
            break
    return "  ".join(parts)


class CaseEditorPage(QWidget):
    """用例编辑器页：列表 / 步骤表 / 属性面板 + dirty 管理。"""

    #: 页面标题（切换与关闭确认文案使用）
    page_title = "测试管理"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: 用例集合：{"ir": dict, "source": "样例" | "工作台草案"}
        self._cases: list[dict[str, Any]] = []
        self._current: int = -1
        self._snapshot: dict[str, Any] | None = None
        self._dirty = False
        self._loading = False
        #: 属性面板字段控件（key -> widget），供外部/测试定位
        self.field_widgets: dict[str, QWidget] = {}

        self._build_ui()
        self._load_examples()
        if self._cases:
            self.case_list.setCurrentRow(0)  # 触发首次加载
        else:
            self._set_status("无可用用例（样例仓库缺失）", COLOR_PENDING)

    # ------------------------------------------------------------------ 布局

    def _build_ui(self) -> None:
        # 左：用例列表
        left = QGroupBox("用例列表")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 10, 8, 8)
        self.case_list = QListWidget()
        self.case_list.setObjectName("caseList")
        left_layout.addWidget(self.case_list)

        # 中：步骤表
        center = QGroupBox("步骤")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 10, 8, 8)
        self.step_table = QTableWidget()
        self.step_table.setObjectName("stepTable")
        self.step_table.setColumnCount(len(_STEP_COLUMNS))
        self.step_table.setHorizontalHeaderLabels(list(_STEP_COLUMNS))
        self.step_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.step_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.step_table.verticalHeader().setVisible(False)
        center_layout.addWidget(self.step_table)

        # 右：属性面板（滚动容器 + 动态表单）
        right = QGroupBox("属性面板")
        right.setObjectName("propertyPanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 10, 8, 8)

        self.action_label = QLabel("未选择步骤")
        self.action_label.setWordWrap(True)
        self.action_label.setStyleSheet(f"color: {COLOR_PASS}; font-weight: bold; background: transparent;")
        self.risk_label = QLabel("风险等级：—")
        self.risk_label.setWordWrap(True)
        self.source_label = QLabel("来源标注：—")
        self.source_label.setWordWrap(True)
        right_layout.addWidget(self.action_label)
        right_layout.addWidget(self.risk_label)
        right_layout.addWidget(self.source_label)

        self.prop_form = QFormLayout()
        self.prop_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.prop_form.setHorizontalSpacing(10)
        self.prop_form.setVerticalSpacing(6)
        form_host = QWidget()
        form_host.setLayout(self.prop_form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        right_layout.addWidget(scroll, 1)

        # 底部：保存按钮 + 状态
        footer = QHBoxLayout()
        self.save_button = QPushButton("保存更改")
        self.save_button.setObjectName("saveCaseButton")
        self.save_button.setEnabled(False)
        footer.addWidget(self.save_button)
        footer.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(left)
        body.addWidget(center)
        body.addWidget(right)
        body.setStretchFactor(0, 2)
        body.setStretchFactor(1, 5)
        body.setStretchFactor(2, 3)
        body.setSizes([240, 560, 340])

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(body)
        root.addLayout(footer)

        # 信号
        self.case_list.currentRowChanged.connect(self._on_case_selected)
        self.step_table.currentCellChanged.connect(self._on_step_selected)
        self.save_button.clicked.connect(self.save)

    def _load_examples(self) -> None:
        """加载样例用例（离线数据源，容错空仓库）。"""
        try:
            from zlg_ai_test_engine.application.pipeline import (  # noqa: PLC0415
                load_example_irs,
            )

            examples = load_example_irs()
        except Exception:  # noqa: BLE001 — 数据源缺失降级为空列表
            examples = []
        for doc in examples:
            self._cases.append({"ir": dict(doc), "source": "样例"})
        self._refresh_list()

    # ------------------------------------------------------------------ 列表

    def _refresh_list(self) -> None:
        """重建用例列表项（保持当前选择）。"""
        self.case_list.blockSignals(True)
        self.case_list.clear()
        for index, case in enumerate(self._cases):
            ir = case["ir"]
            item = QListWidgetItem(
                f"{ir.get('case_id', '?')} · {ir.get('title', '')}  [{case['source']}]"
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.case_list.addItem(item)
        if 0 <= self._current < len(self._cases):
            self.case_list.setCurrentRow(self._current)
        self.case_list.blockSignals(False)

    def add_draft(self, ir: Any) -> None:
        """接收工作台生成的草案并选中它。"""
        document = copy.deepcopy(dict(ir))
        self._cases.append({"ir": document, "source": "工作台草案"})
        self._refresh_list()
        self.case_list.setCurrentRow(len(self._cases) - 1)
        self._set_status(
            f"已载入草案：{document.get('case_id', '?')}", COLOR_PASS
        )

    def _on_case_selected(self, row: int) -> None:
        """切换用例：dirty 时先弹保存/放弃/取消。"""
        if self._loading:
            return
        if row < 0 or row >= len(self._cases) or row == self._current:
            return
        if self._dirty:
            choice = ask_save_changes(self, what=self.page_title)
            if choice == "cancel":
                # 恢复原选择，保持 dirty
                self._loading = True
                self.case_list.setCurrentRow(self._current)
                self._loading = False
                self._set_status("已取消切换，更改仍保留", COLOR_PENDING)
                return
            if choice == "save":
                self.save()
            else:  # discard
                self.discard()
        self._current = row
        self._snapshot = copy.deepcopy(self._cases[row]["ir"])
        self._dirty = False
        self.save_button.setEnabled(False)
        self._reload_case()
        self._set_status(f"当前用例：{self._cases[row]['ir'].get('case_id', '?')}", COLOR_PASS)

    def _reload_case(self) -> None:
        """重建步骤表并选中首步。"""
        ir = self._cases[self._current]["ir"]
        steps = list(ir.get("steps", []))
        self._loading = True
        self.step_table.setRowCount(len(steps))
        for index, step in enumerate(steps):
            values = (
                str(step.get("id", "?")),
                str(step.get("action", "")),
                _key_param_text(step),
                str(step.get("evidence_source") or "—"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, index)
                self.step_table.setItem(index, column, item)
        self.step_table.resizeColumnsToContents()
        self._loading = False
        if steps:
            self.step_table.setCurrentCell(0, 0)
            self._show_step(0)
        else:
            self._clear_property_panel()

    # -------------------------------------------------------------- 步骤属性

    def _on_step_selected(self, row: int, *_args: Any) -> None:
        if not self._loading and row >= 0:
            self._show_step(row)

    def _clear_property_panel(self) -> None:
        while self.prop_form.rowCount():
            self.prop_form.removeRow(0)
        self.field_widgets.clear()
        self.action_label.setText("未选择步骤")
        self.risk_label.setText("风险等级：—")
        self.source_label.setText("来源标注：—")

    def _show_step(self, row: int) -> None:
        """属性面板：选中步骤全部字段 + 风险等级 + 来源标注。"""
        if not (0 <= self._current < len(self._cases)):
            return
        steps = self._cases[self._current]["ir"].get("steps", [])
        if not (0 <= row < len(steps)):
            self._clear_property_panel()
            return
        step = steps[row]

        self._clear_property_panel()
        action = str(step.get("action", ""))
        spec = ACTION_SPECS.get(action)
        if spec is not None:
            self.action_label.setText(f"{spec.title}（{action}）")
            mvp = "MVP 可执行" if spec.mvp_enabled else "预留动作（默认禁用）"
            self.risk_label.setText(f"风险等级：{spec.default_risk_level.value} · {mvp}")
        else:
            self.action_label.setText(action or "未知动作")
            self.risk_label.setText("风险等级：—")
        self.source_label.setText(f"来源标注：{step.get('evidence_source') or '—'}")

        self._loading = True
        for key, value in step.items():
            widget = self._make_field_editor(key, value)
            self.prop_form.addRow(key, widget)
            self.field_widgets[key] = widget
        self._loading = False

    def _make_field_editor(self, key: str, value: Any) -> QWidget:
        """按字段类型生成编辑控件；改动实时写回步骤并置 dirty。"""
        if key in _READONLY_FIELDS:
            label = QLabel(str(value))
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            return label
        if key in _INT_FIELDS:
            spin = QSpinBox()
            spin.setRange(_INT_MIN, _INT_MAX)
            try:
                spin.setValue(int(value))
            except (TypeError, ValueError):
                spin.setValue(0)
            spin.valueChanged.connect(lambda v, k=key: self._on_field_edited(k))
            return spin
        if key in _FLOAT_FIELDS:
            spin = QDoubleSpinBox()
            spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
            spin.setDecimals(3)
            try:
                spin.setValue(float(value))
            except (TypeError, ValueError):
                spin.setValue(0.0)
            spin.valueChanged.connect(lambda v, k=key: self._on_field_edited(k))
            return spin
        if isinstance(value, str):
            edit = QLineEdit(value)
            edit.textChanged.connect(lambda _t, k=key: self._on_field_edited(k))
            return edit
        # 列表/字典等复杂值：只读展示（JSON 文本），避免破坏结构
        edit = QLineEdit(json.dumps(value, ensure_ascii=False))
        edit.setReadOnly(True)
        return edit

    def _on_field_edited(self, key: str, *_args: Any) -> None:
        """字段改动 → 写回步骤 + dirty + 刷新步骤表关键参数。"""
        if self._loading or not (0 <= self._current < len(self._cases)):
            return
        steps = self._cases[self._current]["ir"].get("steps", [])
        row = self.step_table.currentRow()
        if not (0 <= row < len(steps)):
            return
        step = steps[row]
        if key not in step and key not in self.field_widgets:
            return
        widget = self.field_widgets.get(key)
        if isinstance(widget, QSpinBox):
            step[key] = int(widget.value())
        elif isinstance(widget, QDoubleSpinBox):
            step[key] = float(widget.value())
        elif isinstance(widget, QLineEdit) and not widget.isReadOnly():
            text = widget.text()
            old = step.get(key)
            if isinstance(old, (list, dict)):
                try:
                    step[key] = json.loads(text)
                except json.JSONDecodeError:
                    return  # 输入未完成：暂不写回
            elif isinstance(old, bool):
                step[key] = text.strip().lower() in ("true", "1", "是", "yes")
            elif isinstance(old, int) and not isinstance(old, bool):
                try:
                    step[key] = int(text)
                except ValueError:
                    return
            elif isinstance(old, float):
                try:
                    step[key] = float(text)
                except ValueError:
                    return
            else:
                step[key] = text
        else:
            return
        self._dirty = True
        self.save_button.setEnabled(True)
        # 同步来源标注与步骤表
        if key == "evidence_source":
            self.source_label.setText(f"来源标注：{step.get('evidence_source') or '—'}")
        table_item = self.step_table.item(row, 2)
        if table_item is not None:
            table_item.setText(_key_param_text(step))
        source_item = self.step_table.item(row, 3)
        if source_item is not None:
            source_item.setText(str(step.get("evidence_source") or "—"))

    # -------------------------------------------------------------- dirty API

    def is_dirty(self) -> bool:
        """是否存在未保存的修改（供 MainWindow.closeEvent 探测）。"""
        return self._dirty

    def clear_dirty(self) -> None:
        self._dirty = False
        self.save_button.setEnabled(False)

    def save(self) -> bool:
        """保存：以当前属性面板内容为准固化快照（改动已实时写入 IR）。"""
        if not (0 <= self._current < len(self._cases)):
            return True
        self._snapshot = copy.deepcopy(self._cases[self._current]["ir"])
        self._dirty = False
        self.save_button.setEnabled(False)
        case_id = self._cases[self._current]["ir"].get("case_id", "?")
        self._set_status(f"已保存：{case_id}", COLOR_PASS)
        return True

    def discard(self) -> None:
        """放弃：把当前用例恢复到最近一次保存的快照并重建面板。"""
        if not (0 <= self._current < len(self._cases)) or self._snapshot is None:
            self._dirty = False
            return
        target = self._cases[self._current]["ir"]
        target.clear()
        target.update(copy.deepcopy(self._snapshot))
        self._dirty = False
        self.save_button.setEnabled(False)
        self._reload_case()
        self._set_status("已放弃未保存的更改", COLOR_PENDING)

    # ------------------------------------------------------------------ 工具

    @property
    def case_count(self) -> int:
        return len(self._cases)

    def ir_at(self, index: int) -> dict[str, Any] | None:
        if 0 <= index < len(self._cases):
            return self._cases[index]["ir"]
        return None

    def current_ir(self) -> dict[str, Any] | None:
        """当前选中的 Test IR（运行入口使用；None 表示无用例）。"""
        if 0 <= self._current < len(self._cases):
            return self._cases[self._current]["ir"]
        return None

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background: transparent;")


__all__ = ["CaseEditorPage", "ask_save_changes"]

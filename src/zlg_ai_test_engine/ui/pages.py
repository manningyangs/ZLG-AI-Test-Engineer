"""PLAT-01 / P2-D：页面工厂与系统设置页。

- ``create_pages``：创建 10 个一级页面并注册到主窗口；
  P2-D 起四个页面为正式实现：ai_design（AI 工作台）、test_management
  （用例编辑器）、execution（运行监控）、results（结果页）；
  其余为占位页（居中阶段提示 + Loading/Empty/Error 通用状态组件）；
- ``SettingsPage``：系统设置表单，读写 Settings（容错接入 app/config 约定接口，
  缺失时降级为 dataclass 默认值，状态标签提示“配置服务不可用”）。

红线：UI 不加载 DLL、不判定测试结果、不直接轮询设备。
"""

from __future__ import annotations

import dataclasses
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from zlg_ai_test_engine.ui.case_editor import CaseEditorPage
from zlg_ai_test_engine.ui.main_window import PAGE_DEFS, MainWindow
from zlg_ai_test_engine.ui.monitor import MonitorPage
from zlg_ai_test_engine.ui.results import ResultsPage
from zlg_ai_test_engine.ui.run_controller import RunController
from zlg_ai_test_engine.ui.theme import COLOR_FAIL, COLOR_PASS, COLOR_PENDING
from zlg_ai_test_engine.ui.widgets import EmptyStateWidget, ErrorStateWidget, LoadingStateWidget
from zlg_ai_test_engine.ui.workbench import AIWorkbenchPage

#: 设置表单对应的 Settings 字段（任务书 PLAT-01 接口约定）
SETTING_FIELDS: tuple[str, ...] = (
    "provider",
    "model",
    "api_base_url",
    "timeout_s",
    "concurrency",
    "default_offline_mode",
    "default_security_level",
    "theme",
)

#: 安全等级选项：显示名 -> 数据值
_SECURITY_LEVELS: list[tuple[str, str]] = [
    ("L0 只读", "L0"),
    ("L1 受控发送", "L1"),
    ("L2 故障注入", "L2"),
    ("L3 诊断写入/标定", "L3"),
    ("L4 高风险", "L4"),
]


@dataclasses.dataclass
class _FallbackSettings:
    """配置服务不可用时的默认设置（容错降级，字段与 Settings 约定一致）。"""

    provider: str = "openai-compatible"
    model: str = ""
    api_base_url: str = ""
    timeout_s: int = 60
    concurrency: int = 4
    default_offline_mode: bool = True
    default_security_level: str = "L0"
    theme: str = "light"


# --------------------------------------------------------------------------- 配置后端（容错）


def _settings_to_dict(obj: Any) -> dict[str, Any]:
    """把 Settings 实例转成字段字典（支持 dict / pydantic / dataclass / 普通对象）。"""
    if isinstance(obj, dict):
        return dict(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump())
        except Exception:  # noqa: BLE001 —— 容错降级，任何序列化失败都退回属性读取
            pass
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return {name: getattr(obj, name) for name in SETTING_FIELDS if hasattr(obj, name)}


def _resolve_config() -> tuple[Any, Any]:
    """解析配置后端。

    约定接口 ``app/config.py``（Settings / SettingsService）优先；
    其次是已实现的 ``application.config_service``（Settings / ConfigService，
    PLAT-03）。任一环节缺失则对应位置为 ``None``，由调用方降级处理。

    Returns:
        ``(Settings 类, 配置服务实例)``，缺失环节为 ``None``。
    """
    settings_cls: Any = None
    service: Any = None

    # 1) 约定接口：zlg_ai_test_engine.app.config（由平台/配置负责人提供）
    try:
        from zlg_ai_test_engine.app import config as app_config  # noqa: PLC0415

        settings_cls = getattr(app_config, "Settings", None)
        service_cls = getattr(app_config, "SettingsService", None) or getattr(
            app_config, "ConfigService", None
        )
        if service_cls is not None:
            try:
                service = service_cls()
            except Exception:  # noqa: BLE001 —— 服务构造失败按不可用降级
                service = None
    except Exception:  # noqa: BLE001 —— 模块不存在/导入失败
        pass

    # 2) 已实现的配置中心：application.config_service（PLAT-03）
    if settings_cls is None or service is None:
        try:
            from zlg_ai_test_engine.application.config_service import (  # noqa: PLC0415
                ConfigService,
                Settings,
            )

            if settings_cls is None:
                settings_cls = Settings
            if service is None:
                try:
                    service = ConfigService()
                except Exception:  # noqa: BLE001
                    service = None
        except Exception:  # noqa: BLE001 —— 依赖未就绪（如 secrets 模块）
            pass

    return settings_cls, service


def resolve_theme() -> str:
    """从配置读取主题；任何失败回退 ``"light"``（供 main() 使用）。"""
    try:
        settings_cls, service = _resolve_config()
        raw: Any = None
        if service is not None and callable(getattr(service, "load", None)):
            try:
                raw = service.load()
            except Exception:  # noqa: BLE001
                raw = None
        if raw is None and settings_cls is not None:
            try:
                raw = settings_cls()
            except Exception:  # noqa: BLE001
                raw = None
        if raw is None:
            return "light"
        theme = _settings_to_dict(raw).get("theme", "light")
        return str(theme) if theme in ("light", "dark") else "light"
    except Exception:  # noqa: BLE001 —— 入口主题读取必须绝不抛出
        return "light"


# --------------------------------------------------------------------------- 占位页


class PlaceholderPage(QWidget):
    """P1 占位页：居中阶段提示 + Loading/Empty/Error 通用状态组件。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page_title = title

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        self.title_label = QLabel(f"「{title}」— P2 阶段实现")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.title_label.font()
        font.setPointSize(16)
        font.setBold(True)
        self.title_label.setFont(font)

        # 通用状态组件（Loading / Empty / Error），默认展示空态
        self.loading_state = LoadingStateWidget(
            f"「{title}」加载中…", "占位页面无数据源，正式实现后此处展示加载进度。"
        )
        self.empty_state = EmptyStateWidget(
            f"「{title}」暂无数据", "本页面将在 P2 阶段实现。"
        )
        self.error_state = ErrorStateWidget(
            f"「{title}」加载失败",
            "占位页面无需加载；正式实现后此处展示错误详情与重试入口。",
            action_text="重试",
        )
        self.state_stack = QStackedWidget()
        self.state_stack.addWidget(self.loading_state)  # index 0
        self.state_stack.addWidget(self.empty_state)  # index 1（默认）
        self.state_stack.addWidget(self.error_state)  # index 2
        self.state_stack.setCurrentWidget(self.empty_state)

        layout.addStretch(1)
        layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.state_stack, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

    def set_state(self, state: str) -> None:
        """切换通用状态占位：``"loading"`` / ``"empty"`` / ``"error"``。"""
        mapping = {
            "loading": self.loading_state,
            "empty": self.empty_state,
            "error": self.error_state,
        }
        widget = mapping.get(state)
        if widget is not None:
            self.state_stack.setCurrentWidget(widget)


# --------------------------------------------------------------------------- 设置页


class SettingsPage(QWidget):
    """系统设置：模型、密钥、超时、并发、离线、安全等级、主题。

    - 打开时从 Settings 加载（容错，缺失时用 dataclass 默认值）；
    - 保存时写 Settings 并（若有）经 SecretStore 保存 API Key；
    - 修改未保存时 ``is_dirty()`` 为 True，供主窗口关闭确认使用。
    """

    #: 页面标题（MainWindow 关闭确认文案使用）
    page_title = "系统设置"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dirty = False
        self._loading = False
        #: SecretStore 不可用时密钥仅内存暂存（绝不写入普通配置文件）
        self._memory_api_key: str = ""
        self._settings_cls: Any = None
        self._service: Any = None

        # -- 表单控件 -------------------------------------------------------
        self.provider_edit = QLineEdit()
        self.model_edit = QLineEdit()
        self.api_base_url_edit = QLineEdit()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("留空表示不修改；仅写入系统安全存储")

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 600)
        self.timeout_spin.setSuffix(" 秒")

        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 16)

        self.offline_check = QCheckBox("默认离线模式")

        self.security_combo = QComboBox()
        for label, value in _SECURITY_LEVELS:
            self.security_combo.addItem(label, value)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("light（浅色）", "light")
        self.theme_combo.addItem("dark（深色）", "dark")

        # -- 布局 -----------------------------------------------------------
        form = QFormLayout(self)
        form.setContentsMargins(16, 16, 16, 8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.addRow("Provider", self.provider_edit)
        form.addRow("模型", self.model_edit)
        form.addRow("API Base URL", self.api_base_url_edit)
        form.addRow("API Key", self.api_key_edit)
        form.addRow("超时", self.timeout_spin)
        form.addRow("并发", self.concurrency_spin)
        form.addRow("默认离线模式", self.offline_check)
        form.addRow("默认安全等级", self.security_combo)
        form.addRow("主题", self.theme_combo)

        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("saveSettingsButton")
        self.reset_button = QPushButton("重置")
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(16, 8, 16, 4)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(buttons)
        root.addWidget(self.status_label)
        root.addStretch(1)

        # -- 信号：任何修改置 dirty ----------------------------------------
        self.provider_edit.textChanged.connect(self._mark_dirty)
        self.model_edit.textChanged.connect(self._mark_dirty)
        self.api_base_url_edit.textChanged.connect(self._mark_dirty)
        self.api_key_edit.textChanged.connect(self._mark_dirty)
        self.timeout_spin.valueChanged.connect(self._mark_dirty)
        self.concurrency_spin.valueChanged.connect(self._mark_dirty)
        self.offline_check.toggled.connect(self._mark_dirty)
        self.security_combo.currentIndexChanged.connect(self._mark_dirty)
        self.theme_combo.currentIndexChanged.connect(self._mark_dirty)

        self.save_button.clicked.connect(self.save)
        self.reset_button.clicked.connect(self.reload)

        # 打开时从 Settings 加载
        self.reload()

    # -- 脏标记 -----------------------------------------------------------

    def is_dirty(self) -> bool:
        """是否存在未保存的修改。"""
        return self._dirty

    def clear_dirty(self) -> None:
        """清除脏标记（如用户选择“放弃”时）。"""
        self._dirty = False

    def _mark_dirty(self, *_args: Any) -> None:
        if not self._loading:
            self._dirty = True

    # -- 加载 / 保存 --------------------------------------------------------

    def reload(self) -> None:
        """从 Settings 加载表单；不可用时降级为默认值并提示。"""
        self._loading = True
        try:
            self._settings_cls, self._service = _resolve_config()
            raw: Any = None
            if self._service is not None and callable(
                getattr(self._service, "load", None)
            ):
                try:
                    raw = self._service.load()
                except Exception:  # noqa: BLE001 —— 配置损坏时退回默认值
                    raw = None
            if raw is None and self._settings_cls is not None:
                try:
                    raw = self._settings_cls()
                except Exception:  # noqa: BLE001
                    raw = None

            if raw is None:
                values = _settings_to_dict(_FallbackSettings())
                self._set_status("配置服务不可用：已使用默认值", COLOR_PENDING)
            else:
                values = _settings_to_dict(raw)
                self._set_status("已加载配置", COLOR_PASS)

            fallback = _settings_to_dict(_FallbackSettings())
            self.provider_edit.setText(str(values.get("provider", fallback["provider"])))
            self.model_edit.setText(str(values.get("model", fallback["model"])))
            self.api_base_url_edit.setText(
                str(values.get("api_base_url", fallback["api_base_url"]))
            )
            self.timeout_spin.setValue(int(values.get("timeout_s", fallback["timeout_s"])))
            self.concurrency_spin.setValue(
                int(values.get("concurrency", fallback["concurrency"]))
            )
            self.offline_check.setChecked(
                bool(values.get("default_offline_mode", fallback["default_offline_mode"]))
            )
            index = self.security_combo.findData(
                str(values.get("default_security_level", fallback["default_security_level"]))
            )
            self.security_combo.setCurrentIndex(index if index >= 0 else 0)
            theme_index = self.theme_combo.findData(
                str(values.get("theme", fallback["theme"]))
            )
            self.theme_combo.setCurrentIndex(theme_index if theme_index >= 0 else 0)
        finally:
            self._loading = False
            self._dirty = False
        # 密钥不回填：界面上永远不展示已保存的 Key 明文
        self.api_key_edit.clear()

    def _collect_values(self) -> dict[str, Any]:
        """收集表单值（不含 API Key——密钥不进普通配置）。"""
        return {
            "provider": self.provider_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "api_base_url": self.api_base_url_edit.text().strip(),
            "timeout_s": int(self.timeout_spin.value()),
            "concurrency": int(self.concurrency_spin.value()),
            "default_offline_mode": bool(self.offline_check.isChecked()),
            "default_security_level": str(self.security_combo.currentData() or "L0"),
            "theme": str(self.theme_combo.currentData() or "light"),
        }

    def save(self) -> bool:
        """保存设置与 API Key。

        Returns:
            是否保存成功（降级为内存保存视为成功，但状态标签会说明未持久化）。
        """
        values = self._collect_values()
        notes: list[str] = []
        ok = True

        # 1) 写 Settings（SettingsService.save 或等效）
        can_persist = (
            self._service is not None
            and callable(getattr(self._service, "save", None))
            and self._settings_cls is not None
        )
        if can_persist:
            try:
                settings = self._build_settings(values)
                self._service.save(settings)
                notes.append("设置已保存")
            except Exception as exc:  # noqa: BLE001 —— 状态提示而非崩溃
                ok = False
                notes.append(f"保存失败：{exc}")
        else:
            notes.append("配置服务不可用：设置未持久化（仅保存在内存）")

        # 2) API Key：只进安全存储，绝不写入普通配置文件
        api_key = self.api_key_edit.text().strip()
        if api_key:
            setter = (
                getattr(self._service, "set_api_key", None)
                if self._service is not None
                else None
            )
            if callable(setter):
                try:
                    setter(api_key)
                    notes.append("API Key 已写入安全存储")
                except Exception:  # noqa: BLE001 —— SecretStore 不可用
                    self._memory_api_key = api_key
                    notes.append("密钥未持久化：SecretStore 不可用")
            else:
                self._memory_api_key = api_key
                notes.append("密钥未持久化：SecretStore 不可用")
            # 界面不保留密钥明文
            self.api_key_edit.clear()

        if ok:
            self._dirty = False
        self._set_status("；".join(notes), COLOR_PASS if ok else COLOR_FAIL)
        return ok

    def _build_settings(self, values: dict[str, Any]) -> Any:
        """用表单值构造 Settings 实例。"""
        try:
            return self._settings_cls(**values)
        except TypeError:
            # 构造参数不匹配时退回“默认实例 + 逐字段赋值”
            settings = self._settings_cls()
            for key, value in values.items():
                setattr(settings, key, value)
            return settings

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background: transparent;")


# --------------------------------------------------------------------------- 工厂


def create_pages(main_window: "MainWindow") -> None:
    """创建 10 个一级页面并按导航顺序注册到主窗口。

    P2-D 装配：AI 工作台定稿 → 用例编辑器；监控页运行 → 结果页渲染；
    RunController 统一转发状态迁移；顶栏常驻急停接到监控页急停处理。
    """
    # -- P2-D 四个正式页面与控制器 -----------------------------------------
    editor = CaseEditorPage()
    workbench = AIWorkbenchPage()
    controller = RunController()
    monitor = MonitorPage(
        controller=controller, main_window=main_window, editor=editor
    )
    results = ResultsPage()

    # 工作台定稿 → 编辑器载入草案 → 跳转到编辑器页
    workbench.draft_created.connect(editor.add_draft)

    def _goto_editor() -> None:
        for index, (page_id, _title) in enumerate(PAGE_DEFS):
            if page_id == "test_management":
                main_window.nav_list.setCurrentRow(index)
                break

    workbench.draft_created.connect(_goto_editor)

    # 监控运行结束 → 结果页渲染
    monitor.run_finished.connect(results.set_record)

    # 顶栏常驻急停 → 监控页急停处理（orchestrator.cancel）
    main_window.estop_button.clicked.connect(monitor.emergency_stop)

    # 由管线装配离线 Mock 编排器（state_listener → 控制器跨线程信号）
    try:
        from zlg_ai_test_engine.application.pipeline import (  # noqa: PLC0415
            build_mock_orchestrator,
        )

        orchestrator = build_mock_orchestrator(state_listener=controller.on_record)
        monitor.set_orchestrator(orchestrator)
    except Exception:  # noqa: BLE001 — 管线不可用时监控页运行按钮降级提示
        monitor.set_orchestrator(None)

    special: dict[str, QWidget] = {
        "ai_design": workbench,
        "test_management": editor,
        "execution": monitor,
        "results": results,
    }

    for page_id, title in PAGE_DEFS:
        if page_id == "settings":
            widget: QWidget = SettingsPage()
        elif page_id in special:
            widget = special[page_id]
        else:
            widget = PlaceholderPage(title)
        main_window.register_page(page_id, widget)


__all__ = ["create_pages", "PlaceholderPage", "SettingsPage", "resolve_theme"]

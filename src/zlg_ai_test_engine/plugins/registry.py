"""插件注册中心（ARCH-01）。

职责：
- 校验插件 Manifest（JSON Schema + api_version 兼容性 + id 规范）；
- 按插件类别注册/查询实例；
- 注册失败（不兼容、清单非法、重复 id）记录到 ``rejected``，
  **不得阻塞应用启动**（任务书 ARCH-01 第 8 条）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from typing import Any, Generic, TypeVar

from jsonschema import Draft202012Validator

from zlg_ai_test_engine.domain.plugins.contracts import PLUGIN_API_VERSION, api_compatible

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PluginKind(str, Enum):
    """插件类别（对应任务书 2.3 六维扩展 + 执行后端）。"""

    PROTOCOL = "protocol"
    DEVICE_ADAPTER = "device_adapter"
    PARSER = "parser"
    SCRIPT_BACKEND = "script_backend"
    EXECUTION_BACKEND = "execution_backend"
    LLM_PROVIDER = "llm_provider"
    REPORT_EXPORTER = "report_exporter"


@dataclass(frozen=True)
class PluginManifest:
    """插件清单（与 manifest.schema.json 对应的强类型视图）。"""

    id: str
    version: str
    api_version: str
    license: str
    capabilities: tuple[str, ...]
    entrypoint: str
    dependencies: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        return cls(
            id=data["id"],
            version=data["version"],
            api_version=data["api_version"],
            license=data["license"],
            capabilities=tuple(data.get("capabilities", ())),
            entrypoint=data["entrypoint"],
            dependencies=tuple(data.get("dependencies", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "api_version": self.api_version,
            "license": self.license,
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "entrypoint": self.entrypoint,
        }


@dataclass(frozen=True)
class RejectedPlugin:
    """注册被拒绝的插件及原因（供启动日志与诊断页展示）。"""

    kind: str
    plugin_id: str
    reason: str


def _load_manifest_schema() -> dict[str, Any]:
    schema_text = (
        resources.files("zlg_ai_test_engine.plugins") / "manifest.schema.json"
    ).read_text(encoding="utf-8")
    return json.loads(schema_text)


class PluginRegistry:
    """插件注册中心：注册校验、查询、拒绝记录。"""

    def __init__(self) -> None:
        self._schema = _load_manifest_schema()
        self._validator = Draft202012Validator(self._schema)
        self._plugins: dict[tuple[str, str], Any] = {}
        self._manifests: dict[tuple[str, str], PluginManifest] = {}
        self._rejected: list[RejectedPlugin] = []

    # -- 校验 ---------------------------------------------------------------

    def validate_manifest(self, manifest: PluginManifest) -> list[str]:
        """校验清单，返回问题列表（空列表 = 通过）。"""
        problems: list[str] = []
        errors = sorted(self._validator.iter_errors(manifest.to_dict()), key=lambda e: e.path)
        for err in errors:
            problems.append(f"schema: {err.message}")
        if manifest.api_version and not api_compatible(manifest.api_version):
            problems.append(
                f"api_version 不兼容：插件 {manifest.api_version} vs 宿主 {PLUGIN_API_VERSION}"
            )
        return problems

    # -- 注册 ---------------------------------------------------------------

    def register(self, kind: PluginKind, manifest: PluginManifest, instance: Any) -> bool:
        """注册插件。校验失败或重复注册时记录 rejected 并返回 False，不抛异常。"""
        problems = self.validate_manifest(manifest)
        if problems:
            self._reject(kind, manifest.id, "; ".join(problems))
            return False
        key = (kind.value, manifest.id)
        if key in self._plugins:
            self._reject(kind, manifest.id, "重复的插件 id")
            return False
        self._plugins[key] = instance
        self._manifests[key] = manifest
        logger.info("插件已注册 kind=%s id=%s version=%s", kind.value, manifest.id, manifest.version)
        return True

    def _reject(self, kind: PluginKind, plugin_id: str, reason: str) -> None:
        logger.warning("插件注册被拒绝 kind=%s id=%s 原因：%s", kind.value, plugin_id, reason)
        self._rejected.append(RejectedPlugin(kind=kind.value, plugin_id=plugin_id, reason=reason))

    # -- 查询 ---------------------------------------------------------------

    def get(self, kind: PluginKind, plugin_id: str) -> Any:
        try:
            return self._plugins[(kind.value, plugin_id)]
        except KeyError:
            raise KeyError(f"插件不存在: kind={kind.value} id={plugin_id}") from None

    def list(self, kind: PluginKind) -> list[Any]:
        return [inst for (k, _pid), inst in self._plugins.items() if k == kind.value]

    def manifest(self, kind: PluginKind, plugin_id: str) -> PluginManifest:
        return self._manifests[(kind.value, plugin_id)]

    def entries(self, kind: PluginKind) -> list[tuple[PluginManifest, Any]]:
        return [
            (manifest, self._plugins[key])
            for key, manifest in self._manifests.items()
            if key[0] == kind.value
        ]

    @property
    def rejected(self) -> tuple[RejectedPlugin, ...]:
        """注册被拒绝的插件（启动不因此失败，仅日志与诊断可见）。"""
        return tuple(self._rejected)

    def __len__(self) -> int:
        return len(self._plugins)

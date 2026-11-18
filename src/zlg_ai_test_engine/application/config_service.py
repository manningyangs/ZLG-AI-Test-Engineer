"""PLAT-03：配置中心。

- 配置文件：``%LOCALAPPDATA%/zlg-ai-test-engine/settings.json``（不含任何密钥）；
- 密钥：只经 SecretStore（Windows Credential Manager），永不进配置文件/日志；
- 审计：见 application/audit_service.py（追加式）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from zlg_ai_test_engine.infrastructure.secrets import SecretStore, get_secret_store

#: API Key 在配置引用中的占位符（配置文件只保存“是否已配置”标记）
API_KEY_PLACEHOLDER = "****"

SecurityLevel = Literal["L0", "L1", "L2", "L3", "L4"]


class Settings(BaseModel):
    """应用设置（pydantic 校验；禁止出现真实密钥字段）。"""

    provider: str = "openai-compatible"
    model: str = ""
    api_base_url: str = ""
    timeout_s: int = Field(default=60, ge=1, le=600)
    concurrency: int = Field(default=4, ge=1, le=16)
    default_offline_mode: bool = True
    default_security_level: SecurityLevel = "L0"
    theme: Literal["light", "dark"] = "light"
    project_root: str = ""
    evidence_dir: str = ""

    @field_validator("theme", mode="before")
    @classmethod
    def _theme_fallback(cls, v: object) -> object:
        return v if v in ("light", "dark") else "light"


class ConfigService:
    """配置读写 + 密钥委托。"""

    def __init__(
        self,
        config_path: Path | None = None,
        secret_store: SecretStore | None = None,
    ) -> None:
        self._secret_store = secret_store or get_secret_store()
        if config_path is None:
            local = os.environ.get("LOCALAPPDATA")
            base = Path(local) if local else Path.home() / "AppData" / "Local"
            self._config_path = base / "zlg-ai-test-engine" / "settings.json"
        else:
            self._config_path = Path(config_path)
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def config_path(self) -> Path:
        return self._config_path

    # -- 配置 ---------------------------------------------------------------

    def load(self) -> Settings:
        if not self._config_path.is_file():
            return Settings()
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return Settings()
        return Settings.model_validate(raw)

    def save(self, settings: Settings) -> None:
        """原子写入（临时文件 + 替换）；内容绝不含密钥。"""
        data = json.loads(settings.model_dump_json())
        tmp = self._config_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._config_path)

    # -- 密钥（仅经 SecretStore） --------------------------------------------

    def set_api_key(self, value: str) -> None:
        """保存/清除 API Key；空字符串表示清除。"""
        self._secret_store.set_secret("llm_api_key", value)

    def get_api_key(self) -> str | None:
        """读取 API Key（仅供 Provider 构造时使用，禁止写入日志）。"""
        return self._secret_store.get_secret("llm_api_key")

    def api_key_configured(self) -> bool:
        return self.get_api_key() is not None

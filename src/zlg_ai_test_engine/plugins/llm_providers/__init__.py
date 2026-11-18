"""AI-01：OpenAI-compatible LLM Provider 插件包。

对外导出：
- ``OpenAICompatibleProvider`` —— OpenAI Chat Completions 兼容实现；
- ``Transport`` / ``UrllibTransport`` —— 可注入传输层（测试零真实网络）；
- ``build_provider(config, *, transport=None)`` —— 从 ConfigService 构造注入密钥的 Provider。

配置红线（任务书 PLAT-03/AI-01）：API Key 只来自 SecretStore（经 ConfigService），
缺失时明确报错并指引到设置页，**绝不**回退为无密钥请求、绝不落日志。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from zlg_ai_test_engine.domain.plugins.contracts import LLMError, LLMErrorKind, LLMProvider
from zlg_ai_test_engine.plugins.llm_providers.openai_compatible import OpenAICompatibleProvider
from zlg_ai_test_engine.plugins.llm_providers.transport import Transport, UrllibTransport

if TYPE_CHECKING:
    from zlg_ai_test_engine.application.config_service import ConfigService

__all__ = [
    "LLMProvider",
    "OpenAICompatibleProvider",
    "Transport",
    "UrllibTransport",
    "build_provider",
]

#: 配置缺失时的统一指引文案（设置页三要素）
_CONFIG_HINT = "请在设置页配置 API Base URL、模型与 API Key"


def build_provider(
    config: "ConfigService", *, transport: Transport | None = None
) -> OpenAICompatibleProvider:
    """从 ConfigService 构造注入密钥的 OpenAI-compatible Provider。

    缺 api_base_url / model，或 SecretStore 无 API Key（None）时抛
    ``LLMError(AUTH, retryable=False)``，信息含设置页指引。
    """
    settings = config.load()
    api_key = config.get_api_key()

    missing: list[str] = []
    if not settings.api_base_url:
        missing.append("API Base URL")
    if not settings.model:
        missing.append("模型")
    if not api_key:
        missing.append("API Key")
    if missing:
        raise LLMError(
            LLMErrorKind.AUTH,
            f"LLM 配置不完整（缺少：{'、'.join(missing)}）。{_CONFIG_HINT}",
            retryable=False,
        )

    return OpenAICompatibleProvider(
        base_url=settings.api_base_url,
        model=settings.model,
        api_key=api_key,
        transport=transport,
        timeout=float(settings.timeout_s),
    )

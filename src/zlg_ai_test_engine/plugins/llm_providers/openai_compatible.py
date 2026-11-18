"""AI-01：OpenAI-compatible LLM Provider（标准库传输 + 可注入 Transport）。

实现 domain/plugins/contracts.LLMProvider 契约——只做文本/结构化输出，不执行工具、
不访问文件系统、不触碰设备；API Key 只经请求头携带，**永不进入日志与异常消息**
（一切外部文本经 ``_safe_text`` 脱敏）。

端点：POST ``{api_base_url}/chat/completions``（OpenAI Chat Completions 形状）。

错误分类与有限重试（完整映射表见 docs/ai/llm_provider.md）：
- 传输异常：TimeoutError / socket.timeout / URLError(reason 含 timeout) → TIMEOUT；
  其他 URLError / ConnectionError → NETWORK；其余 → UNKNOWN；
- HTTP：401/403 → AUTH；429 → RATE_LIMIT；5xx → NETWORK；其他 4xx → UNKNOWN；
- 2xx 但响应结构异常 → UNKNOWN；结构化输出非 JSON / 违反 Schema → INVALID_OUTPUT；
- 仅 retryable（TIMEOUT/NETWORK/RATE_LIMIT）内部重试 ≤2 次（间隔 0.05s×2^n），
  用尽抛原 LLMError；AUTH/UNKNOWN/INVALID_OUTPUT 不做内部重试
  （INVALID_OUTPUT 交由上层 GenerationService 修复重试）。
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from zlg_ai_test_engine.domain.plugins.contracts import (
    LLMError,
    LLMErrorKind,
    LLMProvider,
    LLMResult,
    LLMUsage,
)
from zlg_ai_test_engine.plugins.llm_providers.transport import Transport, UrllibTransport

logger = logging.getLogger(__name__)

#: 可重试错误的最大内部重试次数（首发请求之外）
_MAX_RETRIES = 2
#: 重试间隔基数：第 n 次重试前等待 0.05s × 2^n（n 从 0 起 → 0.05s、0.1s）
_RETRY_BASE_DELAY = 0.05
#: 结构化输出在响应中的固定名称（response_format.json_schema.name）
_STRUCTURED_NAME = "structured_output"


def _as_int(value: Any) -> int:
    """usage 字段安全转 int（缺失/非法一律 0）。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI Chat Completions 兼容 Provider（DeepSeek/通义/本地端点等同形状）。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        transport: Transport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = str(base_url)
        self._model = str(model)
        self._api_key = str(api_key)
        self._transport: Transport = transport if transport is not None else UrllibTransport()
        self._timeout = float(timeout)

    # -- 契约表面 ---------------------------------------------------------------

    @property
    def provider_id(self) -> str:
        return "openai-compatible"

    def list_models(self) -> tuple[str, ...]:
        return (self._model,)

    def complete(
        self, messages: Sequence[Mapping[str, str]], *, model: str, **kwargs: Any
    ) -> LLMResult:
        """普通对话补全：POST /chat/completions，解析 choices[0] 与 usage。"""
        status, body = self._request(self._payload(messages, model, kwargs))
        self._ensure_ok(status, body)
        return self._parse_result(body, model)

    def structured_output(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str,
        json_schema: Mapping[str, Any],
        **kwargs: Any,
    ) -> LLMResult:
        """要求 JSON Schema 结构化输出；服务端拒绝 response_format 时降级重试一次。"""
        schema = dict(json_schema)
        payload = self._payload(messages, model, kwargs)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": _STRUCTURED_NAME, "schema": schema, "strict": True},
        }
        status, body = self._request(payload)
        if status == 400 and self._rejects_response_format(body):
            # 服务端不支持 response_format/json_schema → 降级为普通请求，仅重试一次
            logger.info("服务端拒绝 response_format（HTTP 400），降级为普通请求重试一次")
            status, body = self._request(self._payload(messages, model, kwargs))
        self._ensure_ok(status, body)
        result = self._parse_result(body, model)
        self._validate_structured(result.text, schema)
        return result

    # -- 请求与重试 --------------------------------------------------------------

    def _chat_url(self) -> str:
        base = self._base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _payload(
        self,
        messages: Sequence[Mapping[str, str]],
        model: str,
        extra: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [dict(m) for m in messages],
        }
        payload.update(extra)  # 透传 temperature/max_tokens 等 OpenAI 兼容参数
        return payload

    def _request(self, payload: Mapping[str, Any]) -> tuple[int, dict]:
        """POST /chat/completions。

        - 传输异常：映射为 LLMError；仅 retryable 且未达上限时按 0.05s×2^n 等待后重试，
          用尽/不可重试则抛出原 LLMError；
        - HTTP 状态：仅可重试状态（429/5xx）在上限内重试；其余（含 400 降级判定所需）
          立即返回 ``(status, body)`` 交由调用方分类/降级。
        """
        url = self._chat_url()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        retries = 0
        while True:
            logger.debug("LLM 请求 %s model=%s", url, payload.get("model"))
            try:
                status, body = self._transport.post_json(url, headers, payload, self._timeout)
            except Exception as exc:
                error = self._map_transport_error(exc)
                if not error.retryable or retries >= _MAX_RETRIES:
                    logger.error("LLM 传输失败（已重试 %d 次）：%s", retries, error)
                    raise error from exc
                delay = _RETRY_BASE_DELAY * (2**retries)
                retries += 1
                logger.warning(
                    "LLM 传输可重试错误（%s），%.3fs 后第 %d 次重试：%s",
                    error.kind.value, delay, retries, error,
                )
                time.sleep(delay)
                continue
            if 200 <= status < 300:
                return status, body
            error = self._map_http_error(status, body)
            if error.retryable and retries < _MAX_RETRIES:
                delay = _RETRY_BASE_DELAY * (2**retries)
                retries += 1
                logger.warning(
                    "LLM 返回可重试错误 HTTP %s，%.3fs 后第 %d 次重试", status, delay, retries
                )
                time.sleep(delay)
                continue
            return status, body

    # -- 错误映射 ----------------------------------------------------------------

    def _map_transport_error(self, exc: Exception) -> LLMError:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            kind = LLMErrorKind.TIMEOUT
        elif isinstance(exc, urllib.error.URLError):
            reason = getattr(exc, "reason", None)
            reason_text = str(reason).lower()
            if (
                isinstance(reason, TimeoutError)
                or "timeout" in reason_text
                or "timed out" in reason_text
            ):
                kind = LLMErrorKind.TIMEOUT
            else:
                kind = LLMErrorKind.NETWORK
        elif isinstance(exc, ConnectionError):
            kind = LLMErrorKind.NETWORK
        else:
            kind = LLMErrorKind.UNKNOWN
        retryable = kind in (LLMErrorKind.TIMEOUT, LLMErrorKind.NETWORK)
        detail = self._safe_text(f"{type(exc).__name__}: {exc}")
        return LLMError(kind, f"LLM 传输异常（{kind.value}）：{detail}", retryable=retryable)

    def _map_http_error(self, status: int, body: Any) -> LLMError:
        detail = self._safe_text(self._error_text(body))
        if status in (401, 403):
            return LLMError(
                LLMErrorKind.AUTH, f"认证失败（HTTP {status}）：{detail}", retryable=False
            )
        if status == 429:
            return LLMError(
                LLMErrorKind.RATE_LIMIT,
                f"请求频率受限（HTTP 429）：{detail}",
                retryable=True,
            )
        if 500 <= status <= 599:
            return LLMError(
                LLMErrorKind.NETWORK, f"服务端错误（HTTP {status}）：{detail}", retryable=True
            )
        if 400 <= status <= 499:
            return LLMError(
                LLMErrorKind.UNKNOWN, f"请求被拒绝（HTTP {status}）：{detail}", retryable=False
            )
        return LLMError(
            LLMErrorKind.UNKNOWN, f"意外的 HTTP 状态（{status}）：{detail}", retryable=False
        )

    def _ensure_ok(self, status: int, body: Any) -> None:
        if 200 <= status < 300:
            return
        error = self._map_http_error(status, body)
        logger.error("LLM 调用失败 status=%s kind=%s：%s", status, error.kind.value, error)
        raise error

    # -- 响应解析与校验 -----------------------------------------------------------

    def _parse_result(self, body: Any, requested_model: str) -> LLMResult:
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            detail = self._safe_text(str(body))
            raise LLMError(
                LLMErrorKind.UNKNOWN,
                f"LLM 响应结构异常（缺少 choices[0].message.content）：{detail}",
                retryable=False,
            ) from exc
        if not isinstance(content, str):
            raise LLMError(
                LLMErrorKind.UNKNOWN,
                "LLM 响应结构异常：message.content 不是文本",
                retryable=False,
            )
        usage_raw = body.get("usage") if isinstance(body, dict) else None
        if not isinstance(usage_raw, dict):
            usage_raw = {}
        usage = LLMUsage(
            prompt_tokens=_as_int(usage_raw.get("prompt_tokens")),
            completion_tokens=_as_int(usage_raw.get("completion_tokens")),
        )
        response_model = body.get("model") if isinstance(body, dict) else None
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        return LLMResult(
            text=content,
            model=str(response_model or requested_model),
            usage=usage,
            finish_reason=finish_reason,
        )

    def _validate_structured(self, text: str, schema: Mapping[str, Any]) -> None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(
                LLMErrorKind.INVALID_OUTPUT,
                self._safe_text(f"结构化输出不是合法 JSON：{exc}"),
                retryable=True,
            ) from exc
        first = next(Draft202012Validator(schema).iter_errors(data), None)
        if first is not None:
            raise LLMError(
                LLMErrorKind.INVALID_OUTPUT,
                self._safe_text(f"输出不符合 JSON Schema：{first.message}"),
                retryable=True,
            )

    # -- 辅助 --------------------------------------------------------------------

    @staticmethod
    def _error_text(body: Any) -> str:
        """从 OpenAI 兼容错误体提取可读文本（多形状容错）。"""
        if isinstance(body, Mapping):
            error = body.get("error")
            if isinstance(error, Mapping):
                message = error.get("message")
                if isinstance(message, str):
                    return message
            elif isinstance(error, str):
                return error
            for key in ("message", "detail", "raw"):
                value = body.get(key)
                if isinstance(value, str):
                    return value
        return str(body)

    @staticmethod
    def _rejects_response_format(body: Any) -> bool:
        text = str(OpenAICompatibleProvider._error_text(body)).lower()
        return "response_format" in text or "json_schema" in text

    def _safe_text(self, text: str, limit: int = 400) -> str:
        """脱敏 + 截断：任何进入日志/异常的外部文本不得含 API Key。"""
        if self._api_key:
            text = text.replace(self._api_key, "****")
        return text[:limit]

"""AI-01：OpenAI-compatible LLMProvider 单测。

全部使用假 Transport（脚本化应答），**零真实网络**。覆盖：
- complete 正常路径（URL 规范化 / 认证头 / 消息透传 / usage）；
- structured_output 成功、非 JSON、违反 Schema、response_format 400 降级；
- 错误分类映射与有限重试（401 不重试、429/5xx 调用次数 = 1+2）；
- build_provider 配置缺失报错与指引文案；
- 脱敏：异常消息与日志均不含 API Key。
"""

from __future__ import annotations

import json
import logging
import urllib.error

import pytest

from zlg_ai_test_engine.application.config_service import ConfigService, Settings
from zlg_ai_test_engine.domain.plugins.contracts import LLMError, LLMErrorKind, LLMResult
from zlg_ai_test_engine.infrastructure.secrets import InMemorySecretStore
from zlg_ai_test_engine.plugins.llm_providers import (
    OpenAICompatibleProvider,
    Transport,
    UrllibTransport,
    build_provider,
)

FAKE_KEY = "sk-test-fake-key-1234567890abcdef"
BASE_URL = "https://api.example.com/v1"
CHAT_URL = BASE_URL + "/chat/completions"

MESSAGES = [
    {"role": "system", "content": "规则"},
    {"role": "user", "content": "检查周期"},
]

SCHEMA = {
    "type": "object",
    "properties": {"decision": {"type": "string"}},
    "required": ["decision"],
    "additionalProperties": False,
}


class FakeTransport:
    """脚本化传输：按序返回 (status, body) 或抛出异常；耗尽后重复最后一项。"""

    def __init__(self, script: list) -> None:
        assert script, "脚本不能为空"
        self._script = list(script)
        self.calls: list[dict] = []

    def post_json(self, url, headers, payload, timeout):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout": timeout,
            }
        )
        item = self._script[min(len(self.calls) - 1, len(self._script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def ok_body(text: str = "你好", *, usage: dict | None = None, model: str = "gpt-test"):
    body: dict = {
        "model": model,
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
    }
    if usage is not None:
        body["usage"] = usage
    return 200, body


def make_provider(script: list, **kwargs) -> tuple[OpenAICompatibleProvider, FakeTransport]:
    transport = FakeTransport(script)
    provider = OpenAICompatibleProvider(
        base_url=kwargs.pop("base_url", BASE_URL),
        model=kwargs.pop("model", "gpt-test"),
        api_key=kwargs.pop("api_key", FAKE_KEY),
        transport=transport,
        **kwargs,
    )
    return provider, transport


@pytest.fixture
def config(tmp_path) -> ConfigService:
    return ConfigService(
        config_path=tmp_path / "settings.json", secret_store=InMemorySecretStore()
    )


class TestTransportProtocol:
    def test_urllib_transport_satisfies_protocol(self):
        assert isinstance(UrllibTransport(), Transport)
        assert isinstance(FakeTransport([(200, {})]), Transport)


class TestComplete:
    def test_success_url_headers_messages_usage(self):
        provider, transport = make_provider(
            [ok_body("回答", usage={"prompt_tokens": 11, "completion_tokens": 22})]
        )
        result = provider.complete(MESSAGES, model="gpt-test")

        assert isinstance(result, LLMResult)
        assert result.text == "回答"
        assert result.model == "gpt-test"
        assert result.finish_reason == "stop"
        assert result.usage.prompt_tokens == 11
        assert result.usage.completion_tokens == 22
        assert result.usage.cost is None

        call = transport.calls[0]
        assert call["url"] == CHAT_URL
        assert call["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
        assert call["headers"]["Content-Type"] == "application/json"
        assert call["payload"]["model"] == "gpt-test"
        assert call["payload"]["messages"] == MESSAGES
        assert call["timeout"] == 60.0

    @pytest.mark.parametrize(
        "base_url, expected",
        [
            (BASE_URL, CHAT_URL),
            (BASE_URL + "/", CHAT_URL),
            (CHAT_URL, CHAT_URL),      # 已以 /chat/completions 结尾 → 不重复拼
            (CHAT_URL + "/", CHAT_URL),
        ],
    )
    def test_url_normalization(self, base_url, expected):
        provider, transport = make_provider([ok_body()], base_url=base_url)
        provider.complete(MESSAGES, model="gpt-test")
        assert transport.calls[0]["url"] == expected

    def test_usage_missing_defaults_to_zero(self):
        provider, _ = make_provider([ok_body()])
        result = provider.complete(MESSAGES, model="gpt-test")
        assert result.usage.prompt_tokens == 0
        assert result.usage.completion_tokens == 0

    def test_response_model_echoed_and_kwargs_passed(self):
        provider, transport = make_provider([ok_body(model="gpt-test-2024")])
        result = provider.complete(MESSAGES, model="gpt-test", temperature=0.2)
        assert result.model == "gpt-test-2024"
        assert transport.calls[0]["payload"]["temperature"] == 0.2

    def test_malformed_response_unknown_no_retry(self):
        provider, transport = make_provider([(200, {"foo": 1})])
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert exc.value.kind is LLMErrorKind.UNKNOWN
        assert exc.value.retryable is False
        assert len(transport.calls) == 1


class TestStructuredOutput:
    def test_success_sends_json_schema_response_format(self):
        provider, transport = make_provider(
            [ok_body(json.dumps({"decision": "draft"}))]
        )
        result = provider.structured_output(MESSAGES, model="gpt-test", json_schema=SCHEMA)

        assert json.loads(result.text) == {"decision": "draft"}
        rf = transport.calls[0]["payload"]["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "structured_output"
        assert rf["json_schema"]["schema"] == SCHEMA
        assert rf["json_schema"]["strict"] is True

    @pytest.mark.parametrize(
        "text",
        [
            "这不是 JSON",
            json.dumps({"decision": 42}),          # 类型违反
            json.dumps({"other": "x"}),             # 缺 required
        ],
    )
    def test_invalid_output_raises_invalid_output_single_call(self, text):
        provider, transport = make_provider([ok_body(text)])
        with pytest.raises(LLMError) as exc:
            provider.structured_output(MESSAGES, model="gpt-test", json_schema=SCHEMA)
        assert exc.value.kind is LLMErrorKind.INVALID_OUTPUT
        assert exc.value.retryable is True
        assert len(transport.calls) == 1  # INVALID_OUTPUT 不做内部重试

    def test_response_format_400_degrades_to_plain_request_once(self):
        provider, transport = make_provider(
            [
                (400, {"error": {"message": "response_format is not supported"}}),
                ok_body(json.dumps({"decision": "draft"})),
            ]
        )
        result = provider.structured_output(MESSAGES, model="gpt-test", json_schema=SCHEMA)

        assert json.loads(result.text) == {"decision": "draft"}
        assert len(transport.calls) == 2
        assert "response_format" in transport.calls[0]["payload"]
        assert "response_format" not in transport.calls[1]["payload"]

    def test_degraded_response_still_schema_validated(self):
        provider, transport = make_provider(
            [
                (400, {"error": {"message": "unsupported json_schema parameter"}}),
                ok_body(json.dumps({"decision": 42})),
            ]
        )
        with pytest.raises(LLMError) as exc:
            provider.structured_output(MESSAGES, model="gpt-test", json_schema=SCHEMA)
        assert exc.value.kind is LLMErrorKind.INVALID_OUTPUT
        assert len(transport.calls) == 2

    def test_plain_400_without_hint_does_not_degrade(self):
        provider, transport = make_provider(
            [(400, {"error": {"message": "invalid request body"}})]
        )
        with pytest.raises(LLMError) as exc:
            provider.structured_output(MESSAGES, model="gpt-test", json_schema=SCHEMA)
        assert exc.value.kind is LLMErrorKind.UNKNOWN
        assert exc.value.retryable is False
        assert len(transport.calls) == 1


class TestErrorMappingAndRetry:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_no_retry(self, status):
        provider, transport = make_provider([(status, {"error": {"message": "bad key"}})])
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert exc.value.kind is LLMErrorKind.AUTH
        assert exc.value.retryable is False
        assert len(transport.calls) == 1  # AUTH 不内部重试

    def test_rate_limit_retries_twice_then_raises(self):
        provider, transport = make_provider(
            [(429, {"error": {"message": "rate limit"}})]
        )
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert exc.value.kind is LLMErrorKind.RATE_LIMIT
        assert exc.value.retryable is True
        assert len(transport.calls) == 3  # 1 次首发 + 2 次内部重试

    @pytest.mark.parametrize("status", [500, 503])
    def test_server_error_maps_to_network_with_retry(self, status):
        provider, transport = make_provider([(status, {"error": {"message": "boom"}})])
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert exc.value.kind is LLMErrorKind.NETWORK
        assert exc.value.retryable is True
        assert len(transport.calls) == 3

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_other_4xx_unknown_no_retry(self, status):
        provider, transport = make_provider([(status, {"error": {"message": "bad"}})])
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert exc.value.kind is LLMErrorKind.UNKNOWN
        assert exc.value.retryable is False
        assert len(transport.calls) == 1

    @pytest.mark.parametrize(
        "exc_item",
        [
            TimeoutError("timed out"),
            urllib.error.URLError(TimeoutError("timed out")),
            urllib.error.URLError("read timeout=60"),
        ],
        ids=["TimeoutError", "URLError(reason=timeout)", "URLError(text=timeout)"],
    )
    def test_timeout_kind_with_retry(self, exc_item):
        provider, transport = make_provider([exc_item])
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert exc.value.kind is LLMErrorKind.TIMEOUT
        assert exc.value.retryable is True
        assert len(transport.calls) == 3

    @pytest.mark.parametrize(
        "exc_item",
        [urllib.error.URLError("connection refused"), ConnectionResetError("reset")],
    )
    def test_network_kind_with_retry(self, exc_item):
        provider, transport = make_provider([exc_item])
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert exc.value.kind is LLMErrorKind.NETWORK
        assert exc.value.retryable is True
        assert len(transport.calls) == 3

    def test_unexpected_transport_exception_unknown_no_retry(self):
        provider, transport = make_provider([ValueError("bug in transport")])
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert exc.value.kind is LLMErrorKind.UNKNOWN
        assert exc.value.retryable is False
        assert len(transport.calls) == 1

    def test_retry_recovers_within_budget(self):
        """前两次可重试失败、第三次成功 → 正常返回且恰好 3 次调用。"""
        provider, transport = make_provider(
            [
                (429, {"error": {"message": "rate limit"}}),
                (429, {"error": {"message": "rate limit"}}),
                ok_body("恢复"),
            ]
        )
        result = provider.complete(MESSAGES, model="gpt-test")
        assert result.text == "恢复"
        assert len(transport.calls) == 3

    def test_provider_surface(self):
        provider, _ = make_provider([ok_body()])
        assert provider.provider_id == "openai-compatible"
        assert provider.list_models() == ("gpt-test",)


class TestBuildProvider:
    def test_missing_all_raises_auth_with_guidance(self, config):
        config.save(Settings())
        with pytest.raises(LLMError) as exc:
            build_provider(config)
        assert exc.value.kind is LLMErrorKind.AUTH
        assert exc.value.retryable is False
        assert "请在设置页配置 API Base URL、模型与 API Key" in str(exc.value)

    def test_missing_key_raises_auth_with_guidance(self, config):
        config.save(Settings(model="gpt-test", api_base_url=BASE_URL))
        with pytest.raises(LLMError) as exc:
            build_provider(config)
        assert exc.value.kind is LLMErrorKind.AUTH
        assert exc.value.retryable is False
        assert "API Key" in str(exc.value)
        assert "请在设置页配置 API Base URL、模型与 API Key" in str(exc.value)

    @pytest.mark.parametrize(
        "settings, fragment",
        [
            (Settings(model="gpt-test"), "API Base URL"),
            (Settings(api_base_url=BASE_URL), "模型"),
        ],
        ids=["missing-base-url", "missing-model"],
    )
    def test_missing_endpoint_fields_raises_auth(self, config, settings, fragment):
        config.save(settings)
        config.set_api_key(FAKE_KEY)
        with pytest.raises(LLMError) as exc:
            build_provider(config)
        assert exc.value.kind is LLMErrorKind.AUTH
        assert fragment in str(exc.value)
        assert "请在设置页配置 API Base URL、模型与 API Key" in str(exc.value)

    def test_success_builds_provider_with_injected_key(self, config):
        config.save(Settings(model="gpt-test", api_base_url=BASE_URL, timeout_s=30))
        config.set_api_key(FAKE_KEY)
        transport = FakeTransport([ok_body("ok")])

        provider = build_provider(config, transport=transport)

        assert provider.provider_id == "openai-compatible"
        assert provider.list_models() == ("gpt-test",)
        result = provider.complete(MESSAGES, model="gpt-test")
        assert result.text == "ok"
        call = transport.calls[0]
        assert call["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
        assert call["timeout"] == 30.0  # 来自 settings.timeout_s


class TestRedaction:
    def test_exception_message_never_contains_api_key(self):
        """服务端错误体回显密钥 → 异常消息必须脱敏。"""
        provider, _ = make_provider(
            [(401, {"error": {"message": f"invalid api key {FAKE_KEY}"}})]
        )
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert FAKE_KEY not in str(exc.value)
        assert "****" in str(exc.value)

    def test_logs_never_contain_api_key(self, caplog):
        provider, transport = make_provider(
            [
                (429, {"error": {"message": f"rate limit for {FAKE_KEY}"}}),
                (401, {"error": {"message": f"invalid key {FAKE_KEY}"}}),
            ]
        )
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(LLMError):
                provider.complete(MESSAGES, model="gpt-test")  # 429 重试后 401
        assert FAKE_KEY not in caplog.text
        assert "****" in caplog.text  # 错误详情确实进了日志且已脱敏
        # 成功路径日志（URL/model）同样不含密钥
        provider2, _ = make_provider([ok_body()])
        with caplog.at_level(logging.DEBUG):
            provider2.complete(MESSAGES, model="gpt-test")
        assert FAKE_KEY not in caplog.text

    def test_transport_exception_containing_key_redacted(self):
        provider, _ = make_provider(
            [urllib.error.URLError(f"proxy said: {FAKE_KEY}")]
        )
        with pytest.raises(LLMError) as exc:
            provider.complete(MESSAGES, model="gpt-test")
        assert FAKE_KEY not in str(exc.value)
        assert "****" in str(exc.value)

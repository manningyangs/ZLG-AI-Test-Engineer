# AI-01 OpenAI-compatible LLMProvider

**任务：** AI-01（模型接入）｜**状态：** 已实现（单测全假 Transport，待真实端点实测）｜**日期：** 2026-11-18

## 组件

| 模块 | 职责 |
|---|---|
| `plugins/llm_providers/__init__.py` | 导出 + `build_provider(config, *, transport=None)`：从 ConfigService 读配置、经 SecretStore 注入 API Key；配置缺失抛 `LLMError(AUTH)` |
| `plugins/llm_providers/transport.py` | `Transport` 协议（`post_json(url, headers, payload, timeout) -> (status, body)`）+ `UrllibTransport`（标准库 urllib，零第三方依赖） |
| `plugins/llm_providers/openai_compatible.py` | `OpenAICompatibleProvider(LLMProvider)`：端点规范化、请求/解析、错误分类、有限重试、日志与异常脱敏 |

## 端点与请求

- **URL 规范化**：`base_url.rstrip('/')`，若已以 `/chat/completions` 结尾则不重复拼，否则追加 → `POST {base}/chat/completions`。
- **请求头**：`Authorization: Bearer <key>`（Key 仅来自 SecretStore，永不落配置/日志）、`Content-Type: application/json`。
- **complete**：`{model, messages, ...kwargs 透传}` → 解析 `choices[0].message.content`、`finish_reason`、`usage`（缺省 0）→ `LLMResult`。
- **structured_output**：额外携带
  `response_format={"type":"json_schema","json_schema":{"name":"structured_output","schema":…,"strict":true}}`；
  服务端 **HTTP 400 且错误信息含 `response_format` 或 `json_schema`** → 降级为普通请求**重试一次**；
  返回文本须 `json.loads` 成功且过 `Draft202012Validator(schema)`，否则 `LLMError(INVALID_OUTPUT, retryable=True)`。

## 配置（ConfigService）

| 字段 | 用途 |
|---|---|
| `api_base_url` | 端点基址（缺 → AUTH） |
| `model` | 默认模型（`list_models()` 返回它；缺 → AUTH） |
| `timeout_s` | 请求超时（注入 Provider 的 `timeout`） |
| SecretStore `llm_api_key` | Bearer 密钥（`get_api_key()` 为 None/空 → AUTH） |

配置缺失统一抛 `LLMError(AUTH, retryable=False)`，信息含：**「请在设置页配置 API Base URL、模型与 API Key」**。

## 错误分类映射表

| 触发条件 | kind | retryable | 内部重试 |
|---|---|---|---|
| `TimeoutError` / `socket.timeout` / `URLError(reason 含 timeout)` | `TIMEOUT` | ✅ | ≤2 次 |
| 其他 `URLError` / `ConnectionError` | `NETWORK` | ✅ | ≤2 次 |
| 其他传输层异常（非上述） | `UNKNOWN` | ❌ | 不重试 |
| HTTP 401 / 403 | `AUTH` | ❌ | 不重试 |
| HTTP 429 | `RATE_LIMIT` | ✅ | ≤2 次 |
| HTTP 5xx | `NETWORK` | ✅ | ≤2 次 |
| 其他 4xx（400/404/422…） | `UNKNOWN` | ❌ | 不重试 |
| 2xx 但响应结构异常（缺 `choices[0].message.content` 等） | `UNKNOWN` | ❌ | 不重试 |
| structured_output：非 JSON / 违反 Schema | `INVALID_OUTPUT` | ✅（交上层修复重试） | 不内部重试 |
| `build_provider` 配置缺失 | `AUTH` | ❌ | — |

**有限重试**：仅 retryable 错误内部最多重试 2 次（间隔 `0.05s×2^n`，n=0,1 → 0.05s、0.1s），
用尽抛出原 `LLMError`；最多共 3 次 Transport 调用。

**脱敏**：一切进入日志/异常的外部文本（服务端错误体、传输异常）经 `_safe_text`
先替换 API Key 为 `****` 再截断；请求头本身从不进日志。

## 测试

| 测试 | 覆盖 |
|---|---|
| `tests/unit/test_llm_provider.py` | complete 正常（URL/头/消息/usage）、URL 规范化 4 形态、structured 成功/非 JSON/违反 Schema、response_format 400 降级（含降级后仍验 Schema、普通 400 不降级）、401/403→AUTH 不重试、429→RATE_LIMIT 调用数=1+2、5xx→NETWORK、4xx→UNKNOWN、超时/网络异常分类与重试、重试预算内恢复、build_provider 缺失三类报错与指引文案、异常/日志脱敏断言 —— **全部假 Transport，零真实网络** |

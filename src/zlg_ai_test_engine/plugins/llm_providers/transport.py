"""AI-01：LLM 传输层（可注入协议 + 标准库实现）。

- ``Transport``：Provider 与网络之间的唯一边界；单测注入假实现，零真实网络；
- ``UrllibTransport``：标准库 ``urllib.request`` 实现，**不引入任何第三方 HTTP 依赖**。

HTTP 错误（4xx/5xx）是"有状态码的响应"而非网络异常：本层把 HTTPError 也解析为
``(status, body)`` 返回，由 Provider 统一做错误分类；真正的网络/超时异常（URLError 等）
原样上抛，由 Provider 映射为 LLMError。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class Transport(Protocol):
    """HTTP POST + JSON 编解码协议（注入点，测试用假实现替代）。"""

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> tuple[int, dict]: ...


class UrllibTransport:
    """基于标准库 urllib 的默认实现（POST JSON，返回 (status, body)）。"""

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> tuple[int, dict]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers=dict(headers), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # HTTP 错误码也是响应：状态码交由 Provider 分类，不当作网络异常
            status = exc.code
            raw = exc.read().decode("utf-8", errors="replace")
        return status, _to_json_object(raw)


def _to_json_object(raw: str) -> dict:
    """把响应体规约为 dict：非 JSON / 非对象时保留原始文本供错误信息使用。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"raw": parsed}

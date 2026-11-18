"""PLAT-03 关联测试：日志脱敏（DoD：日志不输出 API Key）。"""

from __future__ import annotations

import logging

from zlg_ai_test_engine.infrastructure.logging.setup import SecretRedactingFilter


def _capture(msg: str) -> str:
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None
    )
    SecretRedactingFilter().filter(record)
    return record.getMessage()


def test_sk_key_redacted():
    out = _capture("calling provider with key sk-abcdef1234567890 now")
    assert "sk-abcdef1234567890" not in out
    assert "****" in out


def test_bearer_token_redacted():
    out = _capture("Authorization: Bearer abcdef1234567890")
    assert "abcdef1234567890" not in out


def test_normal_message_untouched():
    msg = "设备初始化完成 channel=0"
    assert _capture(msg) == msg

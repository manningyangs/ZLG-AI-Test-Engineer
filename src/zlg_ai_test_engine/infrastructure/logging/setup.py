"""日志初始化实现。

脱敏要求（任务书 DoD）：日志不输出 API Key、完整敏感工程内容或 Seed-Key。
本模块提供 ``SecretRedactingFilter`` 对疑似密钥的敏感串做遮蔽。
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: 疑似密钥模式（Bearer、sk- 开头的 token 等）
_SECRET_PATTERNS = [
    re.compile(r"(sk-)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key[\"'=:\s]+)[A-Za-z0-9._\-]{8,}"),
]


class SecretRedactingFilter(logging.Filter):
    """把疑似密钥的片段替换为 ****。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "****", redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def init_logging(log_dir: Path | None = None, *, level: int = logging.INFO) -> None:
    """初始化根日志：控制台 + （可选）滚动文件。重复调用幂等。"""
    root = logging.getLogger()
    if getattr(root, "_zlg_ate_initialized", False):
        return

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    redactor = SecretRedactingFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redactor)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)

    root.setLevel(level)
    root._zlg_ate_initialized = True  # type: ignore[attr-defined]

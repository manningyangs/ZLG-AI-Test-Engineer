"""日志初始化：控制台 + 滚动文件（不输出 API Key 等敏感信息）。"""

from zlg_ai_test_engine.infrastructure.logging.setup import init_logging

__all__ = ["init_logging"]

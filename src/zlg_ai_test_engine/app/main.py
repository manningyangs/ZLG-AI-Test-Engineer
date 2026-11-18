"""PLAT-01：应用入口（console script ``zlg-ai-test-engineer`` 指向 ``main``）。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from zlg_ai_test_engine.ui.main_window import MainWindow
from zlg_ai_test_engine.ui.pages import create_pages, resolve_theme
from zlg_ai_test_engine.ui.theme import apply_theme


def main() -> int:
    """启动桌面应用外壳。

    Returns:
        事件循环退出码。
    """
    app = QApplication(sys.argv)
    app.setApplicationName("ZLG")
    app.setOrganizationName("zlg-ai-test-engine")

    # 主题读取容错：配置不可用时回退 light（resolve_theme 内部已兜底）
    apply_theme(app, resolve_theme())

    window = MainWindow()
    create_pages(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

"""应用入口：装配 QApplication、主题、主窗口。"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PyQt6.QtWidgets import QApplication

from netops.core.context import APP_VERSION
from netops.core.contracts import REGISTRY
from netops.ui.icons import app_icon
from netops.ui.main_window import WINDOW_TITLE, MainWindow
from netops.ui.theme import apply_theme

__all__ = ["main"]


def _application(argv: Sequence[str]) -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    app = QApplication(list(argv))
    app.setApplicationName(WINDOW_TITLE)
    app.setApplicationDisplayName(WINDOW_TITLE)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("NetOps")
    app.setWindowIcon(app_icon())
    return app


def _load_plugins() -> None:
    REGISTRY.discover("netops.tools")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    app = _application(args)
    apply_theme(app)
    _load_plugins()

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

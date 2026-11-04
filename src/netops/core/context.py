"""AppContext：UI 取服务与全局状态的唯一入口。

P0 时只承载路径与版本；现在挂上数据库服务，页面通过 ``ctx.db`` 读写设备备注、
脚本收藏等持久化数据，避免全局单例。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from netops.platform_.os_layer import APP_NAME, data_dir

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from netops.core.db import Database

__all__ = ["AppContext", "APP_VERSION", "DB_FILENAME", "create_context"]

APP_VERSION = "0.2.0"

DB_FILENAME = "netops.sqlite3"


@dataclass(frozen=True, slots=True)
class AppContext:
    data_dir: Path
    app_name: str = APP_NAME
    version: str = APP_VERSION
    db: Database | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / DB_FILENAME


def create_context(*, open_db: bool = True) -> AppContext:
    """默认打开数据库（入口与页面使用）；测试构造轻量上下文时可传 ``open_db=False``。"""
    directory = data_dir()
    database = None
    if open_db:
        from netops.core.db import Database

        database = Database(directory / DB_FILENAME)
        database.connect()
    return AppContext(data_dir=directory, db=database)

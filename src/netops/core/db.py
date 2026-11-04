"""SQLite 存储层：WAL 连接、版本化迁移、设备备注与脚本收藏。

设计要点：
- 不使用 ORM，手写 SQL 便于审计；
- 迁移脚本放在 ``migrations/`` 目录，按文件名数字前缀顺序执行；
- 每条迁移在事务内执行并写入 ``schema_version``，重复执行幂等；
- UI 与后续 netmon 进程通过 WAL 共享同一文件。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

__all__ = [
    "Database",
    "Device",
    "ScriptFavorite",
    "MIGRATIONS_PACKAGE",
]

_log = logging.getLogger(__name__)

MIGRATIONS_PACKAGE = "netops.core.migrations"
DB_FILENAME = "netops.sqlite3"


@dataclass(frozen=True, slots=True)
class Device:
    id: int
    ip: str
    remark: str
    model: str
    location: str
    group_name: str
    mac: str
    updated_at: float


@dataclass(frozen=True, slots=True)
class ScriptFavorite:
    id: int
    name: str
    vendor: str
    category: str
    content: str
    tags: str
    created_at: float
    updated_at: float


class Database:
    """单文件 SQLite 封装。线程安全由使用方保证（每线程一个连接或串行访问）。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # -- 连接与迁移 ---------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        self._ensure_schema_table()
        self.migrate()
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self.connect()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure_schema_table(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  name TEXT NOT NULL,"
            "  applied_at REAL NOT NULL"
            ")"
        )
        self.conn.commit()

    def current_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row["v"] or 0)

    def applied_versions(self) -> list[int]:
        rows = self.conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
        return [int(row["version"]) for row in rows]

    def migrate(self) -> list[int]:
        """执行所有尚未应用的迁移，返回本次新应用的版本号。"""
        applied: list[int] = []
        for version, name, sql in _iter_migrations():
            if self.conn.execute(
                "SELECT 1 FROM schema_version WHERE version=?", (version,)
            ).fetchone():
                continue
            _log.info("应用数据库迁移 %03d_%s", version, name)
            # executescript 会隐式提交，无法嵌套在外层事务里，因此先开事务再执行。
            conn = self.conn
            try:
                conn.executescript("BEGIN;\n" + sql)
                conn.execute(
                    "INSERT INTO schema_version(version, name, applied_at) VALUES(?,?,?)",
                    (version, name, time.time()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied.append(version)
        return applied

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # -- 设备备注 -----------------------------------------------------------
    def upsert_device(
        self,
        ip: str,
        *,
        remark: str = "",
        model: str = "",
        location: str = "",
        group_name: str = "",
        mac: str = "",
    ) -> Device:
        if not ip.strip():
            raise ValueError("设备 IP 不能为空")
        now = time.time()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO devices(ip, remark, model, location, group_name, mac, updated_at)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(ip) DO UPDATE SET"
                " remark=excluded.remark, model=excluded.model, location=excluded.location,"
                " group_name=excluded.group_name, mac=excluded.mac, updated_at=excluded.updated_at",
                (ip.strip(), remark, model, location, group_name, mac, now),
            )
        device = self.get_device(ip)
        assert device is not None
        return device

    def get_device(self, ip: str) -> Device | None:
        row = self.conn.execute("SELECT * FROM devices WHERE ip=?", (ip.strip(),)).fetchone()
        return _row_to_device(row) if row else None

    def list_devices(self) -> list[Device]:
        rows = self.conn.execute("SELECT * FROM devices ORDER BY ip").fetchall()
        return [_row_to_device(row) for row in rows]

    def devices_for_ips(self, ips: list[str]) -> dict[str, Device]:
        """批量按 IP 查备注，供 Traceroute 逐跳匹配。"""
        if not ips:
            return {}
        placeholders = ",".join("?" for _ in ips)
        rows = self.conn.execute(
            f"SELECT * FROM devices WHERE ip IN ({placeholders})", [ip.strip() for ip in ips]
        ).fetchall()
        return {row["ip"]: _row_to_device(row) for row in rows}

    def delete_device(self, ip: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM devices WHERE ip=?", (ip.strip(),))
        return cursor.rowcount > 0

    # -- 脚本收藏 -----------------------------------------------------------
    def add_favorite(
        self,
        name: str,
        content: str,
        *,
        vendor: str = "",
        category: str = "",
        tags: str = "",
    ) -> ScriptFavorite:
        if not name.strip():
            raise ValueError("收藏名称不能为空")
        if not content.strip():
            raise ValueError("收藏内容不能为空")
        now = time.time()
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO script_favorites(name, vendor, category, content, tags, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (name.strip(), vendor, category, content, tags, now, now),
            )
        favorite = self.get_favorite(int(cursor.lastrowid))
        assert favorite is not None
        return favorite

    def get_favorite(self, favorite_id: int) -> ScriptFavorite | None:
        row = self.conn.execute(
            "SELECT * FROM script_favorites WHERE id=?", (favorite_id,)
        ).fetchone()
        return _row_to_favorite(row) if row else None

    def list_favorites(
        self, *, vendor: str = "", category: str = "", keyword: str = ""
    ) -> list[ScriptFavorite]:
        sql = "SELECT * FROM script_favorites WHERE 1=1"
        params: list[str] = []
        if vendor:
            sql += " AND vendor=?"
            params.append(vendor)
        if category:
            sql += " AND category=?"
            params.append(category)
        if keyword:
            sql += " AND (name LIKE ? OR content LIKE ? OR tags LIKE ?)"
            like = f"%{keyword}%"
            params.extend([like, like, like])
        sql += " ORDER BY updated_at DESC"
        return [_row_to_favorite(row) for row in self.conn.execute(sql, params).fetchall()]

    def update_favorite(
        self,
        favorite_id: int,
        *,
        name: str | None = None,
        content: str | None = None,
        vendor: str | None = None,
        category: str | None = None,
        tags: str | None = None,
    ) -> ScriptFavorite | None:
        fields: dict[str, str] = {}
        for key, value in (
            ("name", name),
            ("content", content),
            ("vendor", vendor),
            ("category", category),
            ("tags", tags),
        ):
            if value is not None:
                fields[key] = value
        if not fields:
            return self.get_favorite(favorite_id)
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.transaction() as conn:
            conn.execute(
                f"UPDATE script_favorites SET {assignments}, updated_at=? WHERE id=?",
                [*fields.values(), time.time(), favorite_id],
            )
        return self.get_favorite(favorite_id)

    def delete_favorite(self, favorite_id: int) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM script_favorites WHERE id=?", (favorite_id,))
        return cursor.rowcount > 0


# -- 迁移文件 -----------------------------------------------------------------


def _iter_migrations() -> list[tuple[int, str, str]]:
    package = resources.files(MIGRATIONS_PACKAGE)
    migrations: list[tuple[int, str, str]] = []
    for entry in sorted(package.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".sql"):
            continue
        prefix, _, name = entry.name.partition("_")
        if not prefix.isdigit():
            raise RuntimeError(f"迁移文件名必须以数字前缀开头：{entry.name}")
        migrations.append(
            (int(prefix), name.removesuffix(".sql"), entry.read_text(encoding="utf-8"))
        )
    return migrations


def _row_to_device(row: sqlite3.Row) -> Device:
    return Device(
        id=int(row["id"]),
        ip=row["ip"],
        remark=row["remark"] or "",
        model=row["model"] or "",
        location=row["location"] or "",
        group_name=row["group_name"] or "",
        mac=row["mac"] or "",
        updated_at=float(row["updated_at"]),
    )


def _row_to_favorite(row: sqlite3.Row) -> ScriptFavorite:
    return ScriptFavorite(
        id=int(row["id"]),
        name=row["name"],
        vendor=row["vendor"] or "",
        category=row["category"] or "",
        content=row["content"],
        tags=row["tags"] or "",
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )

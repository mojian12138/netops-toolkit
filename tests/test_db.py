"""Database 单测：迁移幂等、设备备注与脚本收藏 CRUD。"""

from __future__ import annotations

import pytest

from netops.core.db import Database


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "test.sqlite3")
    yield database
    database.close()


def test_fresh_database_applies_all_migrations(db: Database) -> None:
    db.connect()
    assert db.current_version() == 2
    assert db.applied_versions() == [1, 2]


def test_migrate_is_idempotent(tmp_path) -> None:
    path = tmp_path / "again.sqlite3"
    first = Database(path)
    assert first.connect() is not None  # 首次连接自动迁移
    assert first.migrate() == []
    first.close()
    second = Database(path)
    assert second.migrate() == []
    assert second.current_version() == 2
    second.close()


def test_wal_mode_enabled(db: Database) -> None:
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_device_upsert_and_lookup(db: Database) -> None:
    created = db.upsert_device("10.0.0.1", remark="核心交换机", model="S5560X", location="机房A")
    assert created.remark == "核心交换机"
    updated = db.upsert_device("10.0.0.1", remark="核心交换机-主", model="S5560X")
    assert updated.id == created.id
    assert updated.remark == "核心交换机-主"
    assert db.list_devices()[0].location == ""  # upsert 覆盖整行


def test_devices_for_ips_bulk(db: Database) -> None:
    db.upsert_device("10.0.0.1", remark="A")
    db.upsert_device("10.0.0.2", remark="B")
    result = db.devices_for_ips(["10.0.0.2", "10.0.0.99"])
    assert set(result) == {"10.0.0.2"}
    assert result["10.0.0.2"].remark == "B"
    assert db.devices_for_ips([]) == {}


def test_delete_device(db: Database) -> None:
    db.upsert_device("10.0.0.3")
    assert db.delete_device("10.0.0.3")
    assert not db.delete_device("10.0.0.3")
    assert db.get_device("10.0.0.3") is None


def test_device_rejects_empty_ip(db: Database) -> None:
    with pytest.raises(ValueError):
        db.upsert_device("  ")


def test_favorite_crud_and_search(db: Database) -> None:
    fav = db.add_favorite(
        "核心交换机开局",
        "sysname CORE\n",
        vendor="H3C",
        category="开局",
        tags="core",
    )
    assert fav.id > 0
    loaded = db.get_favorite(fav.id)
    assert loaded is not None and loaded.content == "sysname CORE\n"

    assert [f.id for f in db.list_favorites(vendor="H3C")] == [fav.id]
    assert db.list_favorites(vendor="华为") == []
    assert [f.id for f in db.list_favorites(keyword="CORE")] == [fav.id]
    assert [f.id for f in db.list_favorites(keyword="core")] == [fav.id]

    renamed = db.update_favorite(fav.id, name="核心开局 v2")
    assert renamed is not None and renamed.name == "核心开局 v2"
    assert db.delete_favorite(fav.id)
    assert db.list_favorites() == []


def test_preset_does_not_store_sensitive_values(db: Database) -> None:
    preset = db.add_preset(
        "核心交换机",
        "h3c.comware_v7",
        {"hostname": "CORE", "password": "secret", "mgmt_ip": "10.0.0.2/24"},
        ("password", "mgmt_ip"),
    )
    assert preset.values == {"hostname": "CORE"}
    assert "secret" not in db.conn.execute("SELECT values_json FROM script_presets").fetchone()[0]

    with pytest.raises(ValueError):
        db.add_favorite("", "content")
    with pytest.raises(ValueError):
        db.add_favorite("name", "   ")


def test_favorite_persists_across_reconnect(tmp_path) -> None:
    path = tmp_path / "persist.sqlite3"
    first = Database(path)
    first.add_favorite("port-desc", "description x")
    first.close()
    second = Database(path)
    assert [f.name for f in second.list_favorites()] == ["port-desc"]
    second.close()

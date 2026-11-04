"""设备备注簿单测：有库 / 无库两条路径都要成立。"""

from __future__ import annotations

import pytest

from netops.core.db import Database
from netops.services.devices import DeviceBook, DeviceBookError


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "remarks.sqlite3")
    database.connect()
    yield database
    database.close()


@pytest.fixture()
def book(db: Database) -> DeviceBook:
    return DeviceBook(db)


# ---------------------------------------------------------------------------
# 无数据库：扫描页在 open_db=False 时要照常工作
# ---------------------------------------------------------------------------


def test_book_without_database_degrades_reads_to_empty() -> None:
    book = DeviceBook(None)
    assert book.available is False
    assert book.all_devices() == []
    assert book.remarks_for(["10.0.0.1"]) == {}
    assert book.remark_of("10.0.0.1") is None
    assert book.forget("10.0.0.1") is False


def test_book_without_database_rejects_writes() -> None:
    book = DeviceBook(None)
    with pytest.raises(DeviceBookError, match="不可用"):
        book.mark("10.0.0.1", remark="核心交换机")


# ---------------------------------------------------------------------------
# 有数据库
# ---------------------------------------------------------------------------


def test_mark_creates_then_updates(book: DeviceBook) -> None:
    assert book.available is True
    created = book.mark("10.0.0.1", remark="核心交换机", model="S5560X", mac="00:11:22:33:44:55")
    assert created.ip == "10.0.0.1"
    assert created.remark == "核心交换机"

    updated = book.mark("10.0.0.1", remark="核心交换机-主")
    assert updated.id == created.id
    assert updated.remark == "核心交换机-主"
    # upsert 覆盖整行，未传的字段被清空——这是 Database 的既有语义，此处固化
    assert updated.model == ""


def test_remarks_for_bulk_ignores_unknown_and_blank(book: DeviceBook) -> None:
    book.mark("10.0.0.1", remark="A")
    book.mark("10.0.0.2", remark="B")
    result = book.remarks_for(["10.0.0.2", "10.0.0.99", "", "  "])
    assert set(result) == {"10.0.0.2"}
    assert result["10.0.0.2"].remark == "B"
    assert book.remarks_for([]) == {}


def test_remark_of_single(book: DeviceBook) -> None:
    book.mark("10.0.0.7", remark="AP-3F")
    assert book.remark_of(" 10.0.0.7 ").remark == "AP-3F"
    assert book.remark_of("10.0.0.8") is None


def test_mark_rejects_blank_ip(book: DeviceBook) -> None:
    with pytest.raises(DeviceBookError, match="IP 不能为空"):
        book.mark("   ")


def test_forget_removes_device(book: DeviceBook) -> None:
    book.mark("10.0.0.3")
    assert book.forget("10.0.0.3") is True
    assert book.forget("10.0.0.3") is False
    assert book.all_devices() == []


def test_remarks_survive_reconnect(book: DeviceBook, db: Database) -> None:
    book.mark("10.0.0.9", remark="持久化测试")
    db.close()
    restored = DeviceBook(Database(db.path))
    restored._db.connect()  # noqa: SLF001 - 测试里直接补连接，模拟应用重启
    assert restored.remark_of("10.0.0.9").remark == "持久化测试"
    restored._db.close()  # noqa: SLF001

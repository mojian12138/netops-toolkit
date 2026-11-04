"""设备备注簿：把 ``core.db`` 的 devices 表包装成扫描页可直接用的接口。

为什么要单独一层而不是页面直接调 ``Database``：

- 页面在 ``open_db=False`` 时必须照样能跑（单测、首次启动失败）；
  ``DeviceBook`` 在无数据库时静默退化为「只读空表」，不让扫描页崩掉；
- ``devices`` 表同时被 Traceroute 与监控目标复用，读写语义集中在这里，
  避免三个页面各写一套 SQL；
- 写操作统一走 ``upsert_device``，保证「标记设备」是幂等的。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable

from netops.core.db import Database, Device

__all__ = ["DeviceBook", "DeviceBookError"]

_log = logging.getLogger(__name__)


class DeviceBookError(RuntimeError):
    """备注簿不可用（无数据库或写入失败）。消息面向用户，可直接展示。"""


class DeviceBook:
    """设备备注簿。``database`` 为 ``None`` 时所有读返回空、所有写报错。"""

    def __init__(self, database: Database | None) -> None:
        self._db = database

    @property
    def available(self) -> bool:
        return self._db is not None

    # -- 读 -----------------------------------------------------------------
    def all_devices(self) -> list[Device]:
        if self._db is None:
            return []
        try:
            return self._db.list_devices()
        except sqlite3.Error as exc:  # pragma: no cover - 磁盘损坏等极端情况
            _log.debug("读取设备备注失败", exc_info=True)
            raise DeviceBookError(f"读取设备备注失败：{exc}") from exc

    def remarks_for(self, ips: Iterable[str]) -> dict[str, Device]:
        """按 IP 批量查备注；无数据库时返回空字典（扫描页照常展示）。"""
        if self._db is None:
            return {}
        values = [ip.strip() for ip in ips if ip and ip.strip()]
        if not values:
            return {}
        try:
            return self._db.devices_for_ips(values)
        except sqlite3.Error as exc:
            # 这里以前是 debug 级别，把「在工作线程里用主线程 sqlite 连接」这种
            # ProgrammingError 吞得无声无息，表现为备注莫名全空。warning 起步，
            # 让这类线程模型错误在日志里看得见。
            _log.warning("批量读取设备备注失败，本次备注将为空：%s", exc)
            return {}

    def remark_of(self, ip: str) -> Device | None:
        return self.remarks_for([ip]).get(ip.strip())

    # -- 写 -----------------------------------------------------------------
    def mark(
        self,
        ip: str,
        *,
        remark: str = "",
        model: str = "",
        location: str = "",
        group_name: str = "",
        mac: str = "",
    ) -> Device:
        """写入 / 更新一台设备的备注。无数据库时抛 ``DeviceBookError``。"""
        if self._db is None:
            raise DeviceBookError("设备备注库不可用（数据库未打开），无法保存备注")
        if not ip.strip():
            raise DeviceBookError("设备 IP 不能为空")
        try:
            return self._db.upsert_device(
                ip.strip(),
                remark=remark,
                model=model,
                location=location,
                group_name=group_name,
                mac=mac,
            )
        except (sqlite3.Error, ValueError) as exc:
            _log.debug("写入设备备注失败", exc_info=True)
            raise DeviceBookError(f"保存设备备注失败：{exc}") from exc

    def forget(self, ip: str) -> bool:
        """删除一台设备的备注；无数据库时返回 ``False``。"""
        if self._db is None:
            return False
        try:
            return self._db.delete_device(ip.strip())
        except sqlite3.Error as exc:  # pragma: no cover
            _log.debug("删除设备备注失败", exc_info=True)
            raise DeviceBookError(f"删除设备备注失败：{exc}") from exc

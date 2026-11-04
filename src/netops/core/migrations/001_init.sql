-- 001 初始业务表：设备备注库 + 脚本收藏
-- devices 被 Traceroute 备注、IP 扫描、后续监控目标共同复用。

CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT    NOT NULL UNIQUE,
    remark      TEXT    NOT NULL DEFAULT '',
    model       TEXT    NOT NULL DEFAULT '',
    location    TEXT    NOT NULL DEFAULT '',
    group_name  TEXT    NOT NULL DEFAULT '',
    mac         TEXT    NOT NULL DEFAULT '',
    updated_at  REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_mac ON devices(mac);

CREATE TABLE IF NOT EXISTS script_favorites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    vendor      TEXT    NOT NULL DEFAULT '',
    category    TEXT    NOT NULL DEFAULT '',
    content     TEXT    NOT NULL,
    tags        TEXT    NOT NULL DEFAULT '',
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_favorites_vendor ON script_favorites(vendor, category);
CREATE INDEX IF NOT EXISTS idx_favorites_name ON script_favorites(name);

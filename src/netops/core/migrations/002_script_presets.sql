-- 002 结构化脚本预设；敏感字段只保存脱敏状态，不保存明文值。
CREATE TABLE IF NOT EXISTS script_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    template_id TEXT NOT NULL,
    values_json TEXT NOT NULL,
    sensitive_fields_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_script_presets_updated ON script_presets(updated_at);

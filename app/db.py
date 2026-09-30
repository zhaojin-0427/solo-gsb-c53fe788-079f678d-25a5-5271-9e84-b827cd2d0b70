"""SQLite 数据访问层：型录、灯具、线路、保留区、已发布地址表与配置版本。"""
import os
import sqlite3
import threading

DB_PATH = os.environ.get("DMX_DB", "/data/dmx.db")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS modes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    channels INTEGER NOT NULL CHECK (channels >= 1)
);
CREATE TABLE IF NOT EXISTS fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    mode_id INTEGER REFERENCES modes(id) ON DELETE SET NULL,
    channels INTEGER NOT NULL CHECK (channels >= 1),
    locked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    start INTEGER NOT NULL CHECK (start >= 1),
    "end" INTEGER NOT NULL CHECK ("end" <= 512),
    CHECK (start <= "end")
);
CREATE TABLE IF NOT EXISTS assignments (
    fixture_id INTEGER PRIMARY KEY REFERENCES fixtures(id) ON DELETE CASCADE,
    line_id INTEGER NOT NULL,
    start INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS previews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

MAX_LINES = 4
LINE_CAPACITY = 512


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    with conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('config_version', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('published_version', 0)"
        )


def get_meta(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return int(row["value"])


def bump_config_version(conn: sqlite3.Connection) -> int:
    """配置变更时调用：版本号 +1，使旧预览失效。"""
    conn.execute(
        "UPDATE meta SET value = value + 1 WHERE key = 'config_version'"
    )
    return get_meta(conn, "config_version")


def load_state(conn: sqlite3.Connection) -> dict:
    """汇总前端所需的全部状态。"""
    models = [dict(r) for r in conn.execute("SELECT * FROM models ORDER BY id")]
    modes = [dict(r) for r in conn.execute("SELECT * FROM modes ORDER BY id")]
    for m in models:
        m["modes"] = [mo for mo in modes if mo["model_id"] == m["id"]]
    fixtures = [dict(r) for r in conn.execute("SELECT * FROM fixtures ORDER BY id")]
    lines = [dict(r) for r in conn.execute("SELECT * FROM lines ORDER BY id")]
    reservations = [
        dict(r) for r in conn.execute("SELECT * FROM reservations ORDER BY line_id, start")
    ]
    assignments = {
        r["fixture_id"]: {"line_id": r["line_id"], "start": r["start"]}
        for r in conn.execute("SELECT * FROM assignments")
    }
    return {
        "models": models,
        "fixtures": fixtures,
        "lines": lines,
        "reservations": reservations,
        "assignments": assignments,
        "config_version": get_meta(conn, "config_version"),
        "published_version": get_meta(conn, "published_version"),
        "max_lines": MAX_LINES,
        "line_capacity": LINE_CAPACITY,
    }

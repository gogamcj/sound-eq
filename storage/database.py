"""SQLite 저장소. 이 프로그램의 기록/프로필만 저장하며, 믹서 자체의 SHOW/SNAP/SNIP 데이터와는 무관하다."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    data TEXT NOT NULL              -- JSON: 채널 매핑/악기/자동허용/설정
);
CREATE TABLE IF NOT EXISTS eq_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    profile TEXT,
    mixer_ch INTEGER NOT NULL,
    source TEXT NOT NULL,           -- 'user_approved' | 'auto' | 'restore' | 'verify_tool'
    status TEXT NOT NULL,
    before TEXT NOT NULL,           -- JSON
    requested TEXT NOT NULL,
    after TEXT NOT NULL,
    reason TEXT,
    recommendation TEXT,            -- JSON (추천 근거)
    restored_by INTEGER,            -- 이 변경을 되돌린 eq_changes.id
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    mixer_ch INTEGER,
    decision TEXT NOT NULL,         -- 'approved' | 'rejected'
    detail TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,            -- 'info' | 'warning' | 'error'
    category TEXT NOT NULL,         -- 'lan' | 'audio' | 'safety' | 'apply' ...
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    usb_ch INTEGER NOT NULL,
    mixer_ch INTEGER,
    instrument TEXT,
    summary TEXT NOT NULL           -- JSON
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def log_event(self, level: str, category: str, message: str) -> None:
        self.execute("INSERT INTO events(ts, level, category, message) VALUES (?,?,?,?)",
                     (time.time(), level, category, message))


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)

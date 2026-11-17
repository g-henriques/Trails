"""SQLite storage. One table of trails (catalog + track) and a key/value table for scrape status."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS trails (
    id TEXT PRIMARY KEY,
    ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    number TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    district TEXT NOT NULL DEFAULT '',
    municipality TEXT NOT NULL DEFAULT '',
    distance_km REAL,
    duration_min INTEGER,
    difficulty TEXT NOT NULL DEFAULT '',
    route_type TEXT NOT NULL DEFAULT '',
    promoter TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    osm_relation INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    closed INTEGER NOT NULL DEFAULT 0,
    sources TEXT NOT NULL DEFAULT '[]',
    start_lat REAL,
    start_lon REAL,
    bbox TEXT,
    track TEXT,
    track_km REAL,
    track_relation INTEGER,
    track_fetched_at TEXT,
    track_error TEXT,
    seen_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS trails_district ON trails(district);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Columns the catalog step writes; the track columns are only touched by the track step.
CATALOG_COLUMNS = [
    "ref", "kind", "number", "code", "name", "district", "municipality", "distance_km", "duration_min",
    "difficulty", "route_type", "promoter", "website", "osm_relation", "notes", "closed", "sources",
    "seen_at", "updated_at",
]

_local = threading.local()


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    conn = getattr(_local, "conns", {}).get(path)
    if conn is None:
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(SCHEMA)
        _local.__dict__.setdefault("conns", {})[path] = conn
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )

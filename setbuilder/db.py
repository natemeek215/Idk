"""SQLite storage.

Two caches matter and must never be invalidated casually:

* ``analysis`` keyed by ``cache_key`` -- the file's SHA-256 for local files, or
  ``sc:<track id>`` for a stream.  Analysis is expensive; we only ever do it
  once per key.
* ``local_files`` keyed by path, with mtime/size so a rescan can skip hashing
  files that have not changed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists (
    id            INTEGER PRIMARY KEY,
    sc_id         TEXT UNIQUE,
    url           TEXT,
    title         TEXT,
    imported_at   REAL
);

CREATE TABLE IF NOT EXISTS tracks (
    id                INTEGER PRIMARY KEY,
    sc_id             TEXT UNIQUE,
    playlist_id       INTEGER,
    position          INTEGER,
    title             TEXT,
    artist            TEXT,
    permalink_url     TEXT,
    duration_ms       INTEGER,
    access            TEXT,           -- playable | preview | blocked
    artwork_url       TEXT,
    label             TEXT,
    genre             TEXT,
    stream_url        TEXT,
    -- resolution
    source            TEXT,           -- local | preview | stream | tag | unmatched
    local_path        TEXT,
    match_score       REAL,
    match_state       TEXT,           -- matched | possible | unmatched | confirmed | rejected
    cache_key         TEXT,
    FOREIGN KEY (playlist_id) REFERENCES playlists(id)
);

CREATE TABLE IF NOT EXISTS local_files (
    path          TEXT PRIMARY KEY,
    sha256        TEXT,
    size          INTEGER,
    mtime         REAL,
    title         TEXT,
    artist        TEXT,
    album         TEXT,
    label         TEXT,
    tag_bpm       REAL,
    tag_key       TEXT,
    duration_s    REAL,
    match_key     TEXT,
    scanned_at    REAL
);

CREATE TABLE IF NOT EXISTS analysis (
    cache_key     TEXT PRIMARY KEY,
    bpm           REAL,
    bpm_raw       REAL,
    bpm_source    TEXT,          -- analyzed | tag
    camelot       TEXT,
    key_name      TEXT,
    key_source    TEXT,          -- essentia | chroma | tag
    features      TEXT,          -- JSON blob of the raw feature vector
    energy        REAL,          -- 1-10, heuristic or model
    energy_source TEXT,          -- heuristic | model
    confidence    TEXT,          -- full | preview | tag
    duration_s    REAL,
    analyzed_at   REAL,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS labels (
    cache_key     TEXT PRIMARY KEY,
    rating        REAL,
    labeled_at    REAL
);

CREATE TABLE IF NOT EXISTS model (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    payload       TEXT,
    trained_at    REAL,
    n_labels      INTEGER,
    r2            REAL
);

CREATE TABLE IF NOT EXISTS custom_arcs (
    name          TEXT PRIMARY KEY,
    points        TEXT,
    created_at    REAL
);

CREATE TABLE IF NOT EXISTS sets (
    id            INTEGER PRIMARY KEY,
    name          TEXT,
    arc           TEXT,
    set_length    INTEGER,
    track_ids     TEXT,
    updated_at    REAL
);

CREATE INDEX IF NOT EXISTS idx_tracks_playlist ON tracks(playlist_id);
CREATE INDEX IF NOT EXISTS idx_local_match ON local_files(match_key);
"""


def connect() -> sqlite3.Connection:
    """Per-thread connection.  The worker pool uses processes, not threads,
    but the API server is threaded and sqlite connections are not shareable."""
    connection = getattr(_local, "connection", None)
    if connection is None:
        config.ensure_home()
        connection = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(SCHEMA)
        _local.connection = connection
    return connection


def query(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    cursor = connect().execute(sql, tuple(params))
    return [dict(row) for row in cursor.fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    connection = connect()
    cursor = connection.execute(sql, tuple(params))
    connection.commit()
    return cursor


def execute_many(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    connection = connect()
    connection.executemany(sql, [tuple(row) for row in rows])
    connection.commit()


# --- analysis cache ------------------------------------------------------

def get_analysis(cache_key: str) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM analysis WHERE cache_key = ?", (cache_key,))
    if row and row.get("features"):
        try:
            row["features"] = json.loads(row["features"])
        except json.JSONDecodeError:
            row["features"] = {}
    return row


def save_analysis(cache_key: str, payload: Dict[str, Any]) -> None:
    features = payload.get("features") or {}
    execute(
        """
        INSERT INTO analysis (cache_key, bpm, bpm_raw, bpm_source, camelot, key_name,
                              key_source, features, energy, energy_source, confidence,
                              duration_s, analyzed_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            bpm=excluded.bpm, bpm_raw=excluded.bpm_raw, bpm_source=excluded.bpm_source,
            camelot=excluded.camelot, key_name=excluded.key_name,
            key_source=excluded.key_source, features=excluded.features,
            energy=excluded.energy, energy_source=excluded.energy_source,
            confidence=excluded.confidence, duration_s=excluded.duration_s,
            analyzed_at=excluded.analyzed_at, error=excluded.error
        """,
        (
            cache_key, payload.get("bpm"), payload.get("bpm_raw"),
            payload.get("bpm_source"), payload.get("camelot"), payload.get("key_name"),
            payload.get("key_source"), json.dumps(features), payload.get("energy"),
            payload.get("energy_source"), payload.get("confidence"),
            payload.get("duration_s"), payload.get("analyzed_at"), payload.get("error"),
        ),
    )

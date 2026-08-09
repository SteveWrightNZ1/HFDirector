from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    product TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'metservice',
    path TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_time TEXT,
    width INTEGER,
    height INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS assets_product_time ON assets(product, observed_at DESC);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    local_time TEXT NOT NULL,
    weekdays TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
    products TEXT NOT NULL,
    source_policy TEXT NOT NULL DEFAULT '{}',
    profile TEXT NOT NULL DEFAULT '1',
    last_slot TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS broadcast_runs (
    id INTEGER PRIMARY KEY,
    schedule_id INTEGER REFERENCES schedules(id),
    slot TEXT,
    state TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(schedule_id, slot)
);

CREATE TABLE IF NOT EXISTS broadcast_items (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES broadcast_runs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    request_id TEXT NOT NULL UNIQUE,
    qsstv_queue_id TEXT,
    state TEXT NOT NULL,
    detail TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, position)
);

CREATE TABLE IF NOT EXISTS bsr_decisions (
    bsr_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    note TEXT,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS radios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    control_type TEXT NOT NULL DEFAULT 'rigctld',
    control_endpoint TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    ptt_type TEXT NOT NULL DEFAULT 'serial',
    ptt_device TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_interfaces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    device TEXT NOT NULL,
    radio_id INTEGER REFERENCES radios(id) ON DELETE SET NULL,
    capture_gain INTEGER NOT NULL DEFAULT 100,
    playback_gain INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    callsign TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'operator',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialise(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            asset_columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
            if "source" not in asset_columns:
                connection.execute("ALTER TABLE assets ADD COLUMN source TEXT NOT NULL DEFAULT 'metservice'")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS assets_source_product_time ON assets(source,product,observed_at DESC)"
            )
            connection.execute("UPDATE assets SET product='rain-forecast' WHERE product='rain-5day'")
            connection.execute(
                "UPDATE assets SET product='satellite-infrared' WHERE product='satellite-tasman-infrared'"
            )
            schedule_columns = {row[1] for row in connection.execute("PRAGMA table_info(schedules)")}
            if "source_policy" not in schedule_columns:
                connection.execute("ALTER TABLE schedules ADD COLUMN source_policy TEXT NOT NULL DEFAULT '{}'")
            connection.execute(
                "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES('tx_inhibit','1',?)",
                (utcnow(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES('bsr_policy','off',?)",
                (utcnow(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES('bsr_callsigns','',?)",
                (utcnow(),),
            )
            count = connection.execute("SELECT count(*) FROM schedules").fetchone()[0]
            if not count:
                now = utcnow()
                connection.execute(
                    """INSERT INTO schedules
                       (name,enabled,local_time,products,profile,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        "Daily marine weather",
                        0,
                        "09:00",
                        "pressure-analysis,rain-radar,marine-high-seas-chart",
                        "1",
                        now,
                        now,
                    ),
                )

    def setting(self, key: str, default: str = "") -> str:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (key, value, utcnow()),
            )

    @staticmethod
    def rows(rows) -> list[dict]:
        return [dict(row) for row in rows]

    @staticmethod
    def metadata(row: dict) -> dict:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json", "{}"))
        return result

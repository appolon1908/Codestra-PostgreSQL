"""Operator-written metadata only; HTTP opens this store in SQLite read-only mode."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS backups_order ON backups(created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS rehearsals (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rehearsals_order ON rehearsals(created_at DESC, id DESC);
"""


def location(default=None):
    return Path(
        os.environ.get(
            "OPERATIONS_CATALOG", str(default or "/catalog/operations.sqlite3")
        )
    )


def write(kind, record, default=None):
    if kind not in ("backups", "rehearsals"):
        raise ValueError("Invalid catalog kind")
    path = location(default)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
        conn.executescript(SCHEMA)
        conn.execute(
            f"INSERT INTO {kind} VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET created_at=excluded.created_at, payload=excluded.payload",
            (
                record["backup_id" if kind == "backups" else "report_id"],
                record["created_at"],
                json.dumps(record),
            ),
        )
    path.chmod(0o600)


def read(kind, identifier=None, default=None):
    if kind not in ("backups", "rehearsals"):
        raise ValueError("Invalid catalog kind")
    path = location(default).resolve()
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    ) as conn:
        if identifier is not None:
            row = conn.execute(
                f"SELECT payload FROM {kind} WHERE id = ?", (identifier,)
            ).fetchone()
            return json.loads(row[0]) if row else None
        rows = conn.execute(
            f"SELECT payload FROM {kind} ORDER BY created_at DESC, id DESC LIMIT 101"
        ).fetchall()
        return {
            "items": [json.loads(row[0]) for row in rows[:100]],
            "truncated": len(rows) > 100,
        }


def counts():
    """Bounded output for metrics; no business identifiers as labels."""
    with closing(
        sqlite3.connect(location().resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    ) as conn:
        return {
            kind: conn.execute(f"SELECT count(*) FROM {kind}").fetchone()[0]
            for kind in ("backups", "rehearsals")
        }

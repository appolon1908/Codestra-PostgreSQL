"""Restricted operator metadata import. Never executes maintenance or failover."""

import argparse
import json
import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from . import catalog
else:
    import catalog

KINDS = {
    "ownership": {"database_ref", "schema_ref", "owning_repository", "service_id"},
    "storage-samples": {"database_bytes", "disk_free_percent", "scope_ref"},
    "base-backups": {"backup_ref", "completed_at", "verified", "wal_start", "wal_end"},
    "recovery-rehearsals": {
        "scenario",
        "started_at",
        "ended_at",
        "rto_target_seconds",
        "duration_seconds",
        "data_integrity",
        "wal_start",
        "wal_end",
        "outcome",
    },
}
BOUND_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
ATTESTATIONS = {
    "base-backups": {"encrypted", "offsite_verified"},
    "recovery-rehearsals": {"application_verified", "rpo_met"},
}


def binding(tenant_id, database_ref):
    if any(
        not isinstance(value, str) or not BOUND_REF.fullmatch(value)
        for value in (tenant_id, database_ref)
    ):
        raise ValueError("Invalid evidence binding")


def stamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed > datetime.now(timezone.utc):
        raise ValueError("Timezone required; future evidence rejected")
    return parsed


def reference(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_./:-]{1,160}", value
    ):
        raise ValueError("Invalid operational reference")


def register(kind, record):
    required = KINDS.get(kind, set()) | {"id", "created_at"}
    optional = ATTESTATIONS.get(kind, set()) | (
        {"tenant_id", "database_ref"} if kind in ATTESTATIONS else set()
    )
    if (
        not isinstance(record, dict)
        or kind not in KINDS
        or not required <= set(record)
        or set(record) - required - optional
    ):
        raise ValueError("Unexpected metadata fields")
    if kind in ATTESTATIONS and set(record) & optional:
        binding(record.get("tenant_id"), record.get("database_ref"))
        for key in ATTESTATIONS[kind] & set(record):
            if type(record[key]) is not bool:
                raise ValueError("Invalid evidence attestation")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", record["id"]):
        raise ValueError("Invalid id")
    record = dict(record)
    for key in ("created_at", "completed_at", "started_at", "ended_at"):
        if key in record:
            record[key] = stamp(record[key]).astimezone(timezone.utc).isoformat()
    for key in (
        "database_ref",
        "schema_ref",
        "owning_repository",
        "service_id",
        "scope_ref",
        "backup_ref",
        "scenario",
    ):
        if key in record and not (key == "schema_ref" and record[key] == "*"):
            reference(record[key])
    for key in (
        "database_bytes",
        "disk_free_percent",
        "rto_target_seconds",
        "duration_seconds",
    ):
        if key in record:
            value = record[key]
            if (
                type(value) not in (int, float)
                or not 0 <= value <= 2**63 - 1
                or not math.isfinite(value)
            ):
                raise ValueError("Invalid measurement")
    if kind == "storage-samples" and (
        record["disk_free_percent"] > 100 or type(record["database_bytes"]) is not int
    ):
        raise ValueError("Invalid storage measurement")
    if kind in ("base-backups", "recovery-rehearsals"):
        for key in ("wal_start", "wal_end"):
            if record[key] is not None and not re.fullmatch(
                r"[0-9A-F]{1,8}/[0-9A-F]{1,8}", record[key]
            ):
                raise ValueError("Invalid WAL position")
    if kind == "base-backups":
        stamp(record["completed_at"])
        if type(record["verified"]) is not bool:
            raise ValueError("Invalid verification")
    if kind == "recovery-rehearsals":
        elapsed = (
            stamp(record["ended_at"]) - stamp(record["started_at"])
        ).total_seconds()
        if elapsed < 0 or abs(elapsed - record["duration_seconds"]) > 1:
            raise ValueError("Inconsistent duration")
        if record["outcome"] not in ("success", "failure") or record[
            "data_integrity"
        ] not in ("passed", "failed", "not_verified"):
            raise ValueError("Invalid outcome")
        record = dict(
            record,
            rto_met=record["outcome"] == "success"
            and record["duration_seconds"] <= record["rto_target_seconds"],
            live_failover_authorized=False,
        )
    path = catalog.location()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
        conn.executescript(catalog.SCHEMA)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS readiness (kind TEXT, id TEXT, created_at TEXT, payload TEXT, PRIMARY KEY(kind,id))"
        )
        conn.execute("BEGIN IMMEDIATE")
        if kind == "ownership":
            rows = conn.execute(
                "SELECT payload FROM readiness WHERE kind='ownership'"
            ).fetchall()
            for row in rows:
                previous = json.loads(row[0])
                if previous["database_ref"] == record["database_ref"] and (
                    previous["schema_ref"] == record["schema_ref"]
                    or "*" in (previous["schema_ref"], record["schema_ref"])
                ):
                    raise ValueError("Duplicate or conflicting ownership claim")
        conn.execute(
            "INSERT INTO readiness VALUES (?, ?, ?, ?)",
            (kind, record["id"], record["created_at"], json.dumps(record)),
        )
    path.chmod(0o600)


def read(kind, identifier=None, *, tenant_id=None, database_ref=None):
    if kind not in KINDS:
        raise ValueError("Unknown metadata kind")
    scoped = tenant_id is not None or database_ref is not None
    if scoped:
        binding(tenant_id, database_ref)
    where = "kind=?"
    parameters = [kind]
    if scoped:
        where += " AND json_extract(payload, '$.tenant_id')=? AND json_extract(payload, '$.database_ref')=?"
        parameters += [tenant_id, database_ref]
    with closing(
        sqlite3.connect(
            catalog.location().resolve().as_uri() + "?mode=ro", uri=True, timeout=2
        )
    ) as conn:
        if identifier:
            row = conn.execute(
                f"SELECT payload FROM readiness WHERE {where} AND id=?",
                (*parameters, identifier),
            ).fetchone()
            return json.loads(row[0]) if row else None
        rows = conn.execute(
            f"SELECT payload FROM readiness WHERE {where} ORDER BY CASE WHEN kind='base-backups' THEN json_extract(payload, '$.completed_at') WHEN kind='recovery-rehearsals' THEN json_extract(payload, '$.ended_at') ELSE created_at END DESC, id DESC LIMIT 101",
            parameters,
        ).fetchall()
        return {
            "items": [json.loads(row[0]) for row in rows[:100]],
            "truncated": len(rows) > 100,
        }


def evidence(kind, *, tenant_id=None, database_ref=None):
    result = read(kind, tenant_id=tenant_id, database_ref=database_ref)
    result["source"] = "operator_attested"
    result["live_failover_authorized"] = False
    return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    try:
        register(args.kind, json.loads(args.record.read_text()))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        parser.exit(
            1, "Metadata rejected; check schema, duplicate id or ownership conflict.\n"
        )


if __name__ == "__main__":
    main()

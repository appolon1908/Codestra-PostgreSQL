"""Read-only operational API. No application data or SQL accepted over HTTP."""

import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts import catalog, readiness

STARTED = time.monotonic()
QUERY = "SELECT current_setting('server_version_num')::int, pg_is_in_recovery(), current_setting('max_connections')::int"


OPERATIONS = {
    "/v1/postgresql/wal": "SELECT codestra_ops.wal()",
    "/v1/postgresql/capacity": "SELECT codestra_ops.capacity()",
    "/v1/postgresql/maintenance": "SELECT codestra_ops.maintenance()",
    "/v1/postgresql/connections": "SELECT codestra_ops.connections()",
    "/v1/postgresql/security": "SELECT codestra_ops.security()",
    "/v1/postgresql/recovery": "SELECT codestra_ops.recovery()",
}


def query(sql):
    # Callers supply only module constants; HTTP inputs never enter SQL.
    env = os.environ.copy()
    with open(env.get("PGPASSWORD_FILE", "/run/secrets/monitor_password")) as secret:
        env["PGPASSWORD"] = secret.read().strip()
    env["PGCONNECT_TIMEOUT"] = "2"
    env["PGOPTIONS"] = "-c statement_timeout=2000 -c default_transaction_read_only=on"
    result = subprocess.run(
        ["psql", "-X", "-w", "-At", "-F", "|", "-c", sql],
        env=env,
        capture_output=True,
        text=True,
        timeout=4,
        check=True,
    )
    return result.stdout.strip()


def probe():
    try:
        version, recovery, limit = query(QUERY).split("|")
        return {
            "reachable": True,
            "version_num": int(version),
            "in_recovery": recovery == "t",
            "max_connections": int(limit),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"reachable": False}


def metadata_status(kind):
    try:
        return dict(readiness.evidence(kind), available=True)
    except (OSError, ValueError, sqlite3.Error):
        return {
            "available": False,
            "items": [],
            "truncated": False,
            "source": "operator_attested",
            "live_failover_authorized": False,
        }


def age_seconds(value):
    return max(0, (datetime.now(timezone.utc) - readiness.stamp(value)).total_seconds())


def operational_status(path):
    body = json.loads(query(OPERATIONS[path]))
    if path.endswith("/wal"):
        evidence = metadata_status("base-backups")
        body["base_backup_evidence"] = evidence
        body["last_archived_age_seconds"] = (
            age_seconds(body["last_archived_at"])
            if body.get("last_archived_at")
            else None
        )
        body["base_backup_age_seconds"] = min(
            (age_seconds(row["completed_at"]) for row in evidence["items"]),
            default=None,
        )
        # pg_dump catalogs are deliberately excluded: logical backups cannot seed PITR.
        body["recoverability"] = "unproven"
        body["recovery_proven"] = False
    if path.endswith("/capacity"):
        evidence = metadata_status("storage-samples")
        body["growth_samples"] = evidence
        latest = evidence["items"][0] if evidence["items"] else None
        body["disk_free_percent"] = latest["disk_free_percent"] if latest else None
        body["sample_age_seconds"] = (
            age_seconds(latest["created_at"]) if latest else None
        )
    return body


def readiness_metrics():
    output = ""
    fields = {
        "wal": (
            "archive_mode_enabled",
            "archive_transport_configured",
            "archived_count",
            "failed_count",
            "last_archived_age_seconds",
            "archive_failure_pending",
            "base_backup_age_seconds",
        ),
        "capacity": (
            "database_bytes",
            "table_count",
            "index_count",
            "disk_free_percent",
            "sample_age_seconds",
        ),
        "maintenance": (
            "autovacuum_enabled",
            "dead_tuple_estimate",
            "modified_since_analyze_estimate",
            "vacuum_backlog_estimate",
            "analyze_backlog_estimate",
            "never_vacuumed_tables",
            "never_analyzed_tables",
            "oldest_database_xid_age",
            "freeze_max_age",
            "checkpoints_timed",
            "checkpoints_requested",
        ),
    }
    for kind, keys in fields.items():
        try:
            values = operational_status("/v1/postgresql/" + kind)
            available = True
        except (
            OSError,
            ValueError,
            KeyError,
            sqlite3.Error,
            subprocess.SubprocessError,
        ):
            values, available = {}, False
        values["available"] = available
        for key in (*keys, "available"):
            value = values.get(key)
            if value is not None:
                name = f"codestra_postgresql_{kind}_{key}"
                output += f"# HELP {name} Operational aggregate; see runbook for scope and thresholds.\n# TYPE {name} gauge\n{name} {float(value)}\n"
    for kind in ("ownership", "recovery-rehearsals"):
        evidence = metadata_status(kind)
        name = "codestra_postgresql_" + kind.replace("-", "_") + "_available"
        output += f"# HELP {name} Operator evidence readable.\n# TYPE {name} gauge\n{name} {int(evidence['available'])}\n"
        if kind == "recovery-rehearsals" and evidence["items"]:
            latest = evidence["items"][0]
            measurements = {
                "age_seconds": age_seconds(latest["ended_at"]),
                "duration_seconds": latest["duration_seconds"],
                "rto_met": latest["rto_met"],
                "success": latest["outcome"] == "success",
                "data_integrity_passed": latest["data_integrity"] == "passed",
            }
            for key, value in measurements.items():
                metric = "codestra_postgresql_recovery_rehearsals_latest_" + key
                output += f"# HELP {metric} Latest operator-attested rehearsal; never live failover authority.\n# TYPE {metric} gauge\n{metric} {float(value)}\n"
    return output


def response(path):
    if path in OPERATIONS:
        try:
            return 200, operational_status(path), "application/json"
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            sqlite3.Error,
            subprocess.SubprocessError,
        ):
            return 503, {"error": "unavailable"}, "application/json"
    metadata = re.fullmatch(
        r"/v1/(ownership|storage-samples|base-backups|recovery-rehearsals)(?:/([a-zA-Z0-9_-]{1,80}))?",
        path,
    )
    if metadata:
        try:
            body = readiness.read(metadata[1], metadata[2])
            return (
                (200, body, "application/json")
                if body is not None
                else (404, {"error": "not_found"}, "application/json")
            )
        except (OSError, ValueError, sqlite3.Error):
            return 503, {"error": "unavailable"}, "application/json"
    match = re.fullmatch(r"/v1/(backups|rehearsals)(?:/([a-zA-Z0-9_-]{1,80}))?", path)
    if match:
        try:
            body = catalog.read(match[1], match[2])
            if body is None:
                return 404, {"error": "not_found"}, "application/json"
            return 200, body, "application/json"
        except (OSError, ValueError, sqlite3.Error):
            return 503, {"error": "unavailable"}, "application/json"
    if path == "/healthz":
        return 200, {"status": "ok"}, "application/json"
    if path == "/v1/capabilities":
        return (
            200,
            {
                "service": "codestra-postgresql",
                "api_version": "1",
                "capabilities": [
                    "runtime.status",
                    "runtime.health",
                    "runtime.metrics",
                    "backups.catalog",
                    "backups.rehearsals",
                    "runtime.connections",
                    "runtime.security",
                    "runtime.recovery",
                    "runtime.wal",
                    "runtime.capacity",
                    "runtime.maintenance",
                    "operations.ownership",
                    "operations.storage_samples",
                    "operations.base_backups",
                    "operations.recovery_rehearsals",
                ],
                "mutations": False,
                "integration_authority": "Middleware V3",
            },
            "application/json",
        )
    if path in ("/readyz", "/health/ready", "/v1/postgresql/status", "/metrics"):
        database = probe()
        ready = database["reachable"] and 170000 <= database["version_num"] < 180000
        if path == "/metrics":
            body = (
                "# HELP codestra_postgresql_ready PostgreSQL 17 is reachable.\n"
                "# TYPE codestra_postgresql_ready gauge\n"
                f"codestra_postgresql_ready {int(ready)}\n"
                "# HELP codestra_postgresql_management_uptime_seconds Management process uptime.\n"
                "# TYPE codestra_postgresql_management_uptime_seconds gauge\n"
                f"codestra_postgresql_management_uptime_seconds {time.monotonic() - STARTED:.3f}\n"
            )
            try:
                counts = catalog.counts()
                available = True
            except (OSError, ValueError, sqlite3.Error):
                counts = {}
                available = False
            body += (
                "# HELP codestra_postgresql_catalog_available Operational catalog readable.\n"
                "# TYPE codestra_postgresql_catalog_available gauge\n"
                f"codestra_postgresql_catalog_available {int(available)}\n"
            )
            for kind, count in counts.items():
                body += (
                    f"# HELP codestra_postgresql_catalog_{kind} Stored metadata records including tombstones.\n"
                    f"# TYPE codestra_postgresql_catalog_{kind} gauge\n"
                    f"codestra_postgresql_catalog_{kind} {count}\n"
                )
            return (
                200,
                body + readiness_metrics(),
                "text/plain; version=0.0.4; charset=utf-8",
            )
        return (
            (200 if ready else 503),
            {"status": "ready" if ready else "unavailable", "database": database},
            "application/json",
        )
    return 404, {"error": "not_found"}, "application/json"


class Handler(BaseHTTPRequestHandler):
    server_version = "CodestraPostgreSQL/1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self):
        status, body, content_type = response(self.path)
        encoded = (json.dumps(body) if isinstance(body, dict) else body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        # Do not log raw URLs, headers, database errors, or credentials.
        print(
            json.dumps(
                {
                    "event": "http_request",
                    "status": str(args[1]) if len(args) > 1 else "error",
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()

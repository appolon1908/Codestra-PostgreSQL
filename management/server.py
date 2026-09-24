"""Read-only operational API. No application data or SQL accepted over HTTP."""

import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STARTED = time.monotonic()
QUERY = "SELECT current_setting('server_version_num')::int, pg_is_in_recovery(), current_setting('max_connections')::int"


def probe():
    try:
        env = os.environ.copy()
        with open(
            env.get("PGPASSWORD_FILE", "/run/secrets/monitor_password")
        ) as secret:
            env["PGPASSWORD"] = secret.read().strip()
        env["PGCONNECT_TIMEOUT"] = "2"
        env["PGOPTIONS"] = (
            "-c statement_timeout=2000 -c default_transaction_read_only=on"
        )
        result = subprocess.run(
            ["psql", "-X", "-w", "-At", "-F", "|", "-c", QUERY],
            env=env,
            capture_output=True,
            text=True,
            timeout=4,
            check=True,
        )
        version, recovery, limit = result.stdout.strip().split("|")
        return {
            "reachable": True,
            "version_num": int(version),
            "in_recovery": recovery == "t",
            "max_connections": int(limit),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"reachable": False}


def response(path):
    if path == "/healthz":
        return 200, {"status": "ok"}, "application/json"
    if path == "/v1/capabilities":
        return (
            200,
            {
                "service": "codestra-postgresql",
                "api_version": "1",
                "capabilities": ["runtime.status", "runtime.health", "runtime.metrics"],
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
            return 200, body, "text/plain; version=0.0.4; charset=utf-8"
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

#!/usr/bin/env python3
"""Disposable Compose project: startup, API, policy, exporter, backup and restore."""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "codestra-pg-test-" + uuid.uuid4().hex[:10]
ENV = dict(os.environ, MANAGEMENT_PORT="0")
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "compose.yaml")]
if os.environ.get("TEST_COMPOSE_OVERRIDE"):
    COMPOSE += ["-f", os.environ["TEST_COMPOSE_OVERRIDE"]]


def compose(*args, ok=True):
    result = subprocess.run(
        COMPOSE + list(args),
        env=ENV,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if ok and result.returncode:
        raise AssertionError(
            f"Compose {args[0]} failed:\n{result.stdout}\n{result.stderr}"
        )
    return result


def sql(statement, db="fixture"):
    return compose(
        "exec",
        "-T",
        "-u",
        "postgres",
        "postgres",
        "psql",
        "-X",
        "-w",
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-d",
        db,
        "-c",
        statement,
    ).stdout.strip()


def tool(*args, ok=True):
    return compose(
        "run", "--rm", "-T", "--no-deps", "tools", "scripts/backup.py", *args, ok=ok
    )


def get(path):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            base + path, timeout=8
        ) as result:
            return result.status, result.read().decode()
    except urllib.error.HTTPError as result:
        return result.code, result.read().decode()


try:
    compose("--profile", "tools", "build")
    compose("up", "-d", "--wait", "--wait-timeout", "90")
    base = "http://" + compose("port", "management", "8080").stdout.strip()
    for path in (
        "/healthz",
        "/readyz",
        "/v1/capabilities",
        "/v1/postgresql/status",
        "/metrics",
        "/v1/postgresql/connections",
        "/v1/postgresql/security",
        "/v1/postgresql/recovery",
    ):
        assert get(path)[0] == 200, path
    assert get("/v1/postgresql/query")[0] == 404
    assert (
        json.loads(get("/v1/postgresql/status")[1])["database"]["version_num"] // 10000
        == 17
    )
    connections = json.loads(get("/v1/postgresql/connections")[1])
    assert connections["max_connections"] == 100
    assert connections["reserved_budget"] == 5
    assert any(
        row["class"] == "monitoring" and row["active"] >= 1
        for row in connections["by_role_class"]
    )
    security = json.loads(get("/v1/postgresql/security")[1])
    assert security["ssl_enabled"] is False
    assert security["pg_hba_rules_valid"] is True
    assert len(security["pg_hba_policy_fingerprint"]) == 64
    sql("CREATE ROLE policy_fixture NOLOGIN", "postgres")
    assert (
        sql(
            "SELECT has_function_privilege('policy_fixture', 'codestra_ops.security()', 'EXECUTE')",
            "postgres",
        )
        == "f"
    )
    assert (
        sql(
            "SELECT has_function_privilege('codestra_monitor', 'codestra_ops.security()', 'EXECUTE')",
            "postgres",
        )
        == "t"
    )
    changed = json.loads(get("/v1/postgresql/security")[1])
    assert changed["role_policy_fingerprint"] != security["role_policy_fingerprint"]
    assert changed["pg_hba_policy_fingerprint"] == security["pg_hba_policy_fingerprint"]
    recovery = json.loads(get("/v1/postgresql/recovery")[1])
    assert recovery["role"] == "primary" and recovery["recovery_proven"] is False
    assert recovery["replication_lag_seconds"] is None
    assert get("/v1/backups")[0] == 503  # no catalog yet
    sql("CREATE DATABASE fixture", "postgres")
    sql(
        "CREATE TABLE sample (id int PRIMARY KEY, value text); INSERT INTO sample VALUES (1, 'restored-value')"
    )
    denied = compose(
        "exec",
        "-T",
        "management",
        "python3",
        "-c",
        "import os, subprocess; os.environ['PGPASSWORD']=open('/run/secrets/monitor_password').read().strip(); "
        "raise SystemExit(subprocess.run(['psql','-X','-w','-d','fixture','-c','SELECT 1'],capture_output=True).returncode)",
        ok=False,
    )
    assert denied.returncode != 0, "monitor must not connect to application databases"
    for _ in range(15):
        exporter = compose(
            "exec",
            "-T",
            "management",
            "python3",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://exporter:9187/metrics').read().decode())",
            ok=False,
        )
        if exporter.returncode == 0 and "pg_up 1" in exporter.stdout:
            break
        time.sleep(1)
    else:
        raise AssertionError("exporter pg_up not healthy")
    archive = tool(
        "backup",
        "--database",
        "fixture",
        "--directory",
        "/backups",
        "--allow-plaintext-dev",
    ).stdout.strip()
    assert archive.startswith("/backups/")
    backup_id = Path(archive).name
    row = json.loads(get("/v1/backups/" + backup_id)[1])
    assert row["database_ref"] == "fixture" and row["retention_status"] == "retained"
    assert json.loads(get("/v1/backups")[1])["items"][0] == row
    tool("verify", archive)
    tool("restore", archive, "--target", "rehearsal")
    assert sql("SELECT value FROM sample WHERE id=1", "rehearsal") == "restored-value"
    report = json.loads(get("/v1/rehearsals")[1])["items"][0]
    assert report["verification_status"] == "passed" and report["digest_match"] is True
    assert get("/v1/rehearsals/" + report["report_id"])[0] == 200
    assert tool("restore", archive, "--target", "rehearsal", ok=False).returncode != 0
    assert tool("restore", archive, "--target", "fixture", ok=False).returncode != 0
    # Exercise real authenticated encryption with throwaway age identities.
    compose(
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "tools",
        "-c",
        "import subprocess; from pathlib import Path; "
        "subprocess.run(['age-keygen','-o','/backups/test-identity'],check=True,capture_output=True); "
        "r=subprocess.run(['age-keygen','-y','/backups/test-identity'],check=True,capture_output=True); "
        "Path('/backups/test-recipients').write_bytes(r.stdout)",
    )
    encryption_env = [
        "-e",
        "BACKUP_ENCRYPT_HOOK=/app/scripts/encrypt-age.sh",
        "-e",
        "BACKUP_DECRYPT_HOOK=/app/scripts/decrypt-age.sh",
        "-e",
        "OPENBAO_BACKUP_RECIPIENTS_FILE=/backups/test-recipients",
        "-e",
        "OPENBAO_BACKUP_IDENTITY_FILE=/backups/test-identity",
    ]
    encrypted = compose(
        "run",
        "--rm",
        "-T",
        "--no-deps",
        *encryption_env,
        "tools",
        "scripts/backup.py",
        "backup",
        "--database",
        "fixture",
        "--directory",
        "/backups",
    ).stdout.strip()
    compose(
        "run",
        "--rm",
        "-T",
        "--no-deps",
        *encryption_env,
        "tools",
        "scripts/backup.py",
        "restore",
        encrypted,
        "--target",
        "encrypted_rehearsal",
    )
    assert (
        sql("SELECT value FROM sample WHERE id=1", "encrypted_rehearsal")
        == "restored-value"
    )
    compose(
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "tools",
        "-c",
        f"from pathlib import Path; p=Path({archive!r})/'backup.dump'; p.write_bytes(p.read_bytes()+b'corrupt')",
    )
    assert tool("verify", archive, ok=False).returncode != 0
    assert (
        tool("restore", archive, "--target", "corrupt_restore", ok=False).returncode
        != 0
    )
    assert (
        sql(
            "SELECT count(*) FROM pg_database WHERE datname='corrupt_restore'",
            "postgres",
        )
        == "0"
    )
    compose("stop", "postgres")
    assert get("/healthz")[0] == 200
    assert get("/v1/backups")[0] == 200  # independent of business DB availability
    for path in ("connections", "security", "recovery"):
        assert get("/v1/postgresql/" + path)[0] == 503
    assert get("/readyz")[0] == 503
    assert get("/v1/postgresql/status")[0] == 503
    assert "codestra_postgresql_ready 0" in get("/metrics")[1]
    compose("start", "postgres")
    for _ in range(20):
        if get("/readyz")[0] == 200:
            break
        time.sleep(1)
    else:
        raise AssertionError("readiness did not recover")
    print(
        "PASS: startup, operational APIs, catalog/reports, policy fingerprints, least privilege, version, app isolation, exporter, backup verification, encrypted restore rehearsal, overwrite refusal, corruption refusal, outage/recovery"
    )
finally:
    # Only this run's randomly named project/volumes are removed.
    compose("down", "-v", "--remove-orphans", ok=False)

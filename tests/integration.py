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
    ):
        assert get(path)[0] == 200, path
    assert get("/v1/postgresql/query")[0] == 404
    assert (
        json.loads(get("/v1/postgresql/status")[1])["database"]["version_num"] // 10000
        == 17
    )
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
    tool("verify", archive)
    tool("restore", archive, "--target", "rehearsal")
    assert sql("SELECT value FROM sample WHERE id=1", "rehearsal") == "restored-value"
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
        "PASS: startup, API, version, app isolation, exporter, backup verification, encrypted restore rehearsal, overwrite refusal, corruption refusal, outage/recovery"
    )
finally:
    # Only this run's randomly named project/volumes are removed.
    compose("down", "-v", "--remove-orphans", ok=False)

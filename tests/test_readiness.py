import copy
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from jsonschema import Draft202012Validator

from management import server
from scripts import readiness


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERATIONS_CATALOG", str(tmp_path / "operations.sqlite3"))
    return tmp_path


def record(kind, identifier="record_1"):
    common = {"id": identifier, "created_at": "2026-01-01T00:00:00+00:00"}
    return (
        common
        | {
            "ownership": {
                "database_ref": "app",
                "schema_ref": "public",
                "owning_repository": "org/app",
                "service_id": "app",
            },
            "storage-samples": {
                "database_bytes": 1024,
                "disk_free_percent": 30,
                "scope_ref": "cluster/pgdata",
            },
            "base-backups": {
                "backup_ref": "physical_1",
                "completed_at": "2026-01-01T00:00:00+00:00",
                "verified": True,
                "wal_start": "0/100",
                "wal_end": "0/200",
            },
            "recovery-rehearsals": {
                "scenario": "isolated_pitr",
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T00:00:20+00:00",
                "rto_target_seconds": 30,
                "duration_seconds": 20,
                "data_integrity": "passed",
                "wal_start": "0/100",
                "wal_end": "0/200",
                "outcome": "success",
            },
        }[kind]
    )


def validate(path, body):
    schema = yaml.safe_load(Path("openapi.yaml").read_text())["paths"][path]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    Draft202012Validator(schema).validate(body)


@pytest.mark.parametrize("kind", readiness.KINDS)
def test_persistent_read_only_api_contract(store, kind):
    row = record(kind)
    original = copy.deepcopy(row)
    readiness.register(kind, row)
    assert row == original
    path = "/v1/" + kind
    code, body, _ = server.response(path)
    assert code == 200
    validate(path, body)
    validate(path + "/{id}", server.response(path + "/record_1")[1])
    assert server.response(path + "/absent")[0] == 404
    assert server.response(path + "?sql=VACUUM")[0] == 404
    with pytest.raises(sqlite3.IntegrityError):
        if kind == "ownership":
            row["schema_ref"] = "other"
        readiness.register(kind, row)


def test_ownership_conflict_duplicate_and_wildcard(store):
    row = record("ownership")
    readiness.register("ownership", row)
    for fields in ({}, {"service_id": "other"}, {"schema_ref": "*"}):
        with pytest.raises(ValueError, match="ownership claim"):
            readiness.register("ownership", row | {"id": "second"} | fields)
    readiness.register("ownership", row | {"id": "separate", "schema_ref": "different"})
    readiness.register("ownership", row | {"id": "other_db", "database_ref": "other"})
    assert len(readiness.read("ownership")["items"]) == 3


def test_concurrent_conflicting_claims(store):
    def attempt(index):
        try:
            readiness.register("ownership", record("ownership", f"claim_{index}"))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("ownership", {"business_row": "secret"}),
        ("ownership", {"service_id": "bad\nname"}),
        ("storage-samples", {"database_bytes": -1}),
        ("storage-samples", {"database_bytes": 10**500}),
        ("storage-samples", {"disk_free_percent": 101}),
        ("storage-samples", {"disk_free_percent": float("nan")}),
        ("base-backups", {"verified": "true"}),
        ("base-backups", {"wal_start": "SELECT secret"}),
        ("base-backups", {"completed_at": "2026-01-01"}),
        ("recovery-rehearsals", {"ended_at": "2025-01-01T00:00:00+00:00"}),
        ("recovery-rehearsals", {"duration_seconds": 22}),
        ("recovery-rehearsals", {"outcome": "authorized"}),
        ("recovery-rehearsals", {"live_failover_authorized": True}),
    ],
)
def test_invalid_metadata_rejected_before_store_creation(store, kind, fields):
    with pytest.raises(ValueError):
        readiness.register(kind, record(kind) | fields)
    assert not (store / "operations.sqlite3").exists()


def test_future_timestamp_rejected(store):
    with pytest.raises(ValueError):
        readiness.register(
            "ownership",
            record("ownership")
            | {
                "created_at": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat()
            },
        )


def test_rehearsal_success_failure_and_rto(store):
    for index, (outcome, duration, expected) in enumerate(
        [("success", 20, True), ("success", 40, False), ("failure", 20, False)]
    ):
        row = record("recovery-rehearsals", str(index)) | {
            "duration_seconds": duration,
            "ended_at": f"2026-01-01T00:00:{duration}+00:00",
            "outcome": outcome,
            "data_integrity": "not_verified",
        }
        readiness.register("recovery-rehearsals", row)
        report = readiness.read("recovery-rehearsals", str(index))
        assert report["rto_met"] is expected
        assert report["live_failover_authorized"] is False


def test_missing_evidence_is_unknown_and_reads_do_not_create(store):
    assert server.response("/v1/base-backups")[0] == 503
    with patch.object(server, "query", return_value="{}"):
        wal = server.response("/v1/postgresql/wal")[1]
        capacity = server.response("/v1/postgresql/capacity")[1]
    assert wal["base_backup_age_seconds"] is None
    assert wal["recovery_proven"] is False
    assert wal["base_backup_evidence"]["available"] is False
    assert capacity["disk_free_percent"] is None
    assert not (store / "operations.sqlite3").exists()


def test_evidence_age_ordering_bounded_and_metrics(store):
    readiness.register("base-backups", record("base-backups"))
    readiness.register("storage-samples", record("storage-samples"))
    with patch.object(server, "query", return_value="{}"):
        wal = server.response("/v1/postgresql/wal")[1]
        capacity = server.response("/v1/postgresql/capacity")[1]
        metrics = server.readiness_metrics()
    assert wal["base_backup_age_seconds"] > 0
    assert wal["recoverability"] == "unproven"
    assert capacity["disk_free_percent"] == 30
    assert capacity["sample_age_seconds"] > 0
    assert "codestra_postgresql_capacity_disk_free_percent 30.0" in metrics
    assert "cluster/pgdata" not in metrics
    for index in range(101):
        readiness.register(
            "storage-samples", record("storage-samples", f"sample_{index:03}")
        )
    samples = readiness.read("storage-samples")
    assert samples["truncated"] and len(samples["items"]) == 100


def test_operational_outage_redacted_and_metrics_omit_values(store):
    with patch.object(server, "query", side_effect=ValueError("secret")):
        for name in ("wal", "capacity", "maintenance"):
            assert server.response("/v1/postgresql/" + name)[:2] == (
                503,
                {"error": "unavailable"},
            )
        metrics = server.readiness_metrics()
    assert "secret" not in metrics
    assert "codestra_postgresql_capacity_available 0.0" in metrics
    assert "codestra_postgresql_capacity_database_bytes" not in metrics


def test_no_mutation_paths():
    for action in ("vacuum", "restore", "failover", "promote", "query"):
        assert server.response("/v1/postgresql/" + action)[0] == 404


def test_base_backup_age_uses_completion_not_registration_order(store):
    readiness.register(
        "base-backups",
        record("base-backups", "later_import")
        | {
            "created_at": "2026-06-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:00+00:00",
        },
    )
    readiness.register(
        "base-backups",
        record("base-backups", "newer_backup")
        | {
            "created_at": "2026-05-01T00:00:00+00:00",
            "completed_at": "2026-04-30T20:00:00-04:00",
        },
    )
    evidence = readiness.read("base-backups")["items"]
    assert evidence[0]["id"] == "newer_backup"
    assert evidence[0]["completed_at"] == "2026-05-01T00:00:00+00:00"


def test_rehearsal_metric_age_uses_end_time(store):
    row = record("recovery-rehearsals") | {"created_at": "2026-06-01T00:00:00+00:00"}
    readiness.register("recovery-rehearsals", row)
    with patch.object(server, "query", side_effect=ValueError):
        metrics = server.readiness_metrics()
    age = float(
        next(
            line.split()[1]
            for line in metrics.splitlines()
            if line.startswith(
                "codestra_postgresql_recovery_rehearsals_latest_age_seconds "
            )
        )
    )
    assert abs(age - server.age_seconds(row["ended_at"])) < 1
    assert "codestra_postgresql_recovery_rehearsals_latest_rto_met 1.0" in metrics


def test_all_operational_metrics_use_prometheus_text_names(store):
    with patch.object(server, "query", return_value="{}"):
        metrics = server.readiness_metrics()
    for line in metrics.splitlines():
        if line and not line.startswith("#"):
            assert re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", line.split()[0])
    assert "codestra_postgresql_pool_isolation_available 1.0" in metrics

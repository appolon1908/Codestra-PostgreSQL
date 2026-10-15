from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from jsonschema import Draft202012Validator

from management import server
from scripts import readiness

TENANT = "tenant-a"
DATABASE = "middleware_test"


@lru_cache(maxsize=1)
def api_contract():
    return yaml.safe_load(Path("openapi.yaml").read_text())


def validate_operational_record(kind, identifier):
    contract = api_contract()
    schema = contract["paths"][f"/v1/{kind}/{{id}}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    Draft202012Validator(schema).validate(readiness.read(kind, identifier))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERATIONS_CATALOG", str(tmp_path / "operations.sqlite3"))
    return tmp_path


def backup_record(identifier="backup_001", **extra):
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        "id": identifier,
        "created_at": stamp,
        "completed_at": stamp,
        "backup_ref": "physical_001",
        "verified": True,
        "wal_start": "0/100",
        "wal_end": "0/200",
    } | extra


def rehearsal_record(identifier="rehearsal_001", **extra):
    ended = datetime.now(timezone.utc)
    return {
        "id": identifier,
        "created_at": ended.isoformat(),
        "started_at": (ended - timedelta(seconds=20)).isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": 20,
        "rto_target_seconds": 30,
        "scenario": "isolated_pitr",
        "outcome": "success",
        "data_integrity": "passed",
        "wal_start": "0/100",
        "wal_end": "0/200",
    } | extra


def test_legacy_unscoped_evidence_is_never_relabelled(store):
    readiness.register("base-backups", backup_record())
    readiness.register("recovery-rehearsals", rehearsal_record())
    for name in ("backup-catalog", "restore-rehearsal", "replication-recovery"):
        body = projection(name)
        assert body["status"] == "unknown"
        assert body["evidence_refs"] == []


def test_registered_backup_proof_is_scoped_before_latest_selection(store):
    readiness.register(
        "base-backups",
        backup_record(
            tenant_id=TENANT,
            database_ref=DATABASE,
            encrypted=True,
            offsite_verified=True,
        ),
    )
    for index in range(101):
        readiness.register(
            "base-backups",
            backup_record(
                f"other_{index}",
                tenant_id="tenant-other",
                database_ref=DATABASE,
                encrypted=True,
                offsite_verified=True,
            ),
        )
    body = projection("backup-catalog")
    validate_operational_record("base-backups", "backup_001")
    assert body["status"] == "ready"
    assert body["evidence_refs"] == ["backup_001"]
    assert (
        body["observed_at"]
        == readiness.read("base-backups", "backup_001")["completed_at"]
    )
    for tenant, database in (("other", DATABASE), (TENANT, "other")):
        result = server.response(
            f"/internal/v1/observability/backup-catalog/{database}",
            tenant_id=tenant,
        )[1]
        assert result["status"] == "unknown"
        assert result["evidence_refs"] == []


def test_registered_rehearsal_requires_integrity_and_bound_application_proof(store):
    readiness.register(
        "recovery-rehearsals",
        rehearsal_record(
            tenant_id=TENANT,
            database_ref=DATABASE,
            application_verified=True,
            data_integrity="failed",
            rpo_met=True,
        ),
    )
    body = projection("restore-rehearsal")
    validate_operational_record("recovery-rehearsals", "rehearsal_001")
    assert body["status"] == "not_ready"
    assert body["checks"]["restore_verified"] is False
    recovery = projection("replication-recovery")
    assert recovery["checks"]["recovery_tested"] is False
    assert recovery["checks"]["replication_healthy"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"tenant_id": TENANT},
        {"tenant_id": TENANT, "database_ref": DATABASE, "encrypted": "true"},
        {"encrypted": True},
        {"tenant_id": "bad tenant", "database_ref": DATABASE},
    ],
)
def test_invalid_projection_attestations_are_rejected_before_store(store, extra):
    with pytest.raises(ValueError):
        readiness.register("base-backups", backup_record(**extra))
    assert not (store / "operations.sqlite3").exists()


def test_stale_scoped_proof_does_not_become_fresh_when_read(store):
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    readiness.register(
        "base-backups",
        backup_record(
            tenant_id=TENANT,
            database_ref=DATABASE,
            encrypted=True,
            offsite_verified=True,
            created_at=old,
            completed_at=old,
        ),
    )
    body = projection("backup-catalog")
    assert body["status"] == "unknown"
    assert body["evidence_refs"] == []


def test_unscoped_live_security_is_not_attested_for_requested_database():
    with patch.object(
        server,
        "operational_status",
        return_value={
            "ssl_enabled": True,
            "minimum_tls_version": "TLSv1.3",
            "certificate_valid": True,
            "least_privilege_verified": True,
            "role_policy_fingerprint": "a" * 64,
        },
    ):
        body = projection("tls-security")
    assert body["status"] == "unknown"
    assert body["evidence_refs"] == []
    assert all(value is None for value in body["checks"].values())


def projection(name: str):
    status, body, content_type = server.response(
        f"/internal/v1/observability/{name}/{DATABASE}",
        tenant_id=TENANT,
    )
    assert status == 200
    assert content_type == "application/json"
    assert body["tenant_id"] == TENANT
    assert body["database_ref"] == DATABASE
    assert body["status"] in {"ready", "not_ready", "unknown"}
    assert isinstance(body["evidence_refs"], list)
    assert body["observed_at"].endswith("+00:00")
    contract = api_contract()
    schema = contract["paths"][f"/internal/v1/observability/{name}/{{database_ref}}"][
        "get"
    ]["responses"]["200"]["content"]["application/json"]["schema"]
    Draft202012Validator(contract | schema).validate(body)
    return body


@pytest.mark.parametrize(
    ("name", "checks"),
    [
        ("backup-catalog", {"catalog_available", "encrypted", "offsite_verified"}),
        (
            "restore-rehearsal",
            {"isolated_target", "restore_verified", "application_verified"},
        ),
        (
            "tls-security",
            {"tls_required", "certificate_valid", "least_privilege_verified"},
        ),
        (
            "replication-recovery",
            {"replication_healthy", "recovery_tested", "rpo_met", "rto_met"},
        ),
    ],
)
def test_middleware_observability_routes_are_fixed_and_closed(name, checks):
    with (
        patch.object(
            server,
            "metadata_status",
            return_value={
                "available": False,
                "items": [],
                "truncated": False,
                "source": "operator_attested",
                "live_failover_authorized": False,
            },
        ),
        patch.object(server, "operational_status", return_value={}),
    ):
        body = projection(name)
    assert set(body) == {
        "tenant_id",
        "database_ref",
        "observed_at",
        "status",
        "evidence_refs",
        "checks",
    }
    assert set(body["checks"]) == checks
    assert body["status"] == "unknown"


def test_backup_catalog_is_ready_only_with_complete_positive_evidence(store):
    readiness.register(
        "base-backups",
        backup_record(
            tenant_id=TENANT,
            database_ref=DATABASE,
            encrypted=True,
            offsite_verified=True,
        ),
    )
    body = projection("backup-catalog")
    assert body["status"] == "ready"
    assert body["checks"] == {
        "catalog_available": True,
        "encrypted": True,
        "offsite_verified": True,
    }
    assert body["evidence_refs"] == ["backup_001"]


def test_restore_rehearsal_distinguishes_integrity_from_application_verification(store):
    readiness.register(
        "recovery-rehearsals",
        rehearsal_record(
            tenant_id=TENANT,
            database_ref=DATABASE,
            application_verified=False,
        ),
    )
    body = projection("restore-rehearsal")
    assert body["status"] == "not_ready"
    assert body["checks"] == {
        "isolated_target": True,
        "restore_verified": True,
        "application_verified": False,
    }


def test_tls_security_does_not_infer_certificate_or_least_privilege_proof():
    with patch.object(
        server,
        "operational_status",
        return_value={
            "ssl_enabled": True,
            "minimum_tls_version": "TLSv1.3",
            "pg_hba_rules_valid": True,
            "role_policy_fingerprint": "a" * 64,
        },
    ):
        body = projection("tls-security")
    assert body["status"] == "unknown"
    assert body["checks"] == {
        "tls_required": None,
        "certificate_valid": None,
        "least_privilege_verified": None,
    }


def test_replication_recovery_uses_rehearsal_without_claiming_unknown_rpo(store):
    readiness.register(
        "recovery-rehearsals",
        rehearsal_record(
            tenant_id=TENANT,
            database_ref=DATABASE,
        ),
    )
    body = projection("replication-recovery")
    assert body["status"] == "unknown"
    assert body["checks"] == {
        "replication_healthy": None,
        "recovery_tested": True,
        "rpo_met": None,
        "rto_met": True,
    }


@pytest.mark.parametrize(
    "path,tenant",
    [
        ("/internal/v1/observability/backup-catalog/middleware_test", None),
        ("/internal/v1/observability/backup-catalog/../secret", TENANT),
        ("/internal/v1/observability/not-a-contract/middleware_test", TENANT),
    ],
)
def test_middleware_observability_routes_fail_closed(path, tenant):
    status, body, _ = server.response(path, tenant_id=tenant)
    assert status in {400, 404}
    assert body["error"] in {"invalid_binding", "not_found"}


def test_http_handler_requires_tenant_header_for_middleware_readback(monkeypatch):
    captured = {}

    def fake_response(path, tenant_id=None):
        captured["path"] = path
        captured["tenant_id"] = tenant_id
        return 200, {"status": "ok"}, "application/json"

    monkeypatch.setattr(server, "response", fake_response)
    handler = object.__new__(server.Handler)
    handler.path = "/internal/v1/observability/backup-catalog/middleware_test"
    handler.headers = {"X-Tenant-ID": TENANT}
    handler.send_response = lambda status: captured.setdefault("status", status)
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None

    class Sink:
        def write(self, value):
            captured["body"] = value

    handler.wfile = Sink()
    handler.do_GET()
    assert captured["tenant_id"] == TENANT

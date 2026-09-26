from unittest.mock import patch

import pytest

from management import server

TENANT = "tenant-a"
DATABASE = "middleware_test"


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
        patch.object(server, "metadata_status", return_value={
            "available": False,
            "items": [],
            "truncated": False,
            "source": "operator_attested",
            "live_failover_authorized": False,
        }),
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


def test_backup_catalog_is_ready_only_with_complete_positive_evidence():
    evidence = {
        "available": True,
        "items": [
            {
                "id": "backup_001",
                "backup_ref": "physical_001",
                "completed_at": "2026-09-25T20:00:00+00:00",
                "verified": True,
                "wal_start": "0/100",
                "wal_end": "0/200",
                "encrypted": True,
                "offsite_verified": True,
            }
        ],
        "truncated": False,
        "source": "operator_attested",
        "live_failover_authorized": False,
    }
    with patch.object(server, "metadata_status", return_value=evidence):
        body = projection("backup-catalog")
    assert body["status"] == "ready"
    assert body["checks"] == {
        "catalog_available": True,
        "encrypted": True,
        "offsite_verified": True,
    }
    assert body["evidence_refs"] == ["backup_001"]


def test_restore_rehearsal_distinguishes_integrity_from_application_verification():
    evidence = {
        "available": True,
        "items": [
            {
                "id": "rehearsal_001",
                "scenario": "isolated_pitr",
                "outcome": "success",
                "data_integrity": "passed",
                "rto_met": True,
                "application_verified": False,
                "live_failover_authorized": False,
            }
        ],
        "truncated": False,
        "source": "operator_attested",
        "live_failover_authorized": False,
    }
    with patch.object(server, "metadata_status", return_value=evidence):
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
        "tls_required": True,
        "certificate_valid": None,
        "least_privilege_verified": None,
    }


def test_replication_recovery_uses_rehearsal_without_claiming_unknown_rpo():
    rehearsal = {
        "available": True,
        "items": [
            {
                "id": "rehearsal_002",
                "scenario": "isolated_pitr",
                "outcome": "success",
                "data_integrity": "passed",
                "rto_met": True,
                "live_failover_authorized": False,
            }
        ],
        "truncated": False,
        "source": "operator_attested",
        "live_failover_authorized": False,
    }
    with (
        patch.object(server, "metadata_status", return_value=rehearsal),
        patch.object(
            server,
            "operational_status",
            return_value={
                "role": "primary",
                "replica_count": 1,
                "replication_lag_seconds": 0.25,
                "replication_healthy": True,
                "recovery_proven": False,
            },
        ),
    ):
        body = projection("replication-recovery")
    assert body["status"] == "unknown"
    assert body["checks"] == {
        "replication_healthy": True,
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

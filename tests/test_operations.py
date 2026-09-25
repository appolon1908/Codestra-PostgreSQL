import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from jsonschema import Draft202012Validator

from management import server
from scripts import backup, catalog


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERATIONS_CATALOG", str(tmp_path / "catalog.sqlite3"))
    return tmp_path


def create_backup(store):
    def fake_run(args, **kwargs):
        if args[0] == "pg_dump":
            Path(args[args.index("--file") + 1]).write_bytes(b"archive")

    with patch.object(backup, "run", side_effect=fake_run):
        return backup.backup(store / "backups", "fixture", True)


def validate(path, body, code=200):
    contract = yaml.safe_load(Path("openapi.yaml").read_text())
    schema = contract["paths"][path]["get"]["responses"][str(code)]["content"][
        "application/json"
    ]["schema"]
    Draft202012Validator(schema).validate(body)


def test_catalog_persists_and_http_is_read_only(store):
    directory = create_backup(store)
    row = catalog.read("backups", directory.name)
    assert row["size"] == 7 and not row["encrypted"]
    validate("/v1/backups/{id}", row)
    validate("/v1/backups", server.response("/v1/backups")[1])
    assert server.response("/v1/backups/" + directory.name)[1] == row
    assert server.response("/v1/backups/missing")[0] == 404
    assert server.response("/v1/backups/../../secrets")[0] == 404
    assert server.response("/v1/backups?sql=DELETE")[0] == 404
    with (
        sqlite3.connect(catalog.location().as_uri() + "?mode=ro", uri=True) as conn,
        pytest.raises(sqlite3.OperationalError),
    ):
        conn.execute("DELETE FROM backups")


def test_missing_catalog_is_unavailable_without_creation(store):
    assert server.response("/v1/backups")[:2] == (503, {"error": "unavailable"})
    assert not catalog.location().exists()


def test_reports_success_failure_and_corruption(store):
    directory = create_backup(store)
    with patch.object(backup, "run"):
        backup.rehearse(directory, "isolated")
    report = catalog.read("rehearsals")["items"][0]
    assert report["verification_status"] == "passed" and report["digest_match"] is True
    validate("/v1/rehearsals/{id}", report)
    with (
        patch.object(backup, "run", side_effect=ValueError("sensitive diagnostic")),
        pytest.raises(ValueError),
    ):
        backup.rehearse(directory, "failure")
    report = catalog.read("rehearsals")["items"][0]
    assert report["verification_status"] == "failed"
    assert "sensitive" not in json.dumps(report)
    (directory / "backup.dump").write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        backup.rehearse(directory, "corrupt")
    assert catalog.read("rehearsals")["items"][0]["digest_match"] is False
    validate("/v1/rehearsals", server.response("/v1/rehearsals")[1])


def test_retention_preserves_tombstone(store):
    directory = create_backup(store)
    old = directory.parent / ("20000101T000000Z-" + "a" * 32)
    directory.rename(old)
    row = catalog.read("backups", directory.name)
    row["backup_id"] = old.name
    catalog.write("backups", row)
    with patch.object(backup, "run"):
        backup.retain(directory.parent, 1)
    assert not old.exists()
    assert catalog.read("backups", old.name)["retention_status"] == "deleted"


def test_fixed_queries_and_redacted_failures():
    for path, sql in server.OPERATIONS.items():
        with patch.object(server, "query", return_value="{}") as query:
            assert server.response(path)[0] == 200
            query.assert_called_once_with(sql)
        with patch.object(server, "query", side_effect=ValueError("password=secret")):
            assert server.response(path)[:2] == (503, {"error": "unavailable"})
        assert server.response(path + "?sql=select")[0] == 404


def test_catalog_list_bounded_and_metrics(store):
    for index in range(102):
        catalog.write(
            "backups",
            {
                "backup_id": f"backup_{index:03}",
                "created_at": "2026-09-24T00:00:00+00:00",
            },
        )
    result = catalog.read("backups")
    assert len(result["items"]) == 100 and result["truncated"] is True
    assert result["items"][0]["backup_id"] == "backup_101"
    assert catalog.read("backups", "backup_000") is not None
    with patch.object(server, "probe", return_value={"reachable": False}):
        metrics = server.response("/metrics")[1]
    assert "codestra_postgresql_catalog_available 1" in metrics
    assert "codestra_postgresql_catalog_backups 102" in metrics
    assert "backup_101" not in metrics


def test_http_rejects_mutations(store):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            connection = http.client.HTTPConnection(*httpd.server_address, timeout=2)
            connection.request(method, "/v1/backups", body="SELECT secret")
            assert connection.getresponse().status == 501
            connection.close()
        assert not catalog.location().exists()
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_catalog_failure_prevents_rehearsal(store):
    directory = create_backup(store)
    with (
        patch.object(catalog, "write", side_effect=sqlite3.OperationalError),
        patch.object(backup, "restore") as restore,
        pytest.raises(sqlite3.OperationalError),
    ):
        backup.rehearse(directory, "isolated")
    restore.assert_not_called()


def test_missing_catalog_metric_does_not_claim_zero_records(store):
    with patch.object(server, "probe", return_value={"reachable": False}):
        body = server.response("/metrics")[1]
    assert "codestra_postgresql_catalog_available 0" in body
    assert "codestra_postgresql_catalog_backups" not in body


def test_initialization_order():
    scripts = sorted(Path("init").iterdir())
    assert scripts.index(Path("init/10-roles.sh")) < scripts.index(
        Path("init/20-operations.sql")
    )

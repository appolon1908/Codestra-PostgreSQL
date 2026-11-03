import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from management import server


class ManagementTests(unittest.TestCase):
    def test_liveness_does_not_need_database(self):
        with patch.object(server, "probe", side_effect=AssertionError):
            self.assertEqual(server.response("/healthz")[:2], (200, {"status": "ok"}))
            self.assertFalse(server.response("/v1/capabilities")[1]["mutations"])

    def test_readiness_and_metrics_track_failure_and_version(self):
        for database, expected in [
            ({"reachable": False}, 503),
            ({"reachable": True, "version_num": 170006}, 200),
            ({"reachable": True, "version_num": 180000}, 503),
        ]:
            with patch.object(server, "probe", return_value=database):
                for path in ("/readyz", "/v1/postgresql/status"):
                    self.assertEqual(server.response(path)[0], expected)
                self.assertIn(
                    f"codestra_postgresql_ready {int(expected == 200)}",
                    server.response("/metrics")[1],
                )

    def test_probe_errors_are_redacted(self):
        with (
            patch("builtins.open", unittest.mock.mock_open(read_data="secret")),
            patch.object(
                subprocess, "run", side_effect=subprocess.TimeoutExpired("psql", 4)
            ),
        ):
            self.assertEqual(server.probe(), {"reachable": False})

    def test_pool_isolation_is_bounded_reference_only(self):
        import json

        body = {
            "max_connections": 100,
            "reserved_budget": 5,
            "usable_connections": 95,
            "connected": 12,
            "active": 4,
            "idle_in_transaction": 1,
            "utilization_ratio": 0.126316,
            "role_database_pairs": [
                {
                    "role_ref": "tenant_a_runtime",
                    "database_ref": "app",
                    "connected": 7,
                    "active": 3,
                    "idle_in_transaction": 1,
                },
                {
                    "role_ref": "tenant_b_runtime",
                    "database_ref": "app_b",
                    "connected": 5,
                    "active": 1,
                    "idle_in_transaction": 0,
                },
            ],
            "roles_spanning_databases": [],
            "isolation_scope": "role_database_refs_only",
            "arbitrary_sql": False,
        }
        with patch.object(server, "query", return_value=json.dumps(body)):
            code, payload, _ = server.response("/v1/postgresql/pool-isolation")
        self.assertEqual(code, 200)
        self.assertEqual(payload, body)
        serialized = json.dumps(payload).lower()
        for forbidden in (
            "query_text",
            "password",
            "session_id",
            "application_name",
            "client_addr",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse(payload["arbitrary_sql"])
        capabilities = server.response("/v1/capabilities")[1]["capabilities"]
        self.assertIn("runtime.pool_isolation", capabilities)

    def test_unknown_endpoint(self):
        self.assertEqual(server.response("/v1/postgresql/query")[0], 404)

    def test_contract_routes_and_schemas(self):
        import yaml
        from jsonschema import Draft202012Validator

        contract = yaml.safe_load(Path("openapi.yaml").read_text())
        for reachable in (True, False):
            with (
                patch.object(server, "query", side_effect=ValueError),
                patch.object(server.catalog, "read", side_effect=OSError),
                patch.object(
                    server,
                    "probe",
                    return_value=(
                        {
                            "reachable": True,
                            "version_num": 170006,
                            "in_recovery": False,
                            "max_connections": 100,
                        }
                        if reachable
                        else {"reachable": False}
                    ),
                ),
            ):
                for path, item in contract["paths"].items():
                    code, body, content_type = server.response(
                        path.replace("{id}", "example")
                    )
                    response = item["get"]["responses"][str(code)]
                    media = content_type.split(";")[0]
                    schema = response["content"][media]["schema"]
                    Draft202012Validator(schema).validate(body)
                    self.assertEqual(set(item), {"get"})

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

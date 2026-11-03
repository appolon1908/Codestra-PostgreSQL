import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def artifact(self):
        raw = self.directory / "backup.dump"
        raw.write_bytes(b"fake archive")
        manifest = {
            "format": 1,
            "encrypted": False,
            "artifact": raw.name,
            "sha256": backup.digest(raw),
            "plaintext_sha256": backup.digest(raw),
            "database": "source",
        }
        (self.directory / "manifest.json").write_text(json.dumps(manifest))
        return raw, manifest

    def test_corruption_rejected_before_restore(self):
        raw, _ = self.artifact()
        raw.write_bytes(b"corrupted")
        with patch.object(backup, "run") as run:
            with self.assertRaisesRegex(ValueError, "checksum"):
                backup.restore(self.directory, "rehearsal")
            run.assert_not_called()

    def test_manifest_path_escape_rejected(self):
        _, manifest = self.artifact()
        manifest["artifact"] = "../other"
        (self.directory / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "artifact name"):
            backup.verify(self.directory)

    def test_source_database_never_restored_over(self):
        self.artifact()
        with patch.object(backup, "run") as run:
            with self.assertRaisesRegex(ValueError, "different"):
                backup.restore(self.directory, "source")
            self.assertEqual(run.call_count, 1)  # archive validation only

    def test_encryption_required_by_default(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(ValueError, "Encryption required"),
        ):
            backup.backup(self.directory, "source")

    def test_retention_preserves_recent_unrelated_and_symlink(self):
        old = (
            (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + "a" * 32
        )
        recent = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + "b" * 32
        for name in (old, recent, "unrelated"):
            (self.directory / name).mkdir()
        linked = self.directory / ("20000101T000000Z-" + "c" * 32)
        linked.symlink_to(self.directory / "unrelated", target_is_directory=True)
        with patch.object(backup, "verify") as verify:
            backup.retain(self.directory, 14)
            verify.assert_called_once_with(self.directory / old)
        self.assertFalse((self.directory / old).exists())
        self.assertTrue((self.directory / recent).exists())
        self.assertTrue((self.directory / "unrelated").exists())
        self.assertTrue(linked.is_symlink())

    def test_failed_encryption_does_not_publish_backup(self):
        def fake_run(args, **kwargs):
            if args[0] == "pg_dump":
                Path(args[args.index("--file") + 1]).write_bytes(b"archive")

        with (
            patch.dict("os.environ", {"BACKUP_ENCRYPT_HOOK": "/test/encrypt"}),
            patch.object(backup, "run", side_effect=fake_run),
            patch.object(backup, "hook", side_effect=ValueError("encryption failed")),
            self.assertRaisesRegex(ValueError, "encryption failed"),
        ):
            backup.backup(self.directory, "source")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_existing_target_never_runs_restore(self):
        self.artifact()

        def fake_run(args, **kwargs):
            if args[0] == "createdb":
                raise ValueError("createdb failed")

        with (
            patch.object(backup, "run", side_effect=fake_run) as run,
            self.assertRaisesRegex(ValueError, "createdb failed"),
        ):
            backup.restore(self.directory, "existing")
        self.assertEqual(
            [call.args[0][0] for call in run.call_args_list], ["pg_restore", "createdb"]
        )

    def test_connection_uri_is_not_recorded_in_manifest(self):
        with self.assertRaisesRegex(ValueError, "never a connection URI"):
            backup.backup(self.directory, "postgresql://example/db")
        self.assertEqual(list(self.directory.iterdir()), [])

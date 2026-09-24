#!/usr/bin/env python3
"""PostgreSQL 17 logical backup, verification and isolated restore rehearsal."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


def run(args, **kwargs):
    # Never emit libpq errors: they may contain connection details or SQL/data.
    try:
        return subprocess.run(args, check=True, capture_output=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"{Path(args[0]).name} failed (exit {exc.returncode})"
        ) from None


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def hook(variable, source, target):
    executable = os.environ.get(variable)
    if not executable or not Path(executable).is_absolute():
        raise ValueError(f"{variable} must name an absolute executable path")
    run([executable, str(source), str(target)])
    if not target.is_file() or target.stat().st_size == 0:
        raise ValueError("Encryption/decryption hook produced no output")


def backup(directory, database, plaintext=False):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database):
        raise ValueError(
            "Database must be a simple lowercase name, never a connection URI"
        )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not plaintext and not os.environ.get("BACKUP_ENCRYPT_HOOK"):
        raise ValueError("Encryption required; configure BACKUP_ENCRYPT_HOOK")
    name = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    )
    # Staging remains on the same filesystem for atomic publication.
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=directory) as temporary:
        stage = Path(temporary)
        raw = stage / "backup.dump"
        run(
            [
                "pg_dump",
                "-w",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(raw),
                "--dbname",
                database,
            ]
        )
        run(["pg_restore", "--list", str(raw)])
        raw_sha = digest(raw)
        artifact = raw
        if not plaintext:
            artifact = stage / "backup.enc"
            hook("BACKUP_ENCRYPT_HOOK", raw, artifact)
            raw.unlink()
        manifest = {
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": database,
            "encrypted": not plaintext,
            "artifact": artifact.name,
            "sha256": digest(artifact),
            "plaintext_sha256": raw_sha,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # Verify encrypted backups can actually decrypt before publishing.
        verify(stage)
        destination = directory / name
        stage.rename(destination)
    return destination


def verified_dump(directory, temporary):
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != 1 or type(manifest.get("encrypted")) is not bool:
        raise ValueError("Unsupported manifest")
    expected = "backup.enc" if manifest["encrypted"] else "backup.dump"
    if manifest.get("artifact") != expected:
        raise ValueError("Invalid artifact name")
    artifact = directory / expected
    if artifact.is_symlink() or digest(artifact) != manifest["sha256"]:
        raise ValueError("Backup checksum mismatch")
    raw = artifact
    if manifest["encrypted"]:
        raw = Path(temporary) / "backup.dump"
        hook("BACKUP_DECRYPT_HOOK", artifact, raw)
    if digest(raw) != manifest["plaintext_sha256"]:
        raise ValueError("Plaintext checksum mismatch")
    run(["pg_restore", "--list", str(raw)])
    return raw, manifest


def verify(directory):
    with tempfile.TemporaryDirectory(prefix="codestra-verify-") as temporary:
        verified_dump(directory, temporary)


def restore(directory, target):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", target):
        raise ValueError("Target must be a simple lowercase database name")
    with tempfile.TemporaryDirectory(prefix="codestra-restore-") as temporary:
        raw, manifest = verified_dump(directory, temporary)
        if target == manifest["database"]:
            raise ValueError("Restore must target a different, new database")
        # No --clean or --create: createdb fails if the target already exists.
        run(["createdb", "-w", "--template=template0", target])
        run(
            [
                "psql",
                "-X",
                "-w",
                "--set=ON_ERROR_STOP=1",
                "--dbname",
                target,
                "-c",
                f'REVOKE ALL ON DATABASE "{target}" FROM PUBLIC',
            ]
        )
        run(
            [
                "pg_restore",
                "-w",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
                "--dbname",
                target,
                str(raw),
            ]
        )


def retain(directory, days):
    if days < 1:
        raise ValueError("Retention must be at least one day")
    now = datetime.now(timezone.utc)
    for entry in directory.iterdir():
        if (
            entry.is_symlink()
            or not entry.is_dir()
            or not re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{32}", entry.name)
        ):
            continue
        stamp = datetime.strptime(entry.name[:16], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
        if (now - stamp).total_seconds() > days * 86400:
            # Only remove complete, verified backups. Corrupt sets need investigation.
            verify(entry)
            shutil.rmtree(entry)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--database", required=True)
    create.add_argument("--directory", type=Path, default=Path("backups"))
    create.add_argument("--allow-plaintext-dev", action="store_true")
    for command in ("verify", "restore"):
        child = commands.add_parser(command)
        child.add_argument("backup", type=Path)
        if command == "restore":
            child.add_argument("--target", required=True)
    retention = commands.add_parser("retain")
    retention.add_argument("--directory", type=Path, default=Path("backups"))
    retention.add_argument(
        "--days", type=int, default=int(os.environ.get("BACKUP_RETENTION_DAYS", "14"))
    )
    args = parser.parse_args()
    try:
        if args.command == "backup":
            print(
                backup(
                    args.directory.resolve(), args.database, args.allow_plaintext_dev
                )
            )
        elif args.command == "verify":
            verify(args.backup.resolve())
        elif args.command == "restore":
            restore(args.backup.resolve(), args.target)
        else:
            retain(args.directory.resolve(), args.days)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

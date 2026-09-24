# Backup, restore and retention

## Scope and recovery objectives

Each archive covers exactly one app database using PostgreSQL 17 `pg_dump` custom
format. This is a consistent logical snapshot, not a physical cluster backup. Roles,
tablespaces, server settings and other databases are not included. The tool omits
ownership and grants so the app's reviewed policy can be reapplied after restore.
It does not supply WAL archiving, PITR, HA, automatic schedules or offsite upload.

App and infrastructure owners must agree RPO/RTO, backup frequency, offsite/immutable
copies, encryption key recovery, storage access and retention. Record a successful
restore rehearsal and measured recovery time before treating any backup plan as
operational. For PITR use an independently reviewed physical/WAL backup system;
a logical dump cannot meet continuous recovery requirements.

## Production job

Use an app-scoped backup role with CONNECT, schema USAGE and required read/sequence
privileges, not the bootstrap superuser. RLS requires an app-specific policy: verify
that dumps are complete; never assume a restricted reader captures all rows.
Supply credentials via an OpenBao-rendered `PGPASSFILE` (0600), set `PGHOST`,
`PGPORT`, `PGUSER`, `PGSSLMODE=verify-full`, and `PGSSLROOTCERT`. Never put credentials
in a database URI. The CLI accepts simple lowercase database names only.

```sh
export BACKUP_ENCRYPT_HOOK=/app/scripts/encrypt-age.sh
export BACKUP_DECRYPT_HOOK=/app/scripts/decrypt-age.sh
export OPENBAO_BACKUP_RECIPIENTS_FILE=/run/openbao/backup/recipients
export OPENBAO_BACKUP_IDENTITY_FILE=/run/openbao/backup/identity
python3 scripts/backup.py backup --database app_db --directory /backups
python3 scripts/backup.py verify /backups/<backup-id>
```

The included `age` hooks provide authenticated encryption. OpenBao references for
recipients/identity appear in the security inventory. A custom executable can use
OpenBao Transit envelope encryption; it receives exactly `INPUT OUTPUT` paths,
without a shell, and must fail nonzero on any integrity/authentication error.
Both encrypt and decrypt hooks are needed to verify recoverability before publication.
Keep keys outside backup storage and test recovery of retired key versions. The
runtime image includes PostgreSQL 17 clients, Python and age.

The backup stages a dump in a private directory, validates its table of contents,
computes SHA-256, encrypts, decrypts and verifies both checksums, then publishes the
complete directory by rename. Failures do not publish a final backup. Interrupted
`.staging-*` directories require operator cleanup. Plaintext staging is temporary,
not securely erased: use encrypted scratch storage or tmpfs, with enough capacity,
and encrypted backup volumes. Restore verification uses `TMPDIR` for plaintext.
The dev tools container supplies tmpfs `/tmp`; production must size it for the dump.

Checksums detect corruption, not malicious replacement of both archive and manifest.
Use trusted encrypted archives, immutable storage and authenticated manifests/storage
controls. Never restore an untrusted dump: a dump can contain executable SQL.
Do not run retention concurrently with a restore; schedule jobs under a deployment
lock. Offsite replication and durable storage acknowledgement happen outside this
CLI, and must complete before applying your deletion policy.

## Restore rehearsal

```sh
python3 scripts/backup.py restore /backups/<backup-id> --target app_rehearsal
```

Verification completes before `createdb`. The target must be a **new** database,
different from the recorded source. Existing targets fail without being modified.
Restore uses a single transaction, error-stop, no ownership and no ACL replay, and
revokes PUBLIC access on the new database. Use a dedicated rehearsal cluster and
a restore identity with CREATEDB; the backup reader does not need that privilege.
A failed restore may leave an empty target database; inspect and explicitly remove
it before retrying. The tool never drops a target on failure.

Afterward validate application invariants/counts/checksums, extensions, schema
versions and permissions with the app owner. Reapply ownership/grants, rehearse app
startup, and record timings. Production cutover is a separate authorized operation
through established app/Middleware release procedures, never performed by this tool.

## Development exercise

```sh
# App/test database must already exist; tools uses local admin socket access.
docker compose --profile tools build tools
docker compose run --rm tools scripts/backup.py backup --database app_db --directory /backups --allow-plaintext-dev
docker compose run --rm tools scripts/backup.py verify /backups/<backup-id>
docker compose run --rm tools scripts/backup.py restore /backups/<backup-id> --target app_rehearsal
```

The `tools` profile mounts the privileged local socket and the backup volume; do
not deploy it as a production backup job. `--allow-plaintext-dev` is explicit and
must not be used on production data. Integration tests generate their own fixtures,
exercise plaintext and real age-encrypted restores, and remove disposable volumes.

## Retention

```sh
BACKUP_RETENTION_DAYS=14 python3 scripts/backup.py retain --directory /backups
# Or explicitly:
python3 scripts/backup.py retain --directory /backups --days 14
```

Retention is a separate destructive operator command, never automatic after backup.
It removes only tool-named complete directories older than the UTC timestamp cutoff
and verifies them before deletion. It skips unrelated names and top-level symlinks;
corruption stops the command for investigation. It does not guarantee a minimum
number of surviving copies: ensure a newer verified offsite copy exists first.
Keep the backup root writable only by its operator; share it with no app identity.

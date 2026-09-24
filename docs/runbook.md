# Operational runbook

## Startup and preflight

1. Confirm PostgreSQL 17 image/client versions, storage capacity, app connection
   budgets, secret paths, network policy and production TLS overlay.
2. Render OpenBao credentials/certificates with workload-specific ownership. Dev:
   use `python3 scripts/dev-secrets.py`. Never print passwords in logs.
3. Run `docker compose config --quiet`, then `docker compose up -d --build --wait`.
4. Check `docker compose ps`, `/healthz`, `/readyz`, `/v1/postgresql/status`, exporter
   `pg_up` and `pg_hba_file_rules` errors. Run the app's own connectivity checks.
5. Establish verified encrypted/offsite backup and a restore rehearsal before traffic.

## Database unavailable

Check whether health is 200 while readiness is 503. Inspect `docker compose logs
--tail=100 postgres` in a restricted terminal, container status, free disk, DNS,
certificate validity and secret lease/rotation state. Check server major version:
readiness deliberately rejects non-17 servers. Do not disable TLS/authentication
or broaden HBA to make a probe pass. Readiness is not a test of write availability;
inspect `in_recovery` and app-specific checks for writer incidents.

## Connection exhaustion or lock incident

Use the reserved admin channel. Inspect `pg_stat_activity` and `pg_locks`, identify
app owners, idle transactions and pool fan-out. Prefer fixing/reducing the affected
app's pool or canceling its confirmed offending query; terminate sessions only with
incident-owner authorization. Do not increase max_connections without memory/load
review. Record cause, timing, affected owner and remediation without copying SQL
parameters or user data into incident logs.

## Credential rotation

Initialization scripts run only on a new data volume. Replacing a secret file does
not change PostgreSQL's stored password. Use an OpenBao database role/rotation
workflow or a reviewed admin session to change the appropriate role credential,
render the updated file atomically, then recreate/reload the consuming service so
its mount sees the new file. Verify fresh connections; revoke the old credential.
Coordinate static-role rotations to avoid a mismatch outage. Never remove a data
volume as a credential reset method. Bootstrap admin is break-glass only.

## Backup failure / recovery

Inspect job exit status and storage/key availability. The CLI intentionally redacts
client error details; reproduce diagnostics only in a restricted operator context.
Verify the latest complete backup and offsite copy. Investigate leftover staging
files; do not report a failed job as successful just because a dump file exists.
Use [the backup procedure](backups.md) to restore into an isolated new database;
record backup timestamp, checksum, key version/reference, restored app validation
and measured RTO. Reapply app-owned grants and keep traffic disconnected until the
app owner approves cutover. No automatic restore over an existing database occurs.

## Upgrade / config changes

Patch within PostgreSQL 17 using tested image digests. Rehearse backup/restore and
client/exporter compatibility, review release notes, schedule downtime for this
single-node runtime, and retain a recovery path. Major upgrades require a separate
app-owner reviewed migration plan and readiness-contract update, not a tag change.
Validate HBA syntax with `pg_hba_file_rules`; reload only after review. Some settings
require restart. Validate both TLS rejection and success after changing policy.

## Shutdown and disposal

`docker compose down` stops services but preserves data. `docker compose down -v`
is destructive and is reserved for explicitly disposable dev/test projects. The
integration script generates and destroys only its unique project. Production
retention/disposal follows each app's authority and storage policy.

## Host test limitation

Some restricted Docker hosts reject all executables with `operation not permitted`
when `no-new-privileges` is set, before PostgreSQL starts. Diagnose with an isolated
`docker run --rm --security-opt no-new-privileges:true postgres:17-bookworm postgres
--version`. Do not relax deployed hardening to conceal this host failure. Use a
compatible host for release validation. A local diagnostic override can be supplied
to `tests/integration.py` with `TEST_COMPOSE_OVERRIDE=/absolute/path/to/override.yaml`;
report any such relaxation with the results. The supplied CI template runs the committed configuration once installed by a
workflow-authorized maintainer.

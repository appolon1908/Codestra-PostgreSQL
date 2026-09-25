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

## Operational metadata and readiness APIs

All routes are GET-only behind the existing internal Middleware V3 boundary.
The runtime remains infrastructure, not a shared business-data authority. There
is no SQL passthrough, cross-app write API, backup creation API, or production
restore HTTP endpoint. Keep management private and apply gateway authentication
and rate limits before allowing remote access.

| Route | Meaning |
| --- | --- |
| `/v1/backups` and `/v1/backups/{id}` | Backup metadata, including retention tombstones |
| `/v1/rehearsals` and `/v1/rehearsals/{id}` | Durable isolated restore attempts |
| `/v1/postgresql/connections` | Connection limit, PostgreSQL reserved slots and counts by role class |
| `/v1/postgresql/security` | SSL setting, minimum TLS version, policy references and SHA-256 fingerprints |
| `/v1/postgresql/recovery` | Primary/standby, WAL positions, replication observations and PITR configuration presence |

Lists return the latest 100 records ordered by timestamp then ID, with `truncated`
when older records exist. Retrieve older known IDs through detail routes; use the
operator catalog for full inventory. A missing/locked/unreadable catalog or failed
PostgreSQL probe returns a redacted 503. Unknown record IDs return 404. Backup and
rehearsal metadata remain available during a PostgreSQL outage. Management mounts
only the separate `operations` volume read-only, never backup payloads or keys.

On existing installations, apply `init/20-operations.sql` to database `postgres`
as the bootstrap administrator with `psql -X -v ON_ERROR_STOP=1 -f ...` through
the governed operator channel. New volumes apply it automatically. Recreate
management to acquire its read-only operations mount. The functions are owned by
postgres, use a pinned `pg_catalog` search path, accept no arguments, and revoke
PUBLIC execution. The monitor receives only schema USAGE and execution of the
three fixed functions; it does not receive `pg_monitor`, raw HBA access or app grants.
Review their definitions as privileged code during upgrades.

Connection classes use precedence administrator, monitoring (the two named
Codestra monitor/exporter roles), replication (REPLICATION attribute), application
(all remaining client roles). Counts include client backends and WAL senders;
`connected` includes idle sessions, while `active` means `state = active` and
includes the probe itself. Omitted classes have zero connections. Reserved budget
is superuser_reserved_connections + reserved_connections, not app pool allocations.
WAL senders also have separate PostgreSQL limits, so do not infer exact free client
slots by subtracting the aggregate. Compare against app-owned pool budgets.

Security fingerprints summarize parsed **on-disk** HBA rules (including order,
file references and parse errors) and role attributes/configuration/memberships.
They exclude password hashes and expose no raw contents. They are drift signals,
not compliance verdicts: compare to an approved baseline. Role fingerprinting does
not cover per-database ACLs or every app grant. HBA presence/validity does not prove
reload, and SSL enabled/minimum version does not prove certificate validity or
client enforcement. TLS and OpenBao references point to repository guidance,
not certificates or DSNs. Keep OpenBao rendering and rotation unchanged.

Recovery configuration presence **does not prove successful recovery**;
`recovery_proven` is always false. Archive mode and a nonempty archive command or
library are separate flags, as is a restore command. Logical dumps do not supply
PITR; validate independent physical base backups, WAL continuity, key recovery,
restore points and app invariants in an isolated environment. Sender replay lag
is the maximum observed interval, nullable when unavailable/idle; it is not zero
by assumption. Standby receive/replay byte gap excludes upstream WAL not received.
See PostgreSQL's [statistics semantics](https://www.postgresql.org/docs/17/monitoring-stats.html)
and [recovery functions](https://www.postgresql.org/docs/17/functions-admin.html).

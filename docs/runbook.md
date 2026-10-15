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

## Missions 6–10: read-only readiness and operator evidence

Apply `init/30-readiness.sql` as bootstrap administrator in `postgres` on existing
installs after `20-operations.sql`; fresh volumes run both automatically. The
migration is repeatable. Only `codestra_monitor` receives EXECUTE on the fixed
functions. No new database membership, application CONNECT grant, arbitrary SQL,
maintenance, restore, promotion or failover HTTP route is added. Continue routing
cross-system access through Middleware V3 and the existing gateway/network policy.
OpenBao remains credential authority; no credential belongs in imported metadata.

| GET route | Meaning and limits |
| --- | --- |
| `/v1/postgresql/wal` | Archive configuration, counters/reset time, last success/failure, supported PostgreSQL target types and physical base-backup evidence age. |
| `/v1/ownership[/id]` | Operations-only database/schema ownership claims; no application definitions. |
| `/v1/postgresql/capacity` | Sum of non-template database sizes; table/index counts and relation bytes in `relation_scope`; operator storage samples and disk-free percentage. |
| `/v1/storage-samples[/id]` | Timestamped aggregate samples for growth comparisons. |
| `/v1/postgresql/maintenance` | Autovacuum policy, estimated vacuum/analyze backlog, statistics, database XID age and checkpoint counters. |
| `/v1/base-backups[/id]` | Operator-attested physical base-backup metadata, distinct from logical dump catalog. |
| `/v1/recovery-rehearsals[/id]` | Operator-attested failover/PITR rehearsal results, independent of existing logical restore reports. |

A successful archiver counter, configured command or operator verification flag
**does not prove recoverability**. WAL continuity, archive accessibility, keys,
base-backup validity, timeline/target reachability and application invariants must
be tested in isolation. `recoverability: unproven` and `recovery_proven: false`
remain explicit even with a successful report. Supported target types describe
PostgreSQL capability, not a configured or reachable target. Command text, archive
locations, relation names and business rows are never returned by these probes.

Database bytes cover all non-template databases, but PostgreSQL relation statistics
are database-local. This API connects only to `postgres`; `relation_scope` makes
that limit explicit. App owners must inspect their own database statistics through
their governed access. Never interpret zero local backlog as a healthy app fleet.
Backlog estimates use global threshold/scale-factor settings; table overrides,
insert-triggered vacuum, disabled per-table autovacuum and statistics lag can make
these estimates incomplete. Never-vacuumed/analyzed counts mean no recorded event
since statistics reset, not necessarily maintenance failure. Checkpoint rates
require successive samples with the same stats_reset. XID age spans databases.

### Import operational metadata

Run the restricted operator CLI locally or through the existing operator tools
profile. Prepare an approved JSON record outside application databases and import
it with `python3 -m scripts.readiness KIND /path/record.json`. With Compose:

```sh
docker compose run --rm -T --no-deps \
  -v /absolute/path/record.json:/tmp/record.json:ro \
  tools scripts/readiness.py ownership /tmp/record.json
```

The tools profile writes only the operations SQLite catalog for this command;
management mounts it read-only and opens it with SQLite `mode=ro`. No network or
PostgreSQL access is used by the importer. Record IDs are immutable and unique per
kind. Exact field allowlists reject arbitrary payloads; dates require timezones
and must not be future-dated. Read lists return the latest 100 records and flag
truncation; base backups sort by completion time, others by created_at. Detail
lookups retain access to older records. Missing/uninitialized metadata yields 503
on metadata routes and explicit unknown/null evidence in otherwise healthy probes.
Initialize the new metadata table by importing the first approved record. Do not
fabricate empty evidence just to clear an unavailable alert.

Ownership example (`schema_ref: "*"` reserves the whole database):

```json
{"id":"owner_001","created_at":"2026-09-24T00:00:00+00:00","database_ref":"identity","schema_ref":"public","owning_repository":"org/identity","service_id":"identity"}
```

The importer takes a SQLite write lock before checking claims. A repeated claim,
a different owner for the same database/schema, or overlap with a database-wide
claim fails without replacing existing evidence. Distinct databases/schemas may
have different owners. Ownership transfer requires a reviewed operator catalog
migration and retained audit evidence; there is no HTTP write or automatic transfer.

Storage sample example:

```json
{"id":"capacity_001","created_at":"2026-09-24T00:00:00+00:00","database_bytes":104857600,"disk_free_percent":42.5,"scope_ref":"cluster_a/pgdata"}
```

An operator/host collector supplies actual database-byte totals and available-space
percentage from the PostgreSQL data filesystem. Use available bytes/total bytes
(e.g. statvfs f_bavail/f_blocks), not the management container filesystem. Import
only samples for this runtime, keep a stable scope_ref, and compare growth only for
matching scopes and timestamps. Separate WAL/tablespace mounts need their own host
monitoring. The API reports the latest sample with `sample_age_seconds`; it does
not collect live disk values or invent zeros when evidence is absent. Retained
samples support byte differences over elapsed time; no background write sampler
runs in the read-only management service.

Physical base-backup example (a pg_dump is not eligible):

```json
{"id":"base_001","created_at":"2026-09-24T00:00:00+00:00","backup_ref":"physical_001","completed_at":"2026-09-23T23:00:00+00:00","verified":true,"wal_start":"0/1000000","wal_end":"0/2000000"}
```

`verified` is the operator's base-backup verification attestation, never a PITR
proof. WAL references may be null when unavailable. Base-backup age is elapsed
since the most recent reported physical backup completion; inspect its verified
flag separately. No physical backup or remote restore command is executed here.

Recovery rehearsal example:

```json
{"id":"recovery_001","created_at":"2026-09-24T00:00:00+00:00","scenario":"isolated_pitr_to_lsn","started_at":"2026-09-23T23:00:00+00:00","ended_at":"2026-09-23T23:00:20+00:00","rto_target_seconds":30,"duration_seconds":20,"data_integrity":"passed","wal_start":"0/1000000","wal_end":"0/2000000","outcome":"success"}
```

Record both successes and failures after a governed isolated rehearsal. Accepted
integrity states are passed/failed/not_verified, and outcomes success/failure.
Duration must match start/end within one second. `rto_met` requires success and a
duration within the stated target. Operator attestation is not independently
verified by the API. App-owned invariant checks must substantiate `passed`; archive
checksum or restore command success alone does not. Existing `/v1/rehearsals`
continues to report only logical archive/restore verification, which cannot be
upgraded to application-integrity or PITR evidence. Store restricted supporting
logs outside this catalog under the report ID. `live_failover_authorized` is always
false. Live failover requires separate current Middleware/operator authority and
OpenBao credentials; a stored report can never authorize it.

Maintenance execution remains outside this API. Use existing governed operator
procedures in an isolated environment if rehearsal is needed; no optional arbitrary
VACUUM CLI or SQL HTTP executor was introduced.

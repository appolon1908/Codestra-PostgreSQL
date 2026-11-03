# Observability and Middleware integration

## Management API

`openapi.yaml` is the exact GET success/readiness-failure contract. Management
metrics use Prometheus text format 0.0.4:

| Metric | Type | Meaning |
| --- | --- | --- |
| `codestra_postgresql_ready` | gauge | 1 if PostgreSQL 17 responds to the authenticated fixed probe, otherwise 0 |
| `codestra_postgresql_management_uptime_seconds` | gauge | API process monotonic uptime |

Use `/healthz` for process liveness and `/readyz` for dependency readiness. A database
outage leaves health 200, status/ready 503 and metrics 200 with readiness 0. The
probe does not expose SQL, app records, secrets or raw connection errors. There are
no high-cardinality caller/database labels. HTTP access logs are JSON event/status
records to stdout; PostgreSQL and exporter retain their own logging formats.

Middleware may read status/capabilities using bounded timeouts (greater than the
four-second probe deadline) and backoff. Do not retry a readiness failure as a
business write; this API has no effectful operations or commands. Gateway policy
must restrict the service route and provide authentication/audit correlation;
this repository does not modify Middleware, Kong, Caddy, or any app repository.

## postgres-exporter

Compose pins exporter `v0.17.1`, privately listening on `exporter:9187/metrics`.
Connection credentials use `DATA_SOURCE_USER` plus `DATA_SOURCE_PASS_FILE`; only
the exporter receives its password. The role gets `pg_monitor` but no app ownership.
Do not enable autodiscovery of all app databases or add custom business-data queries.
Exporter defaults can expose database names and operational activity: restrict
scrape access and retention. Metric families can change on upgrades; validate
actual scrape output and dashboards before image promotion.

Example Prometheus scrape jobs for a collector connected to approved private
networks (the collector is provisioned separately):

```yaml
scrape_configs:
  - job_name: codestra-postgresql-management
    scrape_interval: 30s
    scrape_timeout: 8s
    static_configs:
      - targets: ['management:8080']
  - job_name: codestra-postgresql-exporter
    scrape_interval: 30s
    scrape_timeout: 10s
    static_configs:
      - targets: ['exporter:9187']
```

Alert on target `up == 0`, `pg_up == 0`, or management readiness 0 persisting for
one minute. Monitor connection utilization against the configured budget, disk
capacity/inodes, lock waits, deadlocks, transaction age, and recovery/replication
lag where applicable. Backups are operator jobs: publish last successful backup,
last verified offsite copy and last rehearsal age through your scheduler/textfile
collector; the API deliberately does not claim backup freshness from local files.
Assign alert ownership, thresholds and paging policy in the deployment repository.

References: [postgres-exporter upstream](https://github.com/prometheus-community/postgres_exporter),
[PostgreSQL 17 HBA](https://www.postgresql.org/docs/17/auth-pg-hba-conf.html),
[PostgreSQL 17 pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html).

Operational catalog gauges: `codestra_postgresql_catalog_available` reports
whether both metadata tables can be read; `codestra_postgresql_catalog_backups`
and `codestra_postgresql_catalog_rehearsals` count all stored records (including
backup tombstones). Counts are omitted when unavailable, never fabricated as zero.
No database names, backup IDs, roles, or targets become metric labels. Alert on
catalog unavailability separately from PostgreSQL readiness; use rehearsal reports
to investigate stale/running/failed attempts. Configuration presence is not a
recovery-success metric.

## Missions 6–10

Management metrics add `codestra_postgresql_{wal,capacity,maintenance}_available`;
failed probes omit measurements instead of inventing zeros. Metadata availability
for ownership/recovery rehearsal evidence is separate. No database/schema/service
identifiers, row content or report IDs become metric labels. Counters from
PostgreSQL are exposed as sampled gauges; account for stats resets when deriving
rates. A stats endpoint can be available while operator evidence is absent.

Suggested initial alert thresholds (tune to the deployment's RPO/RTO and load):

- `wal_archive_mode_enabled == 0` or `wal_archive_transport_configured == 0` where
  PITR policy requires archiving; `wal_archive_failure_pending == 1` for 5 minutes.
- `wal_last_archived_age_seconds > 900` with expected write activity; idle databases
  can legitimately archive less frequently. Missing age is unknown, not healthy.
- `wal_base_backup_age_seconds > 86400` or absence when physical backups are required.
- `capacity_disk_free_percent < 20` warning, `< 10` critical; also alert on missing
  disk samples and `capacity_sample_age_seconds > 900`. Disk values are operator
  samples for the scope in the API, not management-container free space.
- Rising `capacity_database_bytes`; compare timestamped `/v1/storage-samples` with
  matching scope before calculating growth. Relation counts are local to postgres.
- `maintenance_autovacuum_enabled == 0`; sustained positive vacuum/analyze backlog
  estimates; `maintenance_oldest_database_xid_age / maintenance_freeze_max_age > 0.8`.
  Local zero backlog does not describe other databases. Inspect checkpoint requested
  versus timed deltas and write/sync durations through the API.
- `recovery_rehearsals_latest_success == 0`, `recovery_rehearsals_latest_rto_met == 0`,
  or `recovery_rehearsals_latest_data_integrity_passed == 0`; flag stale/missing reports
  according to rehearsal cadence via `recovery_rehearsals_latest_age_seconds`.

All names above are prefixed `codestra_postgresql_`. Alerting is advisory; no metric,
registry claim or rehearsal result is failover authorization. See the runbook for
scope, evidence freshness and operator import contracts.

## Connection-pool and tenant-isolation readback

`GET /v1/postgresql/pool-isolation` is a fixed read-only aggregate for Middleware. It
returns pool pressure plus at most 64 role/database reference pairs and at most 64 roles
that currently span more than one database. It never returns SQL text, application rows,
client addresses, session identifiers or credentials, and it cannot execute caller-supplied
SQL. `roles_spanning_databases` is an observation for policy review, not proof of a tenant
breach; ownership policy remains authoritative in the operations catalog.

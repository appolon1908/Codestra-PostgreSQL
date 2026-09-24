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

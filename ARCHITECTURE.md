# Architecture and authority

## Owned boundary

Codestra-PostgreSQL owns the PostgreSQL runtime baseline, operational roles,
connection/TLS policy templates, read-only management contract, exporter wiring,
and backup/restore tooling. Each application owns its database/schema, migrations,
records, retention requirements and authorization. No business tables are defined
here. A shared server does not create shared data authority; separate instances
are appropriate when stronger isolation or independent recovery is required.

Middleware V3 is the sole cross-system integration authority:

```text
Caller -> Caddy -> Kong -> Middleware V3 -> PostgreSQL management API
App runtime -> its own PostgreSQL database only
Prometheus -> private exporter / management metrics
Operator backup job -> one app database -> encrypted backup storage
```

App database connections are app-local persistence, never a cross-system business
integration route. No direct cross-app writes, provider calls, foreign-data wrappers,
shared business schemas, or copied application authorization logic belong here.
Middleware owns authentication normalization, caller policy, orchestration, command
ledger/outbox, retries, reconciliation and cross-system audit.

## Components

`postgres` runs PostgreSQL 17 on a persistent volume. `management` runs an unprivileged
Python HTTP adapter using PostgreSQL's client for a fixed, read-only probe query.
`exporter` uses a separate `pg_monitor` role. Neither monitor is an app role or
superuser. A disabled-by-default `tools` profile has privileged local socket access
for development backup/restore exercises. Only the DB and tools mount that socket.

Health and capability routes need no database connection. Readiness/status/metrics
use a two-second connect/statement timeout and a four-second subprocess deadline.
No credential, SQL error, database name inventory, or business data is returned.
Metrics are generated locally and do not proxy exporter payloads.

## Deployment boundary

Compose is a single-node development runtime. In production, enforce network policy
and authenticated gateway/mTLS access before permitting management traffic. The
minimal stdlib HTTP adapter is an internal service and requires gateway request
limits, timeouts and rate limiting. It has no internet-facing authentication layer.
Do not expose its port, PostgreSQL, or exporter directly to public callers.

All management operations are GET-only and effect-free; unsupported methods return
501 and unknown GET paths return JSON 404. Future mutating operations require an
explicit contract, authorization and idempotency design through Middleware V3.
Backups and restores remain operator CLI jobs, not remote API capabilities.

Operational metadata is stored in a separate operator-written SQLite volume:
backup inventory and isolated restore rehearsal reports only. Management reads it
without write access and calls three fixed, zero-argument PostgreSQL functions for
aggregate connection, TLS/policy and recovery observations. This adds no shared
business-data authority, application table access, arbitrary SQL API, cross-app
writes, or HTTP restore/backup mutations. Middleware V3 retains integration authority.

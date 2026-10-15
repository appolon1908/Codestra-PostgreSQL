# Security and deployment policy

## OpenBao secrets

Only OpenBao-compatible references are committed. `.env.example` is a reference
inventory and file-location configuration, not an automatic OpenBao client. An
operator/agent resolves KV v2 names and writes files; do not pass a reference as a
password. Keep bootstrap authentication outside this repository (workload identity
preferred). Grant each workload only its own secret path.

| Purpose | Reference convention | Render destination |
| --- | --- | --- |
| Bootstrap admin | `kv/codestra/postgresql/<env>/admin#password` | `/run/secrets/admin_password` |
| API probe | `kv/codestra/postgresql/<env>/monitor#password` | `/run/secrets/monitor_password` |
| Exporter | `kv/codestra/postgresql/<env>/exporter#password` | `/run/secrets/exporter_password` |
| Backup recipient/identity | `kv/codestra/postgresql/<env>/backup#recipients` / `#identity` | operator-selected secret files |
| Transit hook alternative | `transit/keys/codestra-postgresql-backup` | custom authenticated hook, no key export |
| Server certificate | `pki/issue/codestra-postgresql` | `/run/openbao/postgresql/` |
| App credential lease | `database/creds/<app>-runtime` | app-local libpq credential file |

Dev secret files are readable by differing container UIDs beneath a host 0700
directory. Production must instead use per-workload ownership and file modes,
read-only secret mounts, and non-persistent secret storage. Do not put private
keys in the backup volume. OpenBao policy, token lifecycle and agent deployment
must be provisioned in the deployment's secret authority.

## Network and TLS

The dev `pg_hba.dev.conf` allows only operational monitor roles over its isolated
network. Add application-specific rules in a reviewed deployment overlay; never
replace the final rejects with `host all all ... trust`. The production example
uses explicit database/role/CIDR combinations, rejects cleartext first, and denies
all remaining IPv4/IPv6 traffic. Replace example CIDRs with actual workload ranges.

Mount the production HBA file at `/etc/postgresql/pg_hba.conf` and include the TLS
settings after the baseline (`include = '/etc/postgresql/tls.conf'`). The server
key must be owned by the PostgreSQL UID and mode 0600. Use OpenBao PKI issued
certificates with correct DNS SANs, rotate before expiry, and validate the chain.
Clients use `sslmode=verify-full` and `sslrootcert`; configure `PGSSLMODE` and
`PGSSLROOTCERT` for management/backup, and equivalent query parameters in exporter
`DATA_SOURCE_URI`. Compose's exporter `sslmode=disable` is dev-only.

Validate `pg_hba_file_rules` before reload; test both accepted and denied clients.
TLS changes require end-to-end verification. Gateway/service-mesh mTLS protects
the management HTTP hop; TLS on PostgreSQL does not protect the API port.

## Roles and resource limits

Management gets CONNECT on the maintenance DB and only built-in probe functions.
Exporter additionally receives `pg_monitor`, which exposes operational information;
keep its metrics private. App runtime roles are never superuser, CREATEDB,
CREATEROLE, replication users, owners, or members of other app roles. Use separate
NOLOGIN owners and short-lived migration identities, with `SET ROLE` to the owner
so default privileges apply. See `config/app-roles.sql.example`.

Baseline: 100 connections, five reserved for superusers, API limit eight, exporter
limit five. Budget sum(pool size × replica count) plus migrations, backups and
monitoring below 95, with operational headroom. App example caps runtime at 20 and
migration at two; tune per deployment. Use app-specific pool identities if adding
PgBouncer. Work memory multiplies per operation/connection; load test before
raising limits. Baseline timeouts bound statements, lock waits and idle transactions;
backup/migration jobs may need approved session-local overrides.

No app DDL or parameterized query values are logged by baseline settings. Manage
log access/retention as sensitive operational data. Containers drop capabilities
where supported, run the API as postgres UID without root, and use read-only
filesystems. Preserve `no-new-privileges` in deployment. Pin validated image digests
in release overlays and regularly rebuild for patched PostgreSQL 17/client packages.

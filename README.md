# Codestra PostgreSQL

Standalone PostgreSQL 17 runtime, policy, backup, and health service. This repository
owns operational infrastructure, **not application schemas or business authority**.
Middleware V3 remains the cross-system integration authority.

## Start locally

Requires Docker Engine, Compose 2.24+, and Python 3.11+.

```sh
cp .env.example .env
python3 scripts/dev-secrets.py
docker compose up -d --build --wait
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:8080/v1/postgresql/status
docker compose down
```

The helper generates disposable development credentials in ignored `.secrets/`;
it preserves existing files. In deployed environments an OpenBao agent renders
credentials instead. No passwords belong in `.env`, Git, command arguments, or URLs.
Do not delete credentials while retaining the database volume: initialization only
runs on an empty data directory. Use the rotation procedure in the runbook.

PostgreSQL and the exporter have **no host ports**. The management API binds only
host loopback and offers no mutations. Development uses an isolated internal Docker
network without database TLS; apply the production policy before deployment.
`docker compose down` preserves data; `down -v` destroys this project's volumes.

## Contract

| Endpoint | Meaning |
| --- | --- |
| `GET /healthz` | API process alive; independent of PostgreSQL |
| `GET /readyz` | Authenticated query succeeds against PostgreSQL 17; otherwise 503 |
| `GET /health/ready` | Middleware V3 readiness alias for `/readyz` |
| `GET /v1/postgresql/status` | Same readiness plus version, recovery state and connection limit |
| `GET /v1/capabilities` | Read-only runtime capabilities and Middleware V3 authority |
| `GET /metrics` | Prometheus readiness and management process uptime; 200 even during DB outage |

A reachable standby is ready; callers must inspect `in_recovery` when writer
availability matters. This API does not test an application's schema or migration
version. See [OpenAPI](openapi.yaml) and [exporter contract](docs/observability.md).

## Verify

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m openapi_spec_validator openapi.yaml
python3 scripts/dev-secrets.py
docker compose config --quiet
.venv/bin/python tests/integration.py
```

Integration tests use a unique disposable Compose project and ephemeral host port;
they remove only that project's containers and volumes. They test real PostgreSQL,
backup corruption rejection, restore data, role isolation, exporter and outages.
The [CI template](docs/ci/checks.yml.example) runs the same checks with committed
hardening settings. Install it as `.github/workflows/checks.yml` using a GitHub
credential with workflow scope; the current push credential cannot create workflows.

## Operations and ownership

- [Architecture and authority](ARCHITECTURE.md)
- [Security, TLS, OpenBao and connection budgets](docs/security.md)
- [App ownership and migration safety](docs/migrations.md)
- [Backup, restore and retention](docs/backups.md)
- [Metrics and integration contract](docs/observability.md)
- [Operational runbook](docs/runbook.md)

The dev tools profile has local administrative socket access for rehearsals; it is
not a production backup identity. Production uses app-scoped credentials and
separate backup jobs. Deployment-specific certificates, encryption recipients,
offsite storage, schedules, RPO/RTO, and digest-pinned image promotion are operator
responsibilities. This dev runtime is not an HA or PITR solution.

Operational APIs now include backup catalog and rehearsal list/detail under
`/v1/backups` and `/v1/rehearsals`, plus read-only `/v1/postgresql/connections`,
`/v1/postgresql/security`, and `/v1/postgresql/recovery`. See the
[operational runbook](docs/runbook.md#operational-metadata-and-readiness-apis)
for existing-volume installation, field semantics and limitations, and the
[backup metadata procedure](docs/backups.md#durable-operator-metadata) for catalog
storage and reconciliation. Backup/restore actions remain operator CLI only.

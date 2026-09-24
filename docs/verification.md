# Verification record — 2026-09-24

Branch: `mission/face-id-middleware-integration-20260924`.
Changes are confined to Codestra-PostgreSQL; Middleware and FACE-ID were not modified.

## Passed locally

- 13 Python unit/contract tests: independent liveness; readiness/version/metrics
  behavior; error redaction; OpenAPI response schema conformance; corruption/path
  rejection; source/existing-target protection; default encryption requirement;
  failed encryption publication safety; URI rejection; retention cutoff and symlinks.
- OpenAPI 3.1 validator; Compose configuration; shell syntax and Git whitespace checks.
- Real Docker integration on PostgreSQL 17.11 and postgres-exporter v0.17.1:
  startup; all five HTTP endpoints; unsupported route; monitor rejection from app DB;
  exporter `pg_up 1`; dump verification; plaintext restore data; age-encrypted
  backup/decryption/restore data; existing/source-target refusal; corrupted archive
  refusal before creating a database; health/readiness/metrics during outage and
  readiness recovery after restart. Disposable integration resources were removed.

## Host-specific qualification

The local Docker host rejects every container executable with `operation not
permitted` when `no-new-privileges:true` is set, including a standalone
`postgres --version` command. Integration therefore used an **uncommitted** override
that removed only `security_opt` on the four services. Capability drops, non-root
API/tools users, read-only filesystems, private network, role policy and secret
mounts remained in place. Committed Compose retains `no-new-privileges:true`.
The GitHub CI template exercises the committed settings once installed; no CI
success is claimed by this local verification record. The current OAuth credential
lacks `workflow` scope, so GitHub rejected a push containing an active workflow.
The definition is provided at `docs/ci/checks.yml.example` for a workflow-authorized
maintainer to install as `.github/workflows/checks.yml`.

## Deployment validation still required

Production TLS/mTLS and actual CIDR policy; OpenBao agent/lease/rotation integration;
offsite immutable backup and key recovery; app-owned RLS/grants/invariant checks;
load/connection sizing; RPO/RTO measurement and release-image digest approval.
These are deployment-specific prerequisites, not claims made by the dev suite.

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

## Five operational missions — follow-up verification

Implemented read-only catalog/rehearsal list and detail APIs and fixed connection,
security and recovery probes. Added operator catalog registration, retention
tombstones and durable rehearsal attempts, a separate read-only management volume,
and OpenAPI 1.1.0 contracts. No business-data storage or HTTP mutation was added.

- Ruff, 23 unit/contract tests, OpenAPI validation, Compose config validation,
  Docker build and `git diff --check` pass.
- Expanded real PostgreSQL integration passes with the same local host override
  described above: connection totals/reservations, SSL/HBA reporting, role-policy
  fingerprint changes, primary recovery status with unknown lag, catalog list/detail,
  persisted successful rehearsal, operator failures, and metadata availability
  while PostgreSQL is stopped. Existing plaintext and age-encrypted restore,
  app-database isolation, exporter, corruption/overwrite refusal and restart
  recovery checks still pass. Initialization order was exercised on a fresh volume.
- The committed hardening was attempted first and failed on this host's
  `no-new-privileges` executable restriction, reproduced with `postgres --version`.
  No deployed hardening was relaxed. Temporary test containers/volumes were removed.

A live standby/PITR topology, production certificate policy and an actual physical
recovery are not claimed by these tests. Configuration observations deliberately
return `recovery_proven: false`; application recovery validation stays app-owned.

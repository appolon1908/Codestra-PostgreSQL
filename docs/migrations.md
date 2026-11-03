# Database ownership and migration safety

1. Every app owns its migrations and database. Prefer a dedicated database per app,
   with an app-owned schema inside it. Revoke PUBLIC database CONNECT/TEMP and public
   schema privileges at provisioning. This runtime never runs app migrations at boot.
2. Schema isolation within one database is an exception requiring app-owner review:
   independent schema owners, explicit USAGE/table privileges, safe search paths,
   no cross-app memberships and no PUBLIC object grants. Shared-database restore
   coupling makes this weaker than separate databases; prefer separate instances
   for stronger security/recovery requirements.
3. No direct cross-app reads/writes, cross-app foreign keys, FDW links or shared
   business tables. Request cross-system operations through Middleware V3.
4. Separate owner, migrator, runtime, and backup roles. Runtime cannot perform DDL.
   Issue migrator credentials through OpenBao only for the release window; use
   the app's advisory-lock migration mechanism to prevent concurrent migration runs.
5. Before changing data: review SQL/locks, estimate table size and disk headroom,
   verify a recent recoverable backup, record migration identity/checksum, and
   rehearse on a restored isolated database. Never place production data in dev.
6. Use expand/backfill/contract across compatible app releases. Batch backfills;
   use bounded lock/statement timeouts; monitor replication lag and blocked sessions.
   Run concurrent index operations outside transactions where PostgreSQL requires it.
7. Roll back app code only while the schema remains compatible. Prefer a forward
   repair for applied destructive changes. A database restore is an explicit
   app-owner incident decision with downtime/data-loss implications, not an
   automatic migration rollback. This repo's restore tool never overwrites a DB.
8. Validate app invariants and permissions after restore/migration. Restores omit
   source ownership and grants; reapply the app-owned role/schema policy before
   reconnecting runtime clients. Do not grant runtime ownership as a shortcut.

Provisioning examples are templates, not an invitation to centralize app schemas.
Keep app provisioning SQL and migration versions in the app's own repository.

#!/usr/bin/env bash
set -euo pipefail
# psql reads passwords from process environment, not CLI arguments or logs.
export MONITOR_PASSWORD EXPORTER_PASSWORD
MONITOR_PASSWORD=$(cat /run/secrets/monitor_password)
EXPORTER_PASSWORD=$(cat /run/secrets/exporter_password)
psql -X -v ON_ERROR_STOP=1 --username postgres --dbname postgres <<'SQL'
\getenv monitor_password MONITOR_PASSWORD
\getenv exporter_password EXPORTER_PASSWORD
CREATE ROLE codestra_monitor LOGIN CONNECTION LIMIT 8 PASSWORD :'monitor_password';
CREATE ROLE codestra_exporter LOGIN CONNECTION LIMIT 5 PASSWORD :'exporter_password';
GRANT pg_monitor TO codestra_exporter;
REVOKE CONNECT, TEMPORARY ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO codestra_monitor, codestra_exporter;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER ROLE codestra_monitor SET default_transaction_read_only = on;
ALTER ROLE codestra_exporter SET default_transaction_read_only = on;
SQL
unset MONITOR_PASSWORD EXPORTER_PASSWORD

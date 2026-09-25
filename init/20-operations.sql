-- Apply as the bootstrap administrator in postgres, including existing installs.
-- This schema contains only fixed read-only functions, never app records.
BEGIN;
CREATE SCHEMA IF NOT EXISTS codestra_ops AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA codestra_ops FROM PUBLIC;
GRANT USAGE ON SCHEMA codestra_ops TO codestra_monitor;

CREATE OR REPLACE FUNCTION codestra_ops.connections() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
WITH classes AS (
 SELECT CASE WHEN r.rolsuper THEN 'administrator'
             WHEN a.usename IN ('codestra_monitor', 'codestra_exporter') THEN 'monitoring'
             WHEN r.rolreplication THEN 'replication' ELSE 'application' END AS class,
        a.state
 FROM pg_stat_activity a LEFT JOIN pg_roles r ON a.usesysid = r.oid
 WHERE a.backend_type IN ('client backend', 'walsender')
), counts AS (
 SELECT class, count(*) AS connected, count(*) FILTER (WHERE state = 'active') AS active
 FROM classes GROUP BY class
)
SELECT jsonb_build_object(
 'max_connections', current_setting('max_connections')::int,
 'reserved_budget', current_setting('superuser_reserved_connections')::int + current_setting('reserved_connections')::int,
 'superuser_reserved', current_setting('superuser_reserved_connections')::int,
 'role_reserved', current_setting('reserved_connections')::int,
 'by_role_class', coalesce((SELECT jsonb_agg(to_jsonb(counts) ORDER BY class) FROM counts), '[]'::jsonb)
);
$$;

CREATE OR REPLACE FUNCTION codestra_ops.security() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
SELECT jsonb_build_object(
 'ssl_enabled', current_setting('ssl')::boolean,
 'minimum_tls_version', current_setting('ssl_min_protocol_version'),
 'tls_policy_ref', 'config/tls.conf.example',
 'credential_policy_ref', 'docs/security.md',
 'pg_hba_policy_fingerprint', (SELECT encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(h) ORDER BY rule_number)::text, '[]'), 'UTF8')), 'hex') FROM pg_hba_file_rules h),
 'pg_hba_rules_valid', NOT EXISTS (SELECT 1 FROM pg_hba_file_rules WHERE error IS NOT NULL),
 'role_policy_fingerprint', encode(sha256(convert_to(jsonb_build_object(
   'roles', (SELECT jsonb_agg(jsonb_build_array(rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolconnlimit, rolbypassrls, rolconfig) ORDER BY rolname) FROM pg_roles),
   'memberships', (SELECT jsonb_agg(jsonb_build_array(roleid, member, grantor, admin_option, inherit_option, set_option) ORDER BY roleid, member, grantor) FROM pg_auth_members)
 )::text, 'UTF8')), 'hex')
);
$$;

CREATE OR REPLACE FUNCTION codestra_ops.recovery() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
SELECT jsonb_build_object(
 'role', CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END,
 'in_recovery', pg_is_in_recovery(),
 'wal_level', current_setting('wal_level'),
 'wal_position', CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn() ELSE pg_current_wal_lsn() END::text,
 'receive_position', pg_last_wal_receive_lsn()::text,
 'replay_paused', CASE WHEN pg_is_in_recovery() THEN pg_is_wal_replay_paused() ELSE NULL END,
 'replica_count', (SELECT count(*) FROM pg_stat_replication),
 'replication_lag_seconds', (SELECT max(extract(epoch FROM replay_lag)) FROM pg_stat_replication),
 'receive_replay_gap_bytes', CASE WHEN pg_is_in_recovery() THEN pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn()) ELSE NULL END,
 'archive_mode_enabled', current_setting('archive_mode') <> 'off',
 'archive_transport_configured', current_setting('archive_command') <> '' OR current_setting('archive_library') <> '',
 'restore_command_configured', current_setting('restore_command') <> '',
 'recovery_proven', false
);
$$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA codestra_ops FROM PUBLIC;
ALTER FUNCTION codestra_ops.connections() OWNER TO postgres;
ALTER FUNCTION codestra_ops.security() OWNER TO postgres;
ALTER FUNCTION codestra_ops.recovery() OWNER TO postgres;
GRANT EXECUTE ON FUNCTION codestra_ops.connections(), codestra_ops.security(), codestra_ops.recovery() TO codestra_monitor;
COMMIT;

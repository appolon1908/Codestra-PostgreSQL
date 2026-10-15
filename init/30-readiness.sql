-- Fixed aggregate queries only; apply as bootstrap administrator in postgres.
BEGIN;
CREATE OR REPLACE FUNCTION codestra_ops.wal() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
SELECT jsonb_build_object(
 'archive_mode_enabled', current_setting('archive_mode') <> 'off',
 'archive_transport_configured', current_setting('archive_command') <> '' OR current_setting('archive_library') <> '',
 'wal_level', current_setting('wal_level'),
 'restore_command_configured', current_setting('restore_command') <> '',
 'recovery_target_types_supported', jsonb_build_array('time', 'lsn', 'name', 'xid', 'immediate'),
 'archived_count', archived_count, 'failed_count', failed_count,
 'last_archived_at', last_archived_time, 'last_failed_at', last_failed_time,
 'stats_reset', stats_reset,
 'archive_failure_pending', last_failed_time IS NOT NULL AND (last_archived_time IS NULL OR last_failed_time > last_archived_time),
 'recovery_proven', false
) FROM pg_stat_archiver;
$$;
CREATE OR REPLACE FUNCTION codestra_ops.capacity() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
SELECT jsonb_build_object(
 'database_count', (SELECT count(*) FROM pg_database WHERE NOT datistemplate),
 'database_bytes', (SELECT coalesce(sum(pg_database_size(oid)), 0) FROM pg_database WHERE NOT datistemplate),
 'relation_scope', current_database(),
 'table_count', (SELECT count(*) FROM pg_stat_user_tables),
 'index_count', (SELECT count(*) FROM pg_stat_user_indexes),
 'relation_bytes', (SELECT coalesce(sum(pg_total_relation_size(relid)), 0) FROM pg_stat_user_tables)
);
$$;
CREATE OR REPLACE FUNCTION codestra_ops.maintenance() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
SELECT jsonb_build_object(
 'relation_scope', current_database(), 'autovacuum_enabled', current_setting('autovacuum')::boolean,
 'dead_tuple_estimate', (SELECT coalesce(sum(n_dead_tup), 0) FROM pg_stat_user_tables),
 'modified_since_analyze_estimate', (SELECT coalesce(sum(n_mod_since_analyze), 0) FROM pg_stat_user_tables),
 'vacuum_backlog_estimate', (SELECT count(*) FROM pg_stat_user_tables s JOIN pg_class c ON c.oid=s.relid WHERE n_dead_tup > current_setting('autovacuum_vacuum_threshold')::int + current_setting('autovacuum_vacuum_scale_factor')::float * greatest(c.reltuples, 0)),
 'analyze_backlog_estimate', (SELECT count(*) FROM pg_stat_user_tables s JOIN pg_class c ON c.oid=s.relid WHERE n_mod_since_analyze > current_setting('autovacuum_analyze_threshold')::int + current_setting('autovacuum_analyze_scale_factor')::float * greatest(c.reltuples, 0)),
 'never_vacuumed_tables', (SELECT count(*) FROM pg_stat_user_tables WHERE last_vacuum IS NULL AND last_autovacuum IS NULL),
 'never_analyzed_tables', (SELECT count(*) FROM pg_stat_user_tables WHERE last_analyze IS NULL AND last_autoanalyze IS NULL),
 'oldest_database_xid_age', (SELECT max(age(datfrozenxid)) FROM pg_database),
 'freeze_max_age', current_setting('autovacuum_freeze_max_age')::bigint,
 'checkpoints_timed', num_timed, 'checkpoints_requested', num_requested,
 'checkpoint_write_ms', write_time, 'checkpoint_sync_ms', sync_time, 'stats_reset', stats_reset
) FROM pg_stat_checkpointer;
$$;
REVOKE ALL ON FUNCTION codestra_ops.wal(), codestra_ops.capacity(), codestra_ops.maintenance() FROM PUBLIC;
ALTER FUNCTION codestra_ops.wal() OWNER TO postgres;
ALTER FUNCTION codestra_ops.capacity() OWNER TO postgres;
ALTER FUNCTION codestra_ops.maintenance() OWNER TO postgres;
GRANT EXECUTE ON FUNCTION codestra_ops.wal(), codestra_ops.capacity(), codestra_ops.maintenance() TO codestra_monitor;
COMMIT;

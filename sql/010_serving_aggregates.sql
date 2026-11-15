-- Run as postgres after 008. New serving tables; no fact rows are modified.
BEGIN;
SET LOCAL lock_timeout = '10s';
-- Keep public version updates bound to public, irrespective of caller search_path.
-- Benchmark migration 008 captures its own isolated schema at function creation.
DO $$ BEGIN
    IF current_schema()='public' THEN
        ALTER FUNCTION public.ridepulse_advance_version() SET search_path=public,pg_catalog;
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS aggregate_state (
    city TEXT PRIMARY KEY,
    source_version BIGINT NOT NULL DEFAULT -1,
    refreshed_at TIMESTAMPTZ,
    valid BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS station_hour_counts (
    city TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ('pickups','dropoffs')),
    station_id TEXT NOT NULL,
    hour_start TIMESTAMPTZ NOT NULL,
    station_name TEXT,
    trip_count BIGINT NOT NULL CHECK (trip_count >= 0),
    PRIMARY KEY (city, metric, station_id, hour_start)
);
CREATE INDEX IF NOT EXISTS idx_station_hours_range ON station_hour_counts(city,metric,hour_start);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ridepulse_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON aggregate_state,station_hour_counts TO ridepulse_app;
    END IF;
END $$;
COMMIT;

-- RidePulse Cloud SQL schema alignment
-- PostgreSQL 16
-- Intended for the existing Cloud SQL ridepulse database.
-- Preserves existing trips, stations and snapshots.

BEGIN;

SET LOCAL lock_timeout = '15s';

-- 1. Validate the existing cloud schema
DO $$
DECLARE
    required_table TEXT;
BEGIN
    FOREACH required_table IN ARRAY ARRAY[
        'trips', 'stations', 'station_snapshots',
        'route_counts', 'hourly_trip_counts'
    ] LOOP
        IF to_regclass('public.' || required_table) IS NULL THEN
            RAISE EXCEPTION 'Missing required table: public.%', required_table;
        END IF;
    END LOOP;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_app') THEN
        RAISE EXCEPTION 'Missing required role: ridepulse_app';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'route_counts'
          AND column_name = 'city'
    ) THEN
        RAISE EXCEPTION 'Missing required column: public.route_counts.city';
    END IF;

    IF current_database() <> 'ridepulse' THEN
        RAISE EXCEPTION 'Wrong database';
    END IF;

    IF (
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'trips'
          AND column_name = 'started_at'
    ) IS DISTINCT FROM 'timestamp with time zone' THEN
        RAISE EXCEPTION 'Unexpected trips timestamp type';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'route_counts'
          AND column_name = 'trip_count'
    ) THEN
        RAISE EXCEPTION 'Unexpected route_counts schema';
    END IF;

    IF EXISTS (
        SELECT 1 FROM trips
        WHERE ended_at <= started_at
    ) THEN
        RAISE EXCEPTION 'Invalid trip durations detected';
    END IF;
END $$;

-- 2. Normalize station IDs in existing cloud trips
UPDATE trips
SET
    start_station_id = NULLIF(BTRIM(start_station_id), ''),
    end_station_id = NULLIF(BTRIM(end_station_id), '')
WHERE
    start_station_id IS DISTINCT FROM
        NULLIF(BTRIM(start_station_id), '')
    OR end_station_id IS DISTINCT FROM
        NULLIF(BTRIM(end_station_id), '');

-- 3. Add missing trip-duration constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'valid_trip_duration'
          AND conrelid = 'public.trips'::regclass
    ) THEN
        ALTER TABLE public.trips
        ADD CONSTRAINT valid_trip_duration
        CHECK (ended_at > started_at);
    END IF;
END $$;

-- 4. Align route_counts with canonical schema
ALTER TABLE route_counts
    ADD COLUMN IF NOT EXISTS start_station_name TEXT,
    ADD COLUMN IF NOT EXISTS end_station_name TEXT;

ALTER TABLE route_counts
    ALTER COLUMN city SET DEFAULT 'nyc';

-- 5. Ensure all snapshot fields exist
ALTER TABLE station_snapshots
    ADD COLUMN IF NOT EXISTS is_installed BOOLEAN,
    ADD COLUMN IF NOT EXISTS ebikes_available INTEGER,
    ADD COLUMN IF NOT EXISTS bike_types JSONB;

-- 6. Create any missing indexes
CREATE INDEX IF NOT EXISTS idx_trips_start_station_time
ON trips (start_station_id, started_at);

CREATE INDEX IF NOT EXISTS idx_route_counts_trip_count
ON route_counts (trip_count DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_station_time
ON station_snapshots (city, station_id, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_city_time
ON station_snapshots (city, collected_at DESC);

-- 7. Ensure application user has table access
GRANT SELECT, INSERT, UPDATE
ON trips, stations, station_snapshots,
   route_counts, hourly_trip_counts
TO ridepulse_app;

COMMIT;

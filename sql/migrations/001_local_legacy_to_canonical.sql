-- Migrate the existing local RidePulse database.
-- Run ONLY against the legacy local PostgreSQL database.
-- Do not run against Cloud SQL or an already migrated database.

BEGIN;

SET LOCAL lock_timeout = '15s';

-- Safety checks: fail before altering anything if schema is unexpected.
DO $$
BEGIN
    IF current_database() <> 'ridepulse' THEN
        RAISE EXCEPTION 'Wrong database';
    END IF;

    IF (
        SELECT data_type
        FROM information_schema.columns
        WHERE table_name = 'trips'
          AND column_name = 'started_at'
    ) IS DISTINCT FROM 'timestamp without time zone' THEN
        RAISE EXCEPTION 'Trips schema already migrated or unexpected';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'route_counts'
          AND column_name = 'total_trips'
    ) THEN
        RAISE EXCEPTION 'Unexpected route_counts schema';
    END IF;

    IF (SELECT COUNT(*) FROM trips) <> 3860371 THEN
        RAISE EXCEPTION 'Unexpected trip count; migration aborted';
    END IF;
END $$;

-- Add city and convert both timestamps explicitly.
-- Existing timestamps represent New York local time.
ALTER TABLE trips
    ADD COLUMN city TEXT NOT NULL DEFAULT 'nyc',
    ALTER COLUMN rideable_type DROP NOT NULL,
    ALTER COLUMN member_casual DROP NOT NULL,
    ALTER COLUMN started_at TYPE TIMESTAMPTZ
        USING started_at AT TIME ZONE 'America/New_York',
    ALTER COLUMN ended_at TYPE TIMESTAMPTZ
        USING ended_at AT TIME ZONE 'America/New_York';

-- Normalize empty and whitespace-only station IDs.
UPDATE trips
SET
    start_station_id = NULLIF(BTRIM(start_station_id), ''),
    end_station_id = NULLIF(BTRIM(end_station_id), '')
WHERE
    start_station_id IS DISTINCT FROM NULLIF(BTRIM(start_station_id), '')
    OR end_station_id IS DISTINCT FROM NULLIF(BTRIM(end_station_id), '');

-- Preserve and migrate existing route aggregates.
ALTER TABLE route_counts
    RENAME COLUMN total_trips TO trip_count;

ALTER TABLE route_counts
    ADD COLUMN city TEXT NOT NULL DEFAULT 'nyc';

ALTER TABLE route_counts
    DROP CONSTRAINT route_counts_pkey;

ALTER TABLE route_counts
    ADD CONSTRAINT route_counts_pkey
    PRIMARY KEY (city, start_station_id, end_station_id);

-- Remove aggregates referencing invalid station IDs.
DELETE FROM route_counts
WHERE NULLIF(BTRIM(start_station_id), '') IS NULL
   OR NULLIF(BTRIM(end_station_id), '') IS NULL;

DROP INDEX IF EXISTS idx_route_counts_total_trips;

CREATE INDEX IF NOT EXISTS idx_route_counts_trip_count
ON route_counts (trip_count DESC);

-- Verify all historical trip rows were preserved.
DO $$
BEGIN
    IF (SELECT COUNT(*) FROM trips) <> 3860371 THEN
        RAISE EXCEPTION 'Trip count mismatch after migration';
    END IF;
END $$;

COMMIT;

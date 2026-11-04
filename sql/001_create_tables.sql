-- Canonical RidePulse PostgreSQL 16 schema
-- For NEW databases. Existing databases require migrations.

CREATE TABLE IF NOT EXISTS trips (
    ride_id TEXT PRIMARY KEY,
    city TEXT NOT NULL DEFAULT 'nyc',
    rideable_type TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    start_station_name TEXT,
    start_station_id TEXT,
    end_station_name TEXT,
    end_station_id TEXT,
    start_lat DOUBLE PRECISION,
    start_lng DOUBLE PRECISION,
    end_lat DOUBLE PRECISION,
    end_lng DOUBLE PRECISION,
    member_casual TEXT,
    CONSTRAINT valid_trip_duration CHECK (ended_at > started_at)
);

CREATE TABLE IF NOT EXISTS stations (
    city TEXT NOT NULL,
    station_id TEXT NOT NULL,
    station_name TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    capacity INTEGER,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (city, station_id)
);

CREATE TABLE IF NOT EXISTS station_snapshots (
    city TEXT NOT NULL,
    station_id TEXT NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    last_reported TIMESTAMPTZ,
    bikes_available INTEGER,
    docks_available INTEGER,
    is_renting BOOLEAN,
    is_returning BOOLEAN,
    is_installed BOOLEAN,
    ebikes_available INTEGER,
    bike_types JSONB,
    PRIMARY KEY (city, station_id, collected_at)
);

CREATE TABLE IF NOT EXISTS route_counts (
    city TEXT NOT NULL DEFAULT 'nyc',
    start_station_id TEXT NOT NULL,
    end_station_id TEXT NOT NULL,
    start_station_name TEXT,
    end_station_name TEXT,
    trip_count BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (city, start_station_id, end_station_id)
);

CREATE TABLE IF NOT EXISTS hourly_trip_counts (
    city TEXT NOT NULL,
    hour_start TIMESTAMPTZ NOT NULL,
    trip_count BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (city, hour_start)
);

CREATE INDEX IF NOT EXISTS idx_trips_started_at
ON trips (started_at);

CREATE INDEX IF NOT EXISTS idx_trips_start_station_time
ON trips (start_station_id, started_at);

CREATE INDEX IF NOT EXISTS idx_snapshots_station_time
ON station_snapshots (city, station_id, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_city_time
ON station_snapshots (city, collected_at DESC);

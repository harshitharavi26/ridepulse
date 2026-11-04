-- Refresh NYC route aggregates from all currently ingested NYC trips.
-- Requires canonical route_counts schema from 001.

INSERT INTO route_counts (
    city,
    start_station_id,
    end_station_id,
    start_station_name,
    end_station_name,
    trip_count
)
SELECT
    'nyc',
    BTRIM(start_station_id),
    BTRIM(end_station_id),
    MAX(start_station_name),
    MAX(end_station_name),
    COUNT(*)
FROM trips
WHERE city = 'nyc'
  AND NULLIF(BTRIM(start_station_id), '') IS NOT NULL
  AND NULLIF(BTRIM(end_station_id), '') IS NOT NULL
GROUP BY BTRIM(start_station_id), BTRIM(end_station_id)
ON CONFLICT (city, start_station_id, end_station_id)
DO UPDATE SET
    start_station_name = EXCLUDED.start_station_name,
    end_station_name = EXCLUDED.end_station_name,
    trip_count = EXCLUDED.trip_count;

ANALYZE route_counts;

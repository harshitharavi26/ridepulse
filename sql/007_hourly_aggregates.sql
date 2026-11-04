-- Refresh NYC hourly trip counts from all currently ingested NYC trips.
-- Store each hour as an absolute timestamp using New York hour boundaries.

INSERT INTO hourly_trip_counts (city, hour_start, trip_count)
SELECT
    city,
    date_trunc('hour', started_at, 'America/New_York'),
    COUNT(*)
FROM trips
WHERE city = 'nyc'
GROUP BY city, date_trunc('hour', started_at, 'America/New_York')
ON CONFLICT (city, hour_start)
DO UPDATE SET trip_count = EXCLUDED.trip_count;

ANALYZE hourly_trip_counts;

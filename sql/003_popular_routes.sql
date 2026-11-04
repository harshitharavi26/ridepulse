-- Top 10 NYC routes, April 2026

SELECT
    BTRIM(start_station_id) AS start_station_id,
    MAX(start_station_name) AS start_station,
    BTRIM(end_station_id) AS end_station_id,
    MAX(end_station_name) AS end_station,
    COUNT(*) AS total_trips
FROM trips
WHERE city = 'nyc'
  AND NULLIF(BTRIM(start_station_id), '') IS NOT NULL
  AND NULLIF(BTRIM(end_station_id), '') IS NOT NULL
  AND started_at >= (TIMESTAMP '2026-04-01' AT TIME ZONE 'America/New_York')
  AND started_at < (TIMESTAMP '2026-05-01' AT TIME ZONE 'America/New_York')
GROUP BY BTRIM(start_station_id), BTRIM(end_station_id)
ORDER BY total_trips DESC, start_station_id, end_station_id
LIMIT 10;

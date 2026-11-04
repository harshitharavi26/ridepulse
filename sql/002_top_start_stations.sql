-- Top 10 NYC pickup stations, April 2026
-- Exclude NULL and blank station IDs.

SELECT
    BTRIM(start_station_id) AS start_station_id,
    MAX(start_station_name) AS station_name,
    COUNT(*) AS total_departures
FROM trips
WHERE city = 'nyc'
  AND NULLIF(BTRIM(start_station_id), '') IS NOT NULL
  AND started_at >= (TIMESTAMP '2026-04-01' AT TIME ZONE 'America/New_York')
  AND started_at < (TIMESTAMP '2026-05-01' AT TIME ZONE 'America/New_York')
GROUP BY BTRIM(start_station_id)
ORDER BY total_departures DESC, start_station_id
LIMIT 10;

-- NYC trip starts by New York local hour, April 2026
-- Explicit timezone makes results independent of session timezone.

SELECT
    EXTRACT(
        HOUR FROM started_at AT TIME ZONE 'America/New_York'
    )::INTEGER AS hour_of_day,
    COUNT(*) AS total_trips
FROM trips
WHERE city = 'nyc'
  AND started_at >= (TIMESTAMP '2026-04-01' AT TIME ZONE 'America/New_York')
  AND started_at < (TIMESTAMP '2026-05-01' AT TIME ZONE 'America/New_York')
GROUP BY hour_of_day
ORDER BY hour_of_day;

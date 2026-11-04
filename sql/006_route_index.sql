-- Index for fast top-route queries
-- Uses canonical trip_count column.

CREATE INDEX IF NOT EXISTS idx_route_counts_trip_count
ON route_counts (trip_count DESC);

ANALYZE route_counts;

"""Real SQL equivalence and watermark checks in a separate local test schema."""
from datetime import date
import os
from pathlib import Path
import sys
import unittest

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import aggregates
import analytics
from analytics import City,Metric,Granularity,date_range

DSN=os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DSN,"Set TEST_DATABASE_URL for real aggregate equivalence checks")
class AggregateIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        info=conninfo_to_dict(DSN)
        if info.get("dbname")!="ridepulse_test" or info.get("host") not in ("localhost","127.0.0.1"):
            raise RuntimeError("Requires the dedicated local test database")
        with psycopg.connect(DSN) as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS aggregate_fixture")
            conn.execute("SET search_path=aggregate_fixture")
            root=Path(__file__).resolve().parents[1]/"sql"
            for name in ("001_create_tables.sql","008_dataset_versions.sql","010_serving_aggregates.sql"):
                conn.execute((root/name).read_text())

    def connect(self, dictionary=False):
        return psycopg.connect(DSN,options="-c search_path=aggregate_fixture -c timezone=UTC",row_factory=dict_row if dictionary else None)

    def setUp(self):
        with self.connect() as conn:
            conn.execute("TRUNCATE trips,route_counts,hourly_trip_counts,station_hour_counts,aggregate_state")
            conn.execute("""INSERT INTO trips(ride_id,started_at,ended_at,start_station_id,end_station_id,start_station_name,end_station_name)
                VALUES ('a','2026-11-01T05:30Z','2026-11-01T05:40Z','A','B','Zulu','Beta'),
                       ('b','2026-11-01T06:30Z','2026-11-01T06:40Z','A','B','Alpha','Beta'),
                       ('unknown','2026-11-01T07:30Z','2026-11-01T07:40Z',NULL,'B',NULL,'Beta')""")

    def results(self, conn):
        window=date_range(City.nyc,date(2026,11,1),date(2026,11,2))
        return [analytics.leaderboards(conn,City.nyc,window,Metric.pickups,None,20,0),
                analytics.leaderboards(conn,City.nyc,window,Metric.dropoffs,'B',20,0),
                analytics.routes(conn,City.nyc,window,None,None,20,0),
                *[analytics.patterns(conn,City.nyc,window,Metric.pickups,None,g,date(2026,11,1),date(2026,11,2)) for g in Granularity]]

    def test_refresh_matches_facts_including_dst_unknowns_and_filters(self):
        with self.connect(True) as conn:
            before=self.results(conn)
        with self.connect() as conn:
            aggregates.refresh(conn)
        with self.connect(True) as conn:
            self.assertTrue(aggregates.available(conn,City.nyc))
            self.assertEqual(self.results(conn),before)

    def test_incremental_insert_and_duplicate_keep_totals_aligned(self):
        with self.connect() as conn:
            aggregates.refresh(conn)
        with self.connect() as conn:
            self.assertTrue(aggregates.lock_current(conn))
            conn.execute("""CREATE TEMP TABLE inserted_trips ON COMMIT DROP AS WITH added AS (
                INSERT INTO trips(ride_id,started_at,ended_at,start_station_id,end_station_id)
                VALUES('new','2026-11-01T08:00Z','2026-11-01T08:10Z','A','B') RETURNING *) SELECT * FROM added""")
            aggregates.update_from(conn)
        with self.connect(True) as conn:
            self.assertTrue(aggregates.available(conn,City.nyc))
            self.assertEqual(conn.execute("SELECT SUM(trip_count) AS n FROM hourly_trip_counts").fetchone()['n'],4)
            self.assertEqual(conn.execute("SELECT SUM(trip_count) AS n FROM route_counts").fetchone()['n'],3)
        with self.connect() as conn:
            conn.execute("INSERT INTO trips SELECT * FROM trips WHERE ride_id='new' ON CONFLICT DO NOTHING")
        with self.connect(True) as conn:
            self.assertTrue(aggregates.available(conn,City.nyc))

    def test_external_correction_invalidates_until_refresh(self):
        with self.connect() as conn:
            aggregates.refresh(conn)
            conn.execute("DELETE FROM trips WHERE ride_id IN ('a','b')")
        with self.connect(True) as conn:
            self.assertFalse(aggregates.available(conn,City.nyc))
        with self.connect() as conn:
            self.assertFalse(aggregates.lock_current(conn))
            aggregates.refresh(conn)
        with self.connect(True) as conn:
            self.assertTrue(aggregates.available(conn,City.nyc))
            self.assertEqual(conn.execute("SELECT SUM(trip_count) AS n FROM route_counts WHERE trip_count>0").fetchone()['n'],None)

    def test_failed_refresh_preserves_previous_aggregate_snapshot(self):
        with self.connect() as conn:
            aggregates.refresh(conn)
        with self.connect() as conn:
            conn.execute("DELETE FROM station_hour_counts")
            conn.rollback()
        with self.connect(True) as conn:
            self.assertTrue(aggregates.available(conn,City.nyc))
            self.assertEqual(sum(r['trip_count'] for r in analytics.patterns(conn,City.nyc,date_range(City.nyc,date(2026,11,1),date(2026,11,2)),Metric.pickups,None,Granularity.hour,date(2026,11,1),date(2026,11,2))),3)

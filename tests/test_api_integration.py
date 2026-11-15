"""Real PostgreSQL correctness tests, restricted to a dedicated local test DB.

TEST_DATABASE_URL=postgresql://ridepulse_test:local_test_only@localhost:55433/ridepulse_test
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi.testclient import TestClient
from api import app

DSN = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DSN, "Set TEST_DATABASE_URL for the isolated local PostgreSQL tests")
class ApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = conninfo_to_dict(DSN)
        if parsed.get("dbname") != "ridepulse_test" or parsed.get("host") not in ("localhost", "127.0.0.1", "test-postgres"):
            raise RuntimeError("Integration fixtures require the dedicated local ridepulse_test database")
        cls.env = patch.dict(os.environ, {"DATABASE_URL": DSN})
        cls.env.start()
        with psycopg.connect(DSN) as conn:
            # Only this dedicated disposable database is initialized/truncated.
            conn.execute((Path(__file__).resolve().parents[1] / "sql/001_create_tables.sql").read_text())
            conn.execute((Path(__file__).resolve().parents[1] / "sql/008_dataset_versions.sql").read_text())
            conn.execute("TRUNCATE trips, stations, station_snapshots, route_counts, hourly_trip_counts")
            rows = [
                ("one", "2026-04-01T03:59:00Z", "2026-04-01T04:01:00Z", "A", "B"),
                ("two", "2026-04-01T04:00:00Z", "2026-04-01T04:10:00Z", "A", "B"),
                ("three", "2026-04-01T12:00:00Z", "2026-04-01T12:10:00Z", "A", "C"),
                ("four", "2026-04-01T12:30:00Z", "2026-04-01T12:40:00Z", "B", "A"),
                ("missing", "2026-04-01T13:00:00Z", "2026-04-01T13:05:00Z", None, "A"),
                ("next", "2026-04-02T04:00:00Z", "2026-04-02T04:10:00Z", "C", "A"),
                ("fold-one", "2026-11-01T05:30:00Z", "2026-11-01T05:40:00Z", "A", "B"),
                ("fold-two", "2026-11-01T06:30:00Z", "2026-11-01T06:40:00Z", "A", "B"),
            ]
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO trips (ride_id, started_at, ended_at, start_station_id,
                    end_station_id, start_station_name, end_station_name)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""", [(*r, r[3], r[4]) for r in rows])
                cur.executemany("INSERT INTO stations(city, station_id, station_name) VALUES (%s,%s,%s)",
                                [("nyc", "A", "Current A"), ("boulder", "A", "Boulder A")])
                now = datetime.now(timezone.utc)
                snapshots = [
                    ("nyc", "A", now - timedelta(hours=1), now - timedelta(hours=1), 0, True),
                    ("nyc", "A", now - timedelta(seconds=10), now - timedelta(seconds=20), 5, True),
                    ("nyc", "B", now - timedelta(hours=2), None, 1, True),
                    ("boulder", "A", now - timedelta(seconds=10), now - timedelta(hours=2), 8, True),
                    ("boulder", "low", "2026-04-01T12:00:00Z", "2026-04-01T12:00:00Z", 0, True),
                    ("boulder", "low", "2026-04-01T12:05:00Z", "2026-04-01T12:05:00Z", 3, True),
                    ("boulder", "low", "2026-04-01T12:10:00Z", "2026-04-01T12:10:00Z", None, True),
                    ("boulder", "low", "2026-04-01T12:15:00Z", "2026-04-01T12:15:00Z", 0, False),
                ]
                cur.executemany("""INSERT INTO station_snapshots(city,station_id,collected_at,last_reported,
                    bikes_available,is_renting,is_installed,is_returning,docks_available)
                    VALUES (%s,%s,%s,%s,%s,%s,true,true,4)""", snapshots)
        cls.client_ctx = TestClient(app)
        cls.client = cls.client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        cls.env.stop()

    def request(self, path, **extra):
        params = dict(city="nyc", start_date="2026-04-01", end_date="2026-04-02")
        result = self.client.get(path, params={**params, **extra})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_health_and_read_only_pool(self):
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        with app.state.pool.connection() as conn:
            self.assertEqual(conn.execute("SHOW default_transaction_read_only").fetchone()["default_transaction_read_only"], "on")
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("DELETE FROM trips WHERE false")
            conn.rollback()

    def test_pickups_and_end_exclusive_midnight(self):
        result = self.request("/v1/trips/leaderboard")
        self.assertEqual([(r["station_id"], r["trip_count"]) for r in result["data"]], [("A", 2), ("B", 1)])
        boundary = datetime.fromisoformat(result["meta"]["range"]["start_inclusive"].replace("Z", "+00:00"))
        self.assertEqual(boundary, datetime(2026, 4, 1, 4, tzinfo=timezone.utc))

    def test_dropoffs_filter_on_arrival(self):
        result = self.request("/v1/trips/leaderboard", metric="dropoffs")
        self.assertEqual({r["station_id"]: r["trip_count"] for r in result["data"]}, {"A": 2, "B": 2, "C": 1})
        self.assertEqual(result["meta"]["event_time"], "ended_at")

    def test_station_filter_and_injection_string(self):
        self.assertEqual(self.request("/v1/trips/leaderboard", station_id="B")["data"][0]["trip_count"], 1)
        self.assertEqual(self.request("/v1/trips/leaderboard", station_id="A' OR true --")["data"], [])

    def test_routes_dates_pairs_and_filters(self):
        rows = self.request("/v1/trips/routes")["data"]
        self.assertEqual(sum(r["trip_count"] for r in rows), 3)
        self.assertEqual(len(self.request("/v1/trips/routes", start_station_id="A", end_station_id="B")["data"]), 1)

    def test_patterns_and_zero_fill(self):
        rows = self.request("/v1/trips/patterns", granularity="hour")["data"]
        self.assertEqual(len(rows), 24)
        self.assertEqual(sum(r["trip_count"] for r in rows), 4)  # Includes unknown station.
        self.assertEqual(rows[8]["trip_count"], 2)
        days = self.request("/v1/trips/patterns", granularity="day", end_date="2026-04-04")["data"]
        self.assertEqual([r["trip_count"] for r in days], [4, 1, 0])
        weekday = self.request("/v1/trips/patterns", granularity="weekday")["data"]
        self.assertEqual(weekday[2], {"bucket": 3, "trip_count": 4})

    def test_fall_dst_both_repeated_hours_count(self):
        rows = self.request("/v1/trips/patterns", granularity="hour", start_date="2026-11-01", end_date="2026-11-02")["data"]
        self.assertEqual(rows[1]["trip_count"], 2)
        self.assertEqual(sum(r["trip_count"] for r in rows), 2)

    def test_pagination_is_stable(self):
        first = self.request("/v1/trips/leaderboard", limit=1)
        self.assertEqual(first["meta"]["next_offset"], 1)
        second = self.request("/v1/trips/leaderboard", limit=1, offset=1)
        self.assertEqual(second["data"][0]["station_id"], "B")
        self.assertIsNone(second["meta"]["next_offset"])

    def test_historical_station_list_survives_missing_metadata(self):
        rows = self.request("/v1/stations", source="trips")["data"]
        self.assertEqual([r["station_id"] for r in rows], ["A", "B", "C"])

    def test_latest_per_station_and_upstream_freshness(self):
        rows = {r["station_id"]: r for r in self.request("/v1/availability/latest")["data"]}
        self.assertEqual(rows["A"]["bikes_available"], 5)
        self.assertFalse(rows["A"]["is_stale"])
        self.assertTrue(rows["B"]["is_stale"])
        self.assertFalse(rows["B"]["upstream_timestamp_known"])
        row = self.request("/v1/availability/latest", city="boulder", station_id="A")["data"][0]
        self.assertTrue(row["is_stale"])  # Recently collected, old upstream status.

    def test_latest_pagination_keeps_stations_without_metadata(self):
        first=self.request('/v1/availability/latest',limit=1)
        second=self.request('/v1/availability/latest',limit=1,offset=1)
        self.assertEqual(first['data'][0]['station_id'],'A')
        self.assertEqual(first['meta']['next_offset'],1)
        self.assertEqual(second['data'][0]['station_id'],'B')
        self.assertIsNone(second['data'][0]['station_name'])
        self.assertIsNone(second['meta']['next_offset'])

    def test_low_samples_exclude_unknown_counts_and_closed_stations(self):
        row = self.request("/v1/availability/low", city="boulder", station_id="low")["data"][0]
        self.assertEqual(row["observed_samples"], 4)
        self.assertEqual(row["eligible_samples"], 2)
        self.assertEqual(row["low_samples"], 1)
        self.assertEqual(row["low_sample_fraction"], 0.5)


@unittest.skipUnless(DSN,"Set TEST_DATABASE_URL for API schema isolation")
class ApiSchemaIsolation(unittest.TestCase):
    def test_schema_option_is_preserved_alongside_read_only_and_timeout(self):
        info=conninfo_to_dict(DSN)
        if info.get('dbname')!='ridepulse_test' or info.get('host') not in ('localhost','127.0.0.1'):
            raise RuntimeError('Requires the dedicated local test database')
        with psycopg.connect(DSN) as conn:
            root=Path(__file__).resolve().parents[1]/'sql'
            conn.execute((root/'001_create_tables.sql').read_text())
            conn.execute((root/'008_dataset_versions.sql').read_text())
            conn.execute('CREATE SCHEMA IF NOT EXISTS api_schema_fixture')
            conn.execute('SET search_path=api_schema_fixture')
            conn.execute((root/'001_create_tables.sql').read_text())
            conn.execute((root/'008_dataset_versions.sql').read_text())
            conn.execute('TRUNCATE trips')
            conn.execute("""INSERT INTO trips(ride_id,started_at,ended_at,start_station_id,end_station_id)
                VALUES('private-schema-trip','2026-04-01T12:00Z','2026-04-01T12:10Z','PRIVATE','PRIVATE')""")
            # Equal version watermarks in two schemas must still use different
            # cache namespaces. This models a schema cloned from a backup.
            conn.execute('DELETE FROM dataset_versions')
            conn.execute('INSERT INTO dataset_versions SELECT * FROM public.dataset_versions')
        redis_url=os.getenv('TEST_REDIS_URL','')
        params={'city':'nyc','start_date':'2026-04-01','end_date':'2026-04-02'}
        with patch.dict(os.environ,{'DATABASE_URL':DSN,'DB_SCHEMA':'public','REDIS_URL':redis_url}):
            with TestClient(app) as client:
                client.get('/v1/trips/leaderboard',params=params).raise_for_status()
                warmed=client.get('/v1/trips/leaderboard',params=params)
                self.assertNotIn('PRIVATE',[row['station_id'] for row in warmed.json()['data']])
                if redis_url:self.assertEqual(warmed.json()['meta']['cache'],'hit')
        with patch.dict(os.environ,{'DATABASE_URL':DSN,'DB_SCHEMA':'api_schema_fixture','REDIS_URL':redis_url}):
            with TestClient(app) as client:
                result=client.get('/v1/trips/leaderboard',params=params)
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual([(row['station_id'],row['trip_count']) for row in result.json()['data']],[('PRIVATE',1)])
                with app.state.pool.connection() as conn:
                    self.assertEqual(conn.execute('SHOW search_path').fetchone()['search_path'],'api_schema_fixture')
                    self.assertEqual(conn.execute('SHOW default_transaction_read_only').fetchone()['default_transaction_read_only'],'on')


if __name__ == "__main__":
    unittest.main()

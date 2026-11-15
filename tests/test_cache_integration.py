"""Real Redis + PostgreSQL invalidation and fallback checks, no cloud access."""
import os
from pathlib import Path
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlparse

import psycopg
from psycopg.conninfo import conninfo_to_dict
from fastapi.testclient import TestClient
import redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from api import app

DSN, REDIS = os.getenv("TEST_DATABASE_URL"), os.getenv("TEST_REDIS_URL")


@unittest.skipUnless(DSN and REDIS, "Set TEST_DATABASE_URL and TEST_REDIS_URL for real cache tests")
class CacheIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed, cache = conninfo_to_dict(DSN), urlparse(REDIS)
        if parsed.get("dbname") != "ridepulse_test" or parsed.get("host") not in ("localhost", "127.0.0.1"):
            raise RuntimeError("Cache tests require the local ridepulse_test database")
        if cache.hostname not in ("localhost", "127.0.0.1") or cache.path != "/15":
            raise RuntimeError("Cache tests require reserved local Redis database 15")
        with psycopg.connect(DSN) as conn:
            root = Path(__file__).resolve().parents[1]
            conn.execute((root / "sql/001_create_tables.sql").read_text())
            conn.execute((root / "sql/008_dataset_versions.sql").read_text())

    def setUp(self):
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM trips WHERE ride_id LIKE 'cache-%%'")
            conn.execute("DELETE FROM station_snapshots WHERE station_id = 'cache-live'")
            conn.execute("""INSERT INTO trips(ride_id, started_at, ended_at, start_station_id,end_station_id)
                VALUES ('cache-one','2027-01-01T12:00Z','2027-01-01T12:10Z','cache-A','cache-B')""")
        self.env = patch.dict(os.environ, {"DATABASE_URL": DSN, "REDIS_URL": REDIS})
        self.env.start()
        self.ctx = TestClient(app)
        self.client = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        self.env.stop()

    def query(self, **extra):
        result = self.client.get("/v1/trips/leaderboard", params={
            "city": "nyc", "start_date": "2027-01-01", "end_date": "2027-01-02", **extra})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def version(self, domain="trips"):
        with psycopg.connect(DSN) as conn:
            return conn.execute("SELECT version FROM dataset_versions WHERE city='nyc' AND domain=%s", [domain]).fetchone()[0]

    def test_hit_and_invalidation_after_new_commit(self):
        self.assertEqual(self.query()["meta"]["cache"], "miss")
        self.assertEqual(self.query()["meta"]["cache"], "hit")
        with psycopg.connect(DSN) as conn:
            conn.execute("""INSERT INTO trips(ride_id, started_at, ended_at, start_station_id,end_station_id)
                VALUES ('cache-two','2027-01-01T13:00Z','2027-01-01T13:10Z','cache-A','cache-B')""")
        changed = self.query()
        self.assertEqual(changed["meta"]["cache"], "miss")
        self.assertEqual(changed["data"][0]["trip_count"], 2)

    def test_duplicate_and_identical_update_do_not_advance_version(self):
        self.query()
        before = self.version()
        with psycopg.connect(DSN) as conn:
            conn.execute("INSERT INTO trips SELECT * FROM trips WHERE ride_id='cache-one' ON CONFLICT DO NOTHING")
            conn.execute("UPDATE trips SET start_station_id=start_station_id WHERE ride_id='cache-one'")
        self.assertEqual(self.version(), before)
        self.assertEqual(self.query()["meta"]["cache"], "hit")

    def test_rollback_does_not_advance_version_or_invalidate(self):
        self.query()
        before = self.version()
        with psycopg.connect(DSN) as conn:
            conn.execute("UPDATE trips SET start_station_id='changed' WHERE ride_id='cache-one'")
            conn.rollback()
        self.assertEqual(self.version(), before)
        self.assertEqual(self.query()["meta"]["cache"], "hit")

    def test_delete_invalidates_and_separate_filters_have_separate_keys(self):
        self.query()
        filtered = self.query(station_id="cache-B")
        self.assertEqual(filtered["meta"]["cache"], "miss")
        self.assertEqual(filtered["data"], [])
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM trips WHERE ride_id='cache-one'")
        changed = self.query()
        self.assertEqual(changed["meta"]["cache"], "miss")
        self.assertEqual(changed["data"], [])

    def test_redis_failure_falls_back_to_database(self):
        with patch.object(app.state.cache.client, "get", side_effect=redis.ConnectionError("fixture outage")):
            result = self.query()
        self.assertEqual(result["meta"]["cache"], "fallback")
        self.assertEqual(result["data"][0]["trip_count"], 1)

    def test_corrupt_cache_falls_back(self):
        self.query()
        with patch.object(app.state.cache.client, "get", return_value="{bad-json"):
            result = self.query()
        self.assertEqual(result["meta"]["cache"], "fallback")
        self.assertEqual(result["data"][0]["trip_count"], 1)

    def test_freshness_recomputed_on_cache_hit(self):
        now = datetime.now(timezone.utc)
        with psycopg.connect(DSN) as conn:
            conn.execute("""INSERT INTO station_snapshots(city,station_id,collected_at,last_reported,bikes_available)
                VALUES ('nyc','cache-live',%s,%s,4)""", [now-timedelta(seconds=45)]*2)
        params = dict(city="nyc", station_id="cache-live", freshness_seconds=60)
        first = self.client.get("/v1/availability/latest", params=params).json()
        self.assertFalse(first["data"][0]["is_stale"])
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return now + timedelta(seconds=30)
        with patch("api.datetime", Later):
            second = self.client.get("/v1/availability/latest", params=params).json()
        self.assertEqual(second["meta"]["cache"], "hit")
        self.assertTrue(second["data"][0]["is_stale"])

    def test_metadata_replay_does_not_advance_version(self):
        with psycopg.connect(DSN) as conn:
            conn.execute("INSERT INTO stations(city,station_id,station_name) VALUES ('nyc','cache-meta','Test') ON CONFLICT DO NOTHING")
        before = self.version("metadata")
        with psycopg.connect(DSN) as conn:
            conn.execute("""INSERT INTO stations SELECT * FROM stations WHERE city='nyc' AND station_id='cache-meta'
                ON CONFLICT(city,station_id) DO UPDATE SET station_name=EXCLUDED.station_name, updated_at=EXCLUDED.updated_at""")
        self.assertEqual(self.version("metadata"), before)

    def test_redis_unavailable_is_degraded_readiness(self):
        with patch.object(app.state.cache.client, "ping", side_effect=redis.ConnectionError("fixture outage")):
            result = self.client.get("/health/ready")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["cache"], "unavailable")

    def test_bypass_mode_preserves_versions_without_using_redis(self):
        self.query()
        app.state.cache.bypass=True
        with patch.object(app.state.cache.client,'get',side_effect=AssertionError('Must bypass Redis')):
            result=self.query()
        self.assertEqual(result['meta']['cache'],'bypass')
        self.assertTrue(result['meta']['dataset_versions'])
        self.assertEqual(result['data'][0]['trip_count'],1)

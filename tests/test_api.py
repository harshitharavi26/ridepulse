"""Contract checks requiring no external services."""
import sys
import unittest
from datetime import date
from pathlib import Path
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi import HTTPException
from fastapi.testclient import TestClient
from api import app, get_connection
from analytics import City, date_range
from query_cache import QueryCache


class ApiContracts(unittest.TestCase):
    def setUp(self):
        # Invalid requests must not execute SQL.
        app.dependency_overrides[get_connection] = lambda: None
        app.state.cache = QueryCache()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_liveness_and_openapi_without_database(self):
        self.assertEqual(self.client.get("/health/live").json(), {"status": "alive"})
        spec = self.client.get("/openapi.json").json()
        self.assertIn("/v1/availability/low", spec["paths"])
        self.assertIn("X-Request-ID", self.client.get("/health/live").headers)

    def test_invalid_inputs_and_unsupported_city(self):
        base = dict(city="nyc", start_date="2026-04-01", end_date="2026-05-01")
        for change in (dict(city="other"), dict(city="boulder"), dict(limit=201),
                       dict(offset=-1), dict(start_date="nonsense"), dict(metric="sql"),
                       dict(station_id=" "), dict(end_date="2026-04-01"),
                       dict(end_date="2028-04-01")):
            with self.subTest(change=change):
                result = self.client.get("/v1/trips/leaderboard", params={**base, **change})
                self.assertEqual(result.status_code, 422, result.text)

    def test_city_local_dst_boundaries(self):
        for city in City:
            spring = date_range(city, date(2026, 3, 8), date(2026, 3, 9))
            autumn = date_range(city, date(2026, 11, 1), date(2026, 11, 2))
            self.assertEqual((spring.end - spring.start).total_seconds(), 23 * 3600)
            self.assertEqual((autumn.end - autumn.start).total_seconds(), 25 * 3600)

    def test_range_limits(self):
        with self.assertRaises(HTTPException):
            date_range(City.nyc, date(2026, 4, 1), date(2026, 3, 1))

    def test_database_errors_are_sanitized_and_retryable(self):
        def unavailable():
            raise psycopg.OperationalError("private-connection-details")
        app.dependency_overrides[get_connection] = unavailable
        result = self.client.get("/health/ready")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.headers["Retry-After"], "5")
        self.assertNotIn("private-connection-details", result.text)
        self.assertEqual(self.client.get("/health/live").status_code, 200)


if __name__ == "__main__":
    unittest.main()

"""Streamlit render checks with controlled API responses."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

SCRIPT = str(Path(__file__).resolve().parents[1] / "src/dashboard.py")


def api_response(url, **kwargs):
    if url.endswith("/v1/cities"):
        payload = {"data": [{"city": "nyc", "trip_coverage": {"first_event_at": None}}]}
    elif url.endswith("/v1/availability/latest"):
        payload = {"data": [{"station_id": "test", "station_name": "Test station",
                             "bikes_available": 1, "docks_available": 3, "is_stale": True,
                             "collected_at": "2026-04-01T12:00Z", "last_reported": None,
                             "is_renting": True, "is_returning": True,
                             "latitude": None, "longitude": None}]}
    elif url.endswith("/v1/availability/coverage"):
        payload = {"data": [],"meta":{"latest_local_date":"2026-04-01"}}
    else:
        payload = {"data": []}
    response = Mock(status_code=200)
    response.json.return_value = payload
    return response


class DashboardTests(unittest.TestCase):
    def test_empty_history_has_explanation(self):
        with patch("requests.get", side_effect=api_response):
            at = AppTest.from_file(SCRIPT, default_timeout=15).run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("No NYC trip history" in item.value for item in at.info))

    def test_boulder_is_not_presented_as_historical_trips(self):
        with patch("requests.get", side_effect=api_response):
            at = AppTest.from_file(SCRIPT, default_timeout=15).run()
            at.sidebar.selectbox[0].set_value("boulder").run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("case study" in item.value for item in at.info))

    def test_availability_shows_stale_readings(self):
        with patch("requests.get", side_effect=api_response):
            at = AppTest.from_file(SCRIPT, default_timeout=15).run()
            at.sidebar.radio[0].set_value("Station availability").run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("stale" in item.value for item in at.warning))
        self.assertEqual(len(at.dataframe), 1)

    def test_database_unavailable_has_readable_error(self):
        with patch("requests.get", return_value=Mock(status_code=503)):
            at = AppTest.from_file(SCRIPT, default_timeout=15).run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("database may be paused" in item.value for item in at.error))

    def test_low_date_defaults_to_stored_date_not_wall_clock(self):
        with patch("requests.get", side_effect=api_response):
            at = AppTest.from_file(SCRIPT, default_timeout=15).run()
            at.sidebar.radio[0].set_value("Station availability").run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(at.date_input[0].value.isoformat(),"2026-04-01")

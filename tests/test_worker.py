import base64
import csv
import io
import json
import sys
import subprocess
import unittest
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import os
os.environ["RIDEPULSE_BUCKET"] = "ridepulse-dcs-2026-data-42502534899"
os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
from worker import app, clean_text, parse_timestamp


class FakeCopy:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def write_row(self, row):
        self.rows.append(row)


class FakeCursor:
    def __init__(self):
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        if "INSERT INTO" in sql:
            self.rowcount = len(self.rows)

    def copy(self, sql):
        return FakeCopy(self.rows)


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cur


def sample_csv():
    output = io.StringIO()
    fields = [
        "ride_id", "rideable_type", "started_at",
        "ended_at", "start_station_id", "end_station_id",
        "member_casual"
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    writer.writerow({
        "ride_id": "valid-1",
        "started_at": "2026-04-01 08:00:00",
        "ended_at": "2026-04-01 08:15:00",
        "start_station_id": "   ",
        "end_station_id": "123",
    })

    writer.writerow({
        "ride_id": "invalid-1",
        "started_at": "2026-04-01 09:00:00",
        "ended_at": "2026-04-01 08:00:00",
        "start_station_id": "456",
        "end_station_id": "789",
    })

    writer.writerow({
        "ride_id": "valid-2",
        "started_at": "2026-04-01 10:00:00",
        "ended_at": "2026-04-01 10:20:00",
        "start_station_id": "456",
        "end_station_id": "",
    })

    return output.getvalue()


def request_body():
    task = {
        "task_type": "trip_batch",
        "city": "nyc",
        "bucket": "ridepulse-dcs-2026-data-42502534899",
        "object": "raw/citibike/test.csv",
    }
    encoded = base64.b64encode(
        json.dumps(task).encode()
    ).decode()
    return {"message": {"data": encoded}}


class WorkerTests(unittest.TestCase):

    def test_timestamp_timezone(self):
        dt = parse_timestamp("2026-04-01 08:00:00")
        self.assertEqual(dt.astimezone(timezone.utc).hour, 12)

    def test_blank_station_id(self):
        self.assertIsNone(clean_text("   "))
        self.assertIsNone(clean_text(""))
        self.assertEqual(clean_text(" 123 "), "123")

    def test_mixed_valid_and_invalid_rows(self):
        conn = FakeConnection()

        with patch("worker.storage.Client") as client, \
             patch("worker.database_connection", return_value=conn):

            blob = client.return_value.bucket.return_value.blob.return_value
            blob.open.return_value.__enter__.return_value = io.StringIO(
                sample_csv()
            )

            response = app.test_client().post(
                "/", json=request_body()
            )

        self.assertEqual(response.status_code, 200, response.get_json())

        result = response.get_json()

        self.assertEqual(result["rows_read"], 3)
        self.assertEqual(result["rows_valid"], 2)
        self.assertEqual(result["rows_rejected"], 1)
        self.assertEqual(result["rows_inserted"], 2)

        # start_station_id and end_station_id
        self.assertIsNone(conn.cur.rows[0][6])
        self.assertIsNone(conn.cur.rows[1][8])

    def test_missing_database_configuration(self):
        with patch("worker.storage.Client") as client, \
             patch(
                 "worker.database_connection",
                 side_effect=RuntimeError("Missing configuration")
             ):

            blob = client.return_value.bucket.return_value.blob.return_value
            blob.open.return_value.__enter__.return_value = io.StringIO(
                sample_csv()
            )

            response = app.test_client().post(
                "/", json=request_body()
            )

        self.assertEqual(response.status_code, 500)


    def test_permanent_trip_rejections_are_acknowledged(self):
        for content in ("wrong,columns\n1,2\n", sample_csv().splitlines()[0] + "\n"):
            with self.subTest(content=content), patch("worker.storage.Client") as client, \
                 patch("worker.database_connection", return_value=FakeConnection()):
                blob = client.return_value.bucket.return_value.blob.return_value
                blob.open.return_value.__enter__.return_value = io.StringIO(content)
                response = app.test_client().post("/", json=request_body())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["status"], "rejected")

    def test_bad_message_is_acknowledged(self):
        response = app.test_client().post("/", json={"message": {"data": "!"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "rejected")

    def test_worker_requires_cloud_configuration(self):
        from worker import required_environment
        for key in ("GOOGLE_CLOUD_PROJECT", "RIDEPULSE_BUCKET"):
            with self.subTest(key=key), patch.dict(os.environ, {key: " "}):
                with self.assertRaisesRegex(RuntimeError, key):
                    required_environment("GOOGLE_CLOUD_PROJECT", "RIDEPULSE_BUCKET")

    def test_entry_points_fail_clearly_without_cloud_environment(self):
        root = Path(__file__).resolve().parents[1]
        for target in ("src/worker.py", "scripts/collect_gbfs_cloud.py"):
            with self.subTest(target=target):
                env = dict(os.environ)
                env.pop("GOOGLE_CLOUD_PROJECT", None)
                env.pop("RIDEPULSE_BUCKET", None)
                result = subprocess.run(
                    [sys.executable, str(root / target)], env=env,
                    capture_output=True, text=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Missing required configuration", result.stderr)
                self.assertIn("GOOGLE_CLOUD_PROJECT", result.stderr)
                self.assertIn("RIDEPULSE_BUCKET", result.stderr)

    def gbfs_request(self, payload, task_type="station_status", connection_error=None):
        task = {
            "task_type": task_type, "city": "nyc",
            "bucket": os.environ["RIDEPULSE_BUCKET"],
            "object": f"raw/gbfs/nyc/20261104T120000000000Z_{task_type}.json",
        }
        conn = FakeConnection()
        with patch("gbfs_ingest.storage.Client") as client, \
             patch("worker.database_connection", return_value=conn,
                   side_effect=connection_error):
            client.return_value.bucket.return_value.blob.return_value.download_as_bytes.return_value = json.dumps(payload).encode()
            response = app.test_client().post("/", json={"message": {
                "data": base64.b64encode(json.dumps(task).encode()).decode()
            }})
        return response, conn

    def test_gbfs_skips_invalid_stations_in_both_feeds(self):
        for feed in ("station_status", "station_information"):
            with self.subTest(feed=feed):
                response, conn = self.gbfs_request({"data": {"stations": [
                    {"station_id": "valid", "num_bikes_available": 2, "capacity": 3},
                    {"station_id": "negative", "num_bikes_available": -1, "capacity": -1},
                    {"num_bikes_available": 4}, None,
                ]}}, feed)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.get_json()["status"], "committed")
                self.assertEqual(response.get_json()["rows_valid"], 1)
                self.assertEqual(response.get_json()["rows_rejected"], 3)
                self.assertEqual(len(conn.cur.rows), 1)

    def test_gbfs_invalid_files_are_acknowledged(self):
        for payload in ([], {"data": []}, {"data": {"stations": []}},
                        {"data": {"stations": [{"station_id": "bad", "num_bikes_available": -1}]}}):
            with self.subTest(payload=payload):
                response, _ = self.gbfs_request(payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["status"], "rejected")

    def test_gbfs_configuration_and_database_failures_remain_retryable(self):
        for error in (RuntimeError("Missing database configuration"),
                      ConnectionError("Database unavailable")):
            with self.subTest(error=error):
                response, _ = self.gbfs_request(
                    {"data": {"stations": [{"station_id": "valid"}]}},
                    connection_error=error)
                self.assertEqual(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()

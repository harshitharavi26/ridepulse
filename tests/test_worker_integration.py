"""Storage is mocked; COPY, transactions, constraints and races use real PostgreSQL."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("RIDEPULSE_BUCKET", "ridepulse-dcs-2026-data-42502534899")
import worker
import aggregates

DSN = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DSN, "Set TEST_DATABASE_URL for real ingestion transaction tests")
class WorkerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = conninfo_to_dict(DSN)
        if parsed.get("dbname") != "ridepulse_test" or parsed.get("host") not in ("localhost", "127.0.0.1", "test-postgres"):
            raise RuntimeError("Worker fixtures require the dedicated local ridepulse_test database")
        with psycopg.connect(DSN) as conn:
            conn.execute((Path(__file__).resolve().parents[1] / "sql/001_create_tables.sql").read_text())
            conn.execute((Path(__file__).resolve().parents[1] / "sql/008_dataset_versions.sql").read_text())

    def task(self):
        payload = dict(task_type="trip_batch", city="nyc", bucket=worker.EXPECTED_BUCKET,
                       object="raw/citibike/integration.csv")
        return {"message": {"data": base64.b64encode(json.dumps(payload).encode()).decode()}}

    def source(self):
        return "ride_id,started_at,ended_at,start_station_id,end_station_id\n" + "\n".join(
            f"integration-{i},2026-04-03 08:00:00,2026-04-03 08:10:00,A,B" for i in range(3))

    def setUp(self):
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM trips WHERE ride_id LIKE 'integration-%%'")
        self.env = patch.dict(os.environ, {"DATABASE_URL": DSN})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def invoke(self):
        return worker.app.test_client().post("/", json=self.task())

    def count(self):
        with psycopg.connect(DSN) as conn:
            return conn.execute("SELECT COUNT(*) FROM trips WHERE ride_id LIKE 'integration-%%'").fetchone()[0]

    def version(self):
        with psycopg.connect(DSN) as conn:
            return conn.execute("SELECT version FROM dataset_versions WHERE city='nyc' AND domain='trips'").fetchone()[0]

    def test_copy_and_duplicate_delivery(self):
        before = self.version()
        with patch("worker.storage.Client") as client:
            blob = client.return_value.bucket.return_value.blob.return_value
            blob.open.side_effect = lambda *a, **kw: io.StringIO(self.source())
            first, replay = self.invoke(), self.invoke()
        self.assertEqual(first.json["rows_inserted"], 3)
        self.assertEqual(replay.json["rows_inserted"], 0)
        self.assertEqual(replay.json["duplicates_skipped"], 3)
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.version(), before + 1)

    def test_concurrent_deliveries_insert_once(self):
        before = self.version()
        with patch("worker.storage.Client") as client:
            blob = client.return_value.bucket.return_value.blob.return_value
            blob.open.side_effect = lambda *a, **kw: io.StringIO(self.source())
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda _: self.invoke(), range(4)))
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(sum(r.json["rows_inserted"] for r in results), 3)
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.version(), before + 1)

    def test_database_failure_rolls_back_and_retry_recovers(self):
        with psycopg.connect(DSN) as conn:
            conn.execute("""CREATE OR REPLACE FUNCTION integration_failure() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.ride_id = 'integration-1' THEN RAISE EXCEPTION 'controlled fixture failure'; END IF; RETURN NEW; END $$""")
            conn.execute("CREATE TRIGGER integration_failure BEFORE INSERT ON trips FOR EACH ROW EXECUTE FUNCTION integration_failure()")
        try:
            with patch("worker.storage.Client") as client:
                blob = client.return_value.bucket.return_value.blob.return_value
                blob.open.side_effect = lambda *a, **kw: io.StringIO(self.source())
                failed = self.invoke()
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(self.count(), 0)
        finally:
            with psycopg.connect(DSN) as conn:
                conn.execute("DROP TRIGGER integration_failure ON trips")
                conn.execute("DROP FUNCTION integration_failure()")
        with patch("worker.storage.Client") as client:
            blob = client.return_value.bucket.return_value.blob.return_value
            blob.open.side_effect = lambda *a, **kw: io.StringIO(self.source())
            self.assertEqual(self.invoke().json["rows_inserted"], 3)
        self.assertEqual(self.count(), 3)

    def test_commit_then_lost_response_replay_is_safe(self):
        class LostResponse(io.StringIO):
            def __exit__(self, *args):
                super().__exit__(*args)
                raise ConnectionError("Simulated failure after database commit")
        with patch("worker.storage.Client") as client:
            blob = client.return_value.bucket.return_value.blob.return_value
            blob.open.return_value = LostResponse(self.source())
            failed = self.invoke()
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(self.count(), 3)
            blob.open.side_effect = lambda *a, **kw: io.StringIO(self.source())
            replay = self.invoke()
        self.assertEqual(replay.json["rows_inserted"], 0)
        self.assertEqual(self.count(), 3)

    def test_transactional_aggregates_under_concurrent_duplicates(self):
        with psycopg.connect(DSN) as conn:
            root=Path(__file__).resolve().parents[1]
            conn.execute((root/'sql/010_serving_aggregates.sql').read_text())
            aggregates.refresh(conn)
        with patch.dict(os.environ,{'RIDEPULSE_MAINTAIN_AGGREGATES':'true'}), patch('worker.storage.Client') as client:
            blob=client.return_value.bucket.return_value.blob.return_value
            blob.open.side_effect=lambda *a,**kw:io.StringIO(self.source())
            with ThreadPoolExecutor(max_workers=4) as executor:
                results=list(executor.map(lambda _:self.invoke(),range(4)))
        self.assertTrue(all(r.status_code==200 for r in results))
        self.assertEqual(sum(r.json['rows_inserted'] for r in results),3)
        with psycopg.connect(DSN) as conn:
            total=conn.execute('SELECT COUNT(*) FROM trips').fetchone()[0]
            hourly=conn.execute('SELECT SUM(trip_count) FROM hourly_trip_counts').fetchone()[0]
            station=conn.execute("SELECT SUM(trip_count) FROM station_hour_counts WHERE metric='pickups'").fetchone()[0]
            aligned=conn.execute("SELECT a.valid AND a.source_version=v.version FROM aggregate_state a JOIN dataset_versions v USING(city) WHERE a.city='nyc' AND v.domain='trips'").fetchone()[0]
        self.assertEqual(hourly,total)
        self.assertEqual(station,total)
        self.assertTrue(aligned)

    def test_duplicate_metadata_ids_do_not_fail_the_whole_transaction(self):
        payload={'data':{'stations':[{'station_id':'integration-station','name':'First','capacity':3},
                                     {'station_id':'integration-station','name':'Duplicate','capacity':3}]}}
        task={'task_type':'station_information','city':'boulder','bucket':worker.EXPECTED_BUCKET,
              'object':'raw/gbfs/boulder/20261114T120000000000Z_station_information.json'}
        body={'message':{'data':base64.b64encode(json.dumps(task).encode()).decode()}}
        with patch('worker.storage.Client') as client:
            client.return_value.bucket.return_value.blob.return_value.download_as_bytes.return_value=json.dumps(payload).encode()
            result=worker.app.test_client().post('/',json=body)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(result.json['status'],'committed')
        self.assertEqual(result.json['rows_valid'],1)
        self.assertEqual(result.json['rows_rejected'],1)

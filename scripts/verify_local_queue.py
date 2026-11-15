"""Real emulator push -> worker -> isolated SQL smoke test, including replay.

Start: docker compose --profile test --profile queue up -d --build test-postgres pubsub local-worker
Run: .venv/bin/python scripts/verify_local_queue.py
No GCP credentials, cloud APIs or production tables are used.
"""
import base64
import json
import time
from pathlib import Path
import psycopg
import requests

ROOT=Path(__file__).resolve().parents[1]
DSN="postgresql://ridepulse_test:local_test_only@127.0.0.1:55433/ridepulse_test"
BASE="http://127.0.0.1:8085/v1/projects/ridepulse-local"

def main():
    with psycopg.connect(DSN) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS queue_fixture")
        conn.execute("SET search_path=queue_fixture")
        for name in ("001_create_tables.sql","008_dataset_versions.sql"):
            conn.execute((ROOT/"sql"/name).read_text())
        conn.execute("TRUNCATE trips")
    session=requests.Session()
    for path,body in [("topics/local-trips",{}),("subscriptions/local-worker",{
        "topic":"projects/ridepulse-local/topics/local-trips",
        "pushConfig":{"pushEndpoint":"http://local-worker:8080/"},"ackDeadlineSeconds":10})]:
        result=session.put(BASE+"/"+path,json=body,timeout=10)
        if result.status_code!=409:result.raise_for_status()
    task={"task_type":"trip_batch","city":"nyc","bucket":"ridepulse-local","object":"raw/citibike/local.csv"}
    def publish():
        response=session.post(BASE+"/topics/local-trips:publish",json={"messages":[{"data":base64.b64encode(json.dumps(task).encode()).decode()}]},timeout=10)
        response.raise_for_status()
        return response.json()["messageIds"][0]
    def state():
        with psycopg.connect(DSN) as conn:
            return conn.execute("SELECT (SELECT COUNT(*) FROM queue_fixture.trips),(SELECT version FROM queue_fixture.dataset_versions WHERE city='nyc' AND domain='trips')").fetchone()
    first_id=publish()
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        count,version=state()
        if count==2:break
        time.sleep(.25)
    else:raise RuntimeError("Emulator delivery did not commit the two fixture trips")
    second_id=publish()
    # Poll logs independently below: second publish must have been delivered, not just queued.
    import subprocess
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        logs=subprocess.check_output(["docker","compose","--profile","test","--profile","queue","logs","--no-color","local-worker"],cwd=ROOT,text=True)
        completed=[]
        for line in logs.splitlines():
            try:record=json.loads(line[line.index('{'):])
            except (ValueError,json.JSONDecodeError):continue
            if record.get('message')=='task_completed' and record.get('result')=='committed':
                completed.append(record.get('task_id'))
        if first_id in completed and second_id in completed:break
        time.sleep(.5)
    else:raise RuntimeError("Duplicate push not observed by worker")
    assert state()==(2,version),"Replay changed row count or cache version"
    result={"queue":"real Pub/Sub emulator push","rows":2,"duplicate_replay":"passed","version_unchanged":True,"scope":"local queue_fixture only; local files replace GCS"}
    (ROOT/"work").mkdir(exist_ok=True)
    (ROOT/"work/local-queue-verification.json").write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()

"""Controlled HTTP-driven Cloud Run worker trials against an isolated SQL schema.

The handler receives normal Pub/Sub envelopes; producer concurrency is fixed.
This isolates worker/SQL scaling from Pub/Sub ramp-up, which is verified separately.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import sys
import time
import uuid
from threading import Lock

from google.cloud import storage
from google.oauth2.credentials import Credentials
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import aggregates
from cloud_support import gcloud,token,database,PROJECT,BUCKET

SCHEMA="ridepulse_benchmark"


def initialize():
    with database(SCHEMA) as conn:
        if conn.execute("SELECT current_schema()").fetchone()[0]!=SCHEMA:
            raise RuntimeError("Owner must create the isolated ridepulse_benchmark schema first")
        root=Path(__file__).resolve().parents[1]/"sql"
        for name in ("001_create_tables.sql","008_dataset_versions.sql","010_serving_aggregates.sql"):
            conn.execute((root/name).read_text())


def reset():
    with database(SCHEMA) as conn:
        if conn.execute("SELECT current_schema()").fetchone()[0]!=SCHEMA:
            raise RuntimeError("Refusing to reset anything outside the benchmark schema")
        conn.execute("TRUNCATE trips,route_counts,hourly_trip_counts,station_hour_counts,stations,station_snapshots,aggregate_state")
        aggregates.refresh(conn)


def inputs(path,count):
    if path.exists():
        saved=json.loads(path.read_text())
        if saved.get("project")!=PROJECT or len(saved.get('tasks',[]))!=count:
            raise ValueError("Input manifest project/batch count mismatch")
        return saved
    client=storage.Client(project=PROJECT,credentials=Credentials(token()))
    blobs=[b for b in client.list_blobs(BUCKET,prefix="raw/citibike/") if re.search(r"-batch-\d+\.csv$",b.name)]
    blobs.sort(key=lambda blob:blob.name)
    if len(blobs)<count:
        raise ValueError("Not enough immutable source batches")
    tasks=[]
    for blob in blobs[:count]:
        pinned=client.bucket(BUCKET).blob(blob.name,generation=int(blob.generation))
        with pinned.open("r",encoding="utf-8") as source:
            reader=csv.DictReader(source)
            rows=sum(1 for _ in reader)
        tasks.append({"task_type":"trip_batch","city":"nyc","bucket":BUCKET,
                      "object":blob.name,"generation":str(blob.generation),"expected_rows":rows})
        print("Counted source batch",blob.name,rows,flush=True)
    payload={"project":PROJECT,"created_at":datetime.now(timezone.utc).isoformat(),"tasks":tasks}
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(payload,indent=2))
    return payload


def activity(entries):
    starts={};intervals=[];instance_ids=set()
    for entry in sorted(entries,key=lambda value:value['timestamp']):
        payload=entry.get('jsonPayload',{})
        instance=entry.get('labels',{}).get('instanceId')
        if not instance:
            continue
        key=(instance,payload.get('task_id'))
        moment=datetime.fromisoformat(entry['timestamp'].replace('Z','+00:00')).timestamp()
        if payload.get('message')=='task_started':
            starts[key]=moment
        elif payload.get('message')=='task_completed' and key in starts:
            intervals.append((starts.pop(key),moment,instance))
            instance_ids.add(instance)
    events=[(start,1,instance) for start,end,instance in intervals]+[(end,-1,instance) for start,end,instance in intervals]
    active=set();peak=0
    for moment,change,instance in sorted(events,key=lambda item:(item[0],item[1])):
        if change>0:active.add(instance)
        else:active.discard(instance)
        peak=max(peak,len(active))
    return {"observed_peak_active_instances":peak,"distinct_instances":len(instance_ids),"task_intervals":len(intervals)}


def trial(service,url,tasks,limit,repeat):
    reset()
    gcloud('run','services','update',service,'--region=us-central1',f'--max-instances={limit}','--quiet')
    revision=gcloud('run','services','describe',service,'--region=us-central1','--format=value(status.latestReadyRevisionName)')
    identity=token(identity=True)
    identity_lock=Lock()
    run_id=uuid.uuid4().hex
    wall_start=time.time()
    start_utc=datetime.now(timezone.utc).isoformat();start=time.monotonic()
    def deliver(index_task):
        nonlocal identity
        index,task=index_task
        body={k:v for k,v in task.items() if k!='expected_rows'}
        envelope={'message':{'messageId':run_id+'-'+str(index),'data':base64.b64encode(json.dumps(body).encode()).decode()}}
        attempts=[]
        for attempt in range(30):
            if time.time()-wall_start>1200:
                raise RuntimeError('Worker trial exceeded the 20-minute dispatch budget')
            try:
                used_identity=identity
                response=requests.post(url,json=envelope,headers={'Authorization':'Bearer '+used_identity},timeout=310)
                attempts.append(response.status_code)
                if response.status_code==401:
                    with identity_lock:
                        if identity==used_identity:identity=token(identity=True)
                if response.status_code==403:
                    raise RuntimeError('Worker invocation was denied')
                if response.status_code==200 and response.json().get('status')=='rejected':
                    raise RuntimeError('Fixed source input was permanently rejected')
                if response.status_code==200 and response.json().get('status')=='committed':
                    return {'index':index,'attempt_statuses':attempts,'result':response.json()}
            except requests.RequestException as exc:
                attempts.append(type(exc).__name__)
            time.sleep(min(10,1+attempt))
        raise RuntimeError('Bounded delivery retries exhausted')
    with ThreadPoolExecutor(max_workers=16) as executor:
        replies=list(executor.map(deliver,enumerate(tasks)))
    elapsed=time.monotonic()-start
    if abs((time.time()-wall_start)-elapsed)>5:
        raise RuntimeError('Client suspension interrupted the trial; timing is invalid')
    expected=sum(task['expected_rows'] for task in tasks)
    with database(SCHEMA) as conn:
        count=conn.execute('SELECT COUNT(*) FROM trips').fetchone()[0]
        hourly=conn.execute('SELECT COALESCE(SUM(trip_count),0) FROM hourly_trip_counts').fetchone()[0]
    if count!=expected or hourly!=expected:
        raise AssertionError(f'Expected {expected} facts/hourly trips, got {count}/{hourly}')
    query=f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}" AND jsonPayload.task_id=~"^{run_id}"'
    entries=[]
    for _ in range(18):
        entries=json.loads(gcloud('logging','read',query,'--limit=1000','--format=json'))
        if sum(e.get('jsonPayload',{}).get('message')=='task_completed' for e in entries)>=len(tasks):
            break
        time.sleep(5)
    result={'run_id':run_id,'revision':revision,'started_at':start_utc,'configured_max_instances':limit,'repeat':repeat,
            'seconds':elapsed,'source_rows':expected,'committed_rows':count,'rows_per_second':count/elapsed,
            'hourly_total':int(hourly),'producer_concurrency':16,**activity(entries),'deliveries':replies}
    if not result['observed_peak_active_instances']:
        raise RuntimeError('No actual instance activity evidence was collected')
    return result,entries


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--batches',type=int,default=16)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--image',default=f'us-central1-docker.pkg.dev/{PROJECT}/ridepulse-containers/ridepulse-worker:completion-20261114-v6')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    if not 16<=args.batches<=32 or not 1<=args.repeats<=5:
        parser.error('Use 16-32 batches and 1-5 repeats for bounded, adequately queued trials')
    initialize()
    manifest=inputs(args.manifest,args.batches)
    if args.prepare_only:
        print('Prepared isolated schema and counted immutable inputs; no scaling trial was run.')
        raise SystemExit(0)
    service='rp-benchmark-'+uuid.uuid4().hex[:8]
    result={'format':1,'method':'HTTP-driven worker scaling, independent of Pub/Sub dispatch','project':PROJECT,
            'image':args.image,'settings':{'cpu':1,'memory_MiB':512,'concurrency':1,'schema':SCHEMA},
            'input_manifest':manifest,'runs':[]}
    created=False
    try:
        gcloud('run','deploy',service,'--image='+args.image,'--region=us-central1',
               f'--service-account=ridepulse-worker@{PROJECT}.iam.gserviceaccount.com',
               f'--add-cloudsql-instances={PROJECT}:us-central1:ridepulse-postgres',
               '--set-secrets=DB_PASSWORD=ridepulse-db-password:latest',
               f'--set-env-vars=GOOGLE_CLOUD_PROJECT={PROJECT},RIDEPULSE_BUCKET={BUCKET},INSTANCE_CONNECTION_NAME={PROJECT}:us-central1:ridepulse-postgres,DB_NAME=ridepulse,DB_USER=ridepulse_app,DB_SCHEMA={SCHEMA},RIDEPULSE_MAINTAIN_AGGREGATES=true',
               '--cpu=1','--memory=512Mi','--concurrency=1','--min-instances=0','--max-instances=1','--timeout=300','--no-allow-unauthenticated','--quiet')
        created=True
        url=gcloud('run','services','describe',service,'--region=us-central1','--format=value(status.url)')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        for repeat in range(args.repeats):
            # Rotate trial order to reduce systematic cold-cache/order effects.
            limits=[1,2,4,8];limits=limits[repeat%4:]+limits[:repeat%4]
            for limit in limits:
                run,entries=trial(service,url,manifest['tasks'],limit,repeat+1)
                result['runs'].append(run)
                args.output.write_text(json.dumps(result,indent=2))
                args.output.with_name(run['run_id']+'-logs.json').write_text(json.dumps(entries,indent=2))
                print(json.dumps({k:v for k,v in run.items() if k!='deliveries'}),flush=True)
    finally:
        if created:gcloud('run','services','delete',service,'--region=us-central1','--quiet')
    reset()
    result['benchmark_data_reset']=True
    args.output.write_text(json.dumps(result,indent=2))

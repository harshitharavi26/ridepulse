"""Finish versioned serving and controlled measurements after owner SQL setup."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid

import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import aggregates
from cloud_support import database,gcloud,token,PROJECT,BUCKET


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-experiments',action='store_true')
    parser.add_argument('--refresh-aggregates',action='store_true',help='Force rebuild even if the serving watermark is current')
    parser.add_argument('--tag',default='completion-20261114-v6')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    with database() as conn:
        tables=conn.execute("SELECT to_regclass('dataset_versions'),to_regclass('aggregate_state'),to_regnamespace('ridepulse_benchmark')").fetchone()
        if any(value is None for value in tables):
            raise SystemExit('Owner setup is not applied. Apply the required sql/008, sql/009 and sql/010 migrations as the database owner in ridepulse first. No cloud changes were made by this command.')
        if args.refresh_aggregates or not aggregates.lock_current(conn):
            aggregates.refresh(conn)
        print('Public aggregates current at:',conn.execute('SELECT * FROM aggregate_state ORDER BY city').fetchall(),flush=True)
    host=gcloud('redis','instances','describe','ridepulse-cache','--region=us-central1','--format=value(host)')
    if not host:raise SystemExit('Memorystore is not ready')
    redis_url=f'redis://{host}:6379/0'
    images=f'us-central1-docker.pkg.dev/{PROJECT}/ridepulse-containers/'
    gcloud('run','services','update','ridepulse-api','--region=us-central1',
           '--image='+images+'ridepulse-api:'+args.tag,
           '--update-env-vars=REDIS_URL='+redis_url+',DB_POOL_SIZE=1,DB_STATEMENT_TIMEOUT_MS=20000','--quiet')
    gcloud('run','services','update','ridepulse-worker','--region=us-central1',
           '--image='+images+'ridepulse-worker:'+args.tag,
           '--update-env-vars=RIDEPULSE_MAINTAIN_AGGREGATES=true','--quiet')
    api=gcloud('run','services','describe','ridepulse-api','--region=us-central1','--format=value(status.url)')
    headers={'Authorization':'Bearer '+token(identity=True)}
    query='/v1/trips/leaderboard?city=nyc&start_date=2026-04-01&end_date=2026-05-01'
    first=requests.get(api+query,headers=headers,timeout=35);first.raise_for_status()
    second=requests.get(api+query,headers=headers,timeout=35);second.raise_for_status()
    assert first.json()['data']==second.json()['data'] and second.json()['meta']['cache']=='hit'
    print('Cloud versioned cache verified',flush=True)
    if args.run_experiments:
        service='rp-api-bypass-'+uuid.uuid4().hex[:8]
        created=False
        output=root/'experiments/results'
        output.mkdir(parents=True,exist_ok=True)
        try:
            gcloud('run','deploy',service,'--region=us-central1','--image='+images+'ridepulse-api:'+args.tag,
                   f'--service-account=ridepulse-worker@{PROJECT}.iam.gserviceaccount.com',
                   f'--add-cloudsql-instances={PROJECT}:us-central1:ridepulse-postgres',
                   '--set-secrets=DB_PASSWORD=ridepulse-db-password:latest',
                   f'--set-env-vars=INSTANCE_CONNECTION_NAME={PROJECT}:us-central1:ridepulse-postgres,DB_NAME=ridepulse,DB_USER=ridepulse_app,DB_POOL_SIZE=1,DB_STATEMENT_TIMEOUT_MS=20000,REDIS_URL={redis_url},RIDEPULSE_CACHE_BYPASS=true',
                   '--network=default','--subnet=default','--vpc-egress=private-ranges-only','--execution-environment=gen2',
                   '--cpu=1','--memory=512Mi','--concurrency=8','--min-instances=0','--max-instances=10',
                   '--timeout=60','--no-allow-unauthenticated','--quiet')
            created=True
            bypass=gcloud('run','services','describe',service,'--region=us-central1','--format=value(status.url)')
            subprocess.run([sys.executable,str(root/'scripts/benchmark_api.py'),'--cached-url',api,'--bypass-url',bypass,
                            '--output-dir',str(output/'api')],check=True)
            subprocess.run([sys.executable,str(root/'scripts/benchmark_workers.py'),'--manifest',str(output/'worker-inputs-10k.json'),
                            '--output',str(output/'workers.json'),'--image',images+'ridepulse-worker:'+args.tag],check=True)
        finally:
            if created:gcloud('run','services','delete',service,'--region=us-central1','--quiet')
    else:
        print('Serving setup is complete; experiments were not requested by this invocation.')

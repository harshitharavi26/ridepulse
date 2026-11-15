"""Start/pause a RidePulse work session without deleting durable project data."""
import argparse
import json
import subprocess
import time
import socket
import os

from cloud_support import gcloud,GCLOUD,PROJECT,database

REGION='us-central1'
JOBS=('ridepulse-status-collector','ridepulse-metadata-collector')


def redis_instance():
    reply=subprocess.run([*GCLOUD,'redis','instances','describe','ridepulse-cache','--region='+REGION,'--format=json'],capture_output=True,text=True)
    if reply.returncode==0:return json.loads(reply.stdout)
    if 'NOT_FOUND' in reply.stderr or 'not found' in reply.stderr:return None
    raise RuntimeError(reply.stderr)


def sql_instance():
    return json.loads(gcloud('sql','instances','describe','ridepulse-postgres','--format=json'))


def wait(check,description,seconds=900):
    print(description,flush=True)
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=check()
        if result:return result
        time.sleep(10)
    raise TimeoutError(description)


def set_sql_policy(policy):
    operation=json.loads(gcloud('sql','instances','patch','ridepulse-postgres',
                                '--activation-policy='+policy,'--async','--format=json','--quiet'))
    def finished():
        result=json.loads(gcloud('sql','operations','describe',operation['name'],'--format=json'))
        if result.get('status')!='DONE':return False
        if result.get('error'):raise RuntimeError('Cloud SQL policy operation failed: '+json.dumps(result['error']))
        return result
    wait(finished,'Waiting for SQL activation policy '+policy,seconds=900)
    if sql_instance()['settings']['activationPolicy']!=policy:
        raise RuntimeError('SQL activation policy did not match the completed operation')


def publishing(enabled):
    for job in JOBS:
        gcloud('run','jobs','update',job,'--region='+REGION,'--update-env-vars=GBFS_PUBLISH_TASKS='+str(enabled).lower(),'--quiet')


def status():
    sql=sql_instance();cache=redis_instance()
    print(json.dumps({'sql':sql['state'],'activation_policy':sql['settings']['activationPolicy'],
                      'redis':cache.get('state') if cache else 'absent',
                      'redis_host':cache.get('host') if cache else None},indent=2))


def start():
    try:
        with socket.create_connection(('127.0.0.1',int(os.getenv('CLOUD_SQL_PROXY_PORT','5434'))),timeout=1):pass
    except OSError as exc:
        raise RuntimeError('Start the Cloud SQL Auth Proxy on port 5434 before starting this session') from exc
    sql=sql_instance()
    if sql['settings']['activationPolicy']!='ALWAYS':
        set_sql_policy('ALWAYS')
    cache=redis_instance()
    if not cache:
        gcloud('redis','instances','create','ridepulse-cache','--region='+REGION,'--size=1','--tier=basic',
               '--network=default','--connect-mode=direct-peering','--async','--quiet')
    def ready_cache():
        value=redis_instance()
        return value if value and value.get('state')=='READY' else None
    cache=wait(ready_cache,'Waiting for Memorystore')
    with database() as conn:
        versions=conn.execute("SELECT to_regclass('dataset_versions')").fetchone()[0]
        serving=conn.execute("SELECT to_regclass('aggregate_state')").fetchone()[0]
        aligned=conn.execute("SELECT a.valid AND a.source_version=v.version FROM aggregate_state a JOIN dataset_versions v USING(city) WHERE a.city='nyc' AND v.domain='trips'").fetchone() if serving else None
    if versions:
        gcloud('run','services','update','ridepulse-api','--region='+REGION,
               '--update-env-vars=REDIS_URL=redis://'+cache['host']+':6379/0','--quiet')
    else:
        gcloud('run','services','update','ridepulse-api','--region='+REGION,'--remove-env-vars=REDIS_URL','--quiet')
    gcloud('run','services','update','ridepulse-worker','--region='+REGION,
           '--update-env-vars=RIDEPULSE_MAINTAIN_AGGREGATES='+str(bool(aligned and aligned[0])).lower(),'--quiet')
    url=gcloud('run','services','describe','ridepulse-worker','--region='+REGION,'--format=value(status.url)')
    gcloud('pubsub','subscriptions','modify-push-config','ridepulse-worker-push','--push-endpoint='+url,
           f'--push-auth-service-account=ridepulse-pubsub-push@{PROJECT}.iam.gserviceaccount.com',
           '--push-auth-token-audience='+url,'--quiet')
    publishing(True)
    for job in JOBS:gcloud('run','jobs','execute',job,'--region='+REGION,'--async','--quiet')
    status()


def stop(delete_cache):
    publishing(False)
    gcloud('pubsub','subscriptions','modify-push-config','ridepulse-worker-push','--push-endpoint=','--quiet')
    # Allow small in-flight GBFS transactions to finish. Unacknowledged messages
    # remain retained; no queue is purged and no source object is removed.
    for _ in range(3):time.sleep(10)
    set_sql_policy('NEVER')
    if delete_cache and redis_instance():
        gcloud('redis','instances','delete','ridepulse-cache','--region='+REGION,'--async','--quiet')
        wait(lambda:redis_instance() is None,'Waiting for disposable Redis removal',seconds=900)
    status()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','stop','status'])
    parser.add_argument('--delete-cache',action='store_true')
    args=parser.parse_args()
    if args.action=='start':start()
    elif args.action=='stop':stop(args.delete_cache)
    else:status()

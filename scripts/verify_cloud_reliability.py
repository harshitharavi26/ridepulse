"""Bounded authenticated Pub/Sub retry/recovery/dead-letter proof.

Uses a separate private worker and temporary queues. It replays existing real
station metadata, never synthetic trips. Production service settings are untouched.
"""
import argparse
import base64
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import time
import uuid
import requests
from datetime import timedelta

from cloud_support import gcloud,PROJECT,database,token

REGION="us-central1"
PUSH=f"ridepulse-pubsub-push@{PROJECT}.iam.gserviceaccount.com"
AGENT="service-42502534899@gcp-sa-pubsub.iam.gserviceaccount.com"


def logs(service,message_id):
    query=f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}" AND jsonPayload.message="task_completed" AND jsonPayload.task_id="{message_id}"'
    return json.loads(gcloud("logging","read",query,"--limit=100","--format=json"))


def wait_for(check,seconds=240):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=check()
        if result:
            return result
        time.sleep(10)
    raise TimeoutError("Verification deadline exceeded")


def public_snapshot():
    with database() as conn:
        row=conn.execute("SELECT (SELECT COUNT(*) FROM trips),(SELECT COUNT(*) FROM stations),(SELECT MAX(updated_at) FROM stations)").fetchone()
        return [str(value) for value in row]


def paused_main_path():
    original=json.loads(gcloud("pubsub","subscriptions","describe","ridepulse-worker-push","--format=json"))
    if original.get("pushConfig",{}).get("pushEndpoint"):
        raise RuntimeError("Main subscription must already be paused")
    for job in ("ridepulse-status-collector","ridepulse-metadata-collector"):
        data=json.loads(gcloud("run","jobs","describe",job,"--region="+REGION,"--format=json"))
        values=[]
        def walk(value):
            if isinstance(value,dict):
                if value.get("name")=="GBFS_PUBLISH_TASKS": values.append(value.get("value"))
                for child in value.values():walk(child)
            elif isinstance(value,list):
                for child in value:walk(child)
        walk(data)
        if values!=["false"]:
            raise RuntimeError("Collectors must explicitly have publishing disabled")
    now=datetime.now(timezone.utc)
    params={"filter":'metric.type="pubsub.googleapis.com/subscription/num_undelivered_messages" AND resource.labels.subscription_id="ridepulse-worker-push"',
            "interval.startTime":(now-timedelta(minutes=10)).isoformat(),"interval.endTime":now.isoformat()}
    reply=requests.get(f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries",params=params,
                       headers={"Authorization":"Bearer "+token(),"x-goog-user-project":PROJECT},timeout=20)
    reply.raise_for_status()
    points=reply.json().get("timeSeries",[])
    if not points or any(int(series['points'][0]['value']['int64Value'])!=0 for series in points):
        raise RuntimeError("Cannot establish that the main backlog is empty")
    return original


def run(task,image,output,reuse_main=False):
    if task.get("task_type")!="station_information":
        raise ValueError("Verification must replay real station metadata only")
    prefix="rp-verify-"+uuid.uuid4().hex[:8]
    topic,dead,subscription,inspection=(prefix+suffix for suffix in ("-tasks","-dead","-push","-inspect"))
    original=paused_main_path() if reuse_main else None
    if original:
        topic,dead,subscription="ridepulse-tasks","ridepulse-dead-letter","ridepulse-worker-push"
    result={"format":1,"started_at":datetime.now(timezone.utc).isoformat(),"service":prefix,
            "before":public_snapshot(),"source_task":task,"events":[]}
    created=[]
    path_changed=False
    def event(name,**details):
        result["events"].append({"event":name,"at":datetime.now(timezone.utc).isoformat(),**details})
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,indent=2,default=str))
        print(name,flush=True)
    def publish():
        payload={key:task[key] for key in ("task_type","city","bucket","object","generation")}
        payload["verification_id"]=prefix
        reply=json.loads(gcloud("pubsub","topics","publish",topic,"--message="+json.dumps(payload),"--format=json"))
        return reply["messageIds"][0]
    try:
        gcloud("run","deploy",prefix,"--image="+image,"--region="+REGION,
               f"--service-account=ridepulse-worker@{PROJECT}.iam.gserviceaccount.com",
               f"--add-cloudsql-instances={PROJECT}:us-central1:ridepulse-postgres",
               "--set-secrets=DB_PASSWORD=ridepulse-db-password:latest",
               f"--set-env-vars=GOOGLE_CLOUD_PROJECT={PROJECT},RIDEPULSE_BUCKET={task['bucket']},INSTANCE_CONNECTION_NAME={PROJECT}:us-central1:ridepulse-postgres,DB_NAME=verification_missing_database,DB_USER=ridepulse_app",
               "--cpu=1","--memory=512Mi","--concurrency=1","--min-instances=0","--max-instances=1",
               "--timeout=300","--no-allow-unauthenticated","--quiet")
        created.append(("service",prefix))
        url=gcloud("run","services","describe",prefix,"--region="+REGION,"--format=value(status.url)")
        gcloud("run","services","add-iam-policy-binding",prefix,"--region="+REGION,
               "--member=serviceAccount:"+PUSH,"--role=roles/run.invoker","--quiet")
        event("waiting_for_invoker_iam_propagation")
        # Cloud Run IAM propagation can outlast a five-attempt retry budget.
        for _ in range(9):time.sleep(10)
        if not reuse_main:
            for name in (topic,dead):
                gcloud("pubsub","topics","create",name,"--quiet")
                created.append(("topic",name))
        gcloud("pubsub","subscriptions","create",inspection,"--topic="+dead,"--quiet")
        created.append(("subscription",inspection))
        if reuse_main:
            event("paused_main_path_preconditions_verified",original=original)
            gcloud("pubsub","subscriptions","modify-push-config",subscription,"--push-endpoint="+url,"--push-auth-service-account="+PUSH,"--quiet")
            path_changed=True
            gcloud("pubsub","subscriptions","update",subscription,"--min-retry-delay=10s","--max-retry-delay=20s","--dead-letter-topic="+dead,"--max-delivery-attempts=20","--quiet")
        else:
            gcloud("pubsub","subscriptions","create",subscription,"--topic="+topic,"--push-endpoint="+url,
                   "--push-auth-service-account="+PUSH,"--dead-letter-topic="+dead,
                   "--max-delivery-attempts=20","--min-retry-delay=10s","--max-retry-delay=20s","--ack-deadline=300","--quiet")
            created.append(("subscription",subscription))
            gcloud("pubsub","topics","add-iam-policy-binding",dead,"--member=serviceAccount:"+AGENT,"--role=roles/pubsub.publisher","--quiet")
            gcloud("pubsub","subscriptions","add-iam-policy-binding",subscription,"--member=serviceAccount:"+AGENT,"--role=roles/pubsub.subscriber","--quiet")
        event("verification_resources_ready",url=url)
        recovery_id=publish()
        failures=wait_for(lambda:[entry for entry in logs(prefix,recovery_id) if entry.get('jsonPayload',{}).get('http_status')==500])
        event("infrastructure_failure_observed",message_id=recovery_id,failures=len(failures))
        gcloud("run","services","update",prefix,"--region="+REGION,"--update-env-vars=DB_NAME=ridepulse","--quiet")
        recovered=wait_for(lambda:[entry for entry in logs(prefix,recovery_id) if entry.get('jsonPayload',{}).get('result')=='committed'])
        event("same_message_recovered",message_id=recovery_id,logs=recovered)
        duplicate_id=publish()
        duplicate=wait_for(lambda:[entry for entry in logs(prefix,duplicate_id) if entry.get('jsonPayload',{}).get('result')=='committed'])
        event("duplicate_replay_committed",message_id=duplicate_id,logs=duplicate)
        gcloud("pubsub","subscriptions","update",subscription,"--dead-letter-topic="+dead,"--max-delivery-attempts=5","--quiet")
        gcloud("run","services","update",prefix,"--region="+REGION,"--update-env-vars=DB_NAME=verification_missing_database","--quiet")
        failed_id=publish()
        def pull():
            messages=json.loads(gcloud("pubsub","subscriptions","pull",inspection,"--limit=1","--auto-ack","--format=json"))
            return messages
        forwarded=wait_for(pull,seconds=420)
        event("dead_letter_forwarded",message_id=failed_id,forwarded=forwarded,attempt_logs=logs(prefix,failed_id))
        result["after"]=public_snapshot()
        result["public_data_unchanged"]=result["before"]==result["after"]
        if not result["public_data_unchanged"]:
            raise AssertionError("Public data snapshot changed during verification")
        event("verification_passed")
    except Exception as exc:
        result["error_type"]=type(exc).__name__
        event("verification_failed")
        raise
    finally:
        result["cleanup_errors"]=[]
        if path_changed:
            try:
                gcloud("pubsub","subscriptions","modify-push-config",subscription,"--push-endpoint=","--quiet")
                retry=original['retryPolicy']
                policy=original['deadLetterPolicy']
                gcloud("pubsub","subscriptions","update",subscription,"--min-retry-delay="+retry['minimumBackoff'],"--max-retry-delay="+retry['maximumBackoff'],"--dead-letter-topic="+policy['deadLetterTopic'],"--max-delivery-attempts="+str(policy['maxDeliveryAttempts']),"--quiet")
                event("main_path_restored")
            except subprocess.CalledProcessError:
                result["cleanup_errors"].append(subscription)
        for kind,name in reversed(created):
            try:
                if kind=="service":
                    gcloud("run","services","delete",name,"--region="+REGION,"--quiet")
                else:
                    gcloud("pubsub",kind+"s","delete",name,"--quiet")
            except subprocess.CalledProcessError:
                result["cleanup_errors"].append(name)
        event("temporary_resources_cleaned",errors=result["cleanup_errors"])


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--reuse-paused-main-path",action="store_true",help="Requires empty backlog and explicitly paused collectors; restores original push/retry settings")
    parser.add_argument("--image",default=f"us-central1-docker.pkg.dev/{PROJECT}/ridepulse-containers/ridepulse-worker:completion-20261114-v2")
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text())
    if manifest.get("project")!=PROJECT:
        raise SystemExit("Wrong manifest project")
    run(manifest["tasks"][0],args.image,args.output,args.reuse_paused_main_path)

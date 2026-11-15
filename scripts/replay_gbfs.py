"""Plan/resume bounded archive replay, pinning each immutable Storage generation."""
import argparse
from datetime import date,datetime,timezone
import json
from pathlib import Path
import re
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor,as_completed
from threading import Lock,Event

from google.cloud import storage
from google.oauth2.credentials import Credentials
import requests

from cloud_support import PROJECT,ACCOUNT,BUCKET,token,gcloud

PATTERN = re.compile(r"raw/gbfs/(nyc|boulder)/(\d{8}T\d{12}Z)_(station_status|station_information)\.json")


def save(path, payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(payload,indent=2))
    temporary.replace(path)


def plan(city,day,limit,feed="both"):
    client=storage.Client(project=PROJECT,credentials=Credentials(token()))
    tasks=[]
    for name in ([city] if city != "both" else ["nyc","boulder"]):
        for blob in client.list_blobs(BUCKET,prefix=f"raw/gbfs/{name}/{day:%Y%m%d}"):
            match=PATTERN.fullmatch(blob.name)
            if match and (feed == "both" or match[3] == feed):
                tasks.append({"task_type":match[3],"city":name,"bucket":BUCKET,
                              "object":blob.name,"generation":str(blob.generation),"status":"pending"})
    tasks.sort(key=lambda task:PATTERN.fullmatch(task["object"])[2],reverse=True)
    return {"format":1,"project":PROJECT,"planned_at":datetime.now(timezone.utc).isoformat(),
            "tasks":tasks[:limit]}


def execute(manifest,path,worker,concurrency=1):
    import base64
    identity=token(identity=True)
    lock=Lock();stopped=Event()
    def process(task):
        nonlocal identity
        if stopped.is_set():return
        if task["status"] in ("committed","rejected"):
            return
        body={key:task[key] for key in ("task_type","city","bucket","object","generation")}
        message_id=hashlib.sha256((task['object']+'@'+task['generation']).encode()).hexdigest()[:24]
        envelope={"message":{"messageId":"replay-"+message_id,
                             "data":base64.b64encode(json.dumps(body).encode()).decode()}}
        for attempt in range(3):
            try:
                response=requests.post(worker,json=envelope,headers={"Authorization":"Bearer "+identity},timeout=310)
                if response.status_code == 401:
                    with lock:identity=token(identity=True)
                response.raise_for_status()
                payload=response.json()
                if payload.get("status") not in ("committed","rejected"):
                    raise ValueError("Worker returned an unknown outcome")
                task.update(status=payload["status"],result=payload,processed_at=datetime.now(timezone.utc).isoformat())
                break
            except (requests.RequestException,ValueError) as exc:
                task.update(status="pending",last_error=type(exc).__name__)
                if attempt<2:
                    time.sleep(attempt+1)
            finally:
                with lock:save(path,manifest)
        print(task["city"],task["task_type"],task["generation"],task["status"],flush=True)
        if task["status"] == "pending":
            stopped.set()
            raise RuntimeError("Stopped on a retryable failure; rerun with the same manifest to resume.")
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures=[pool.submit(process,task) for task in manifest['tasks']]
        try:
            for future in as_completed(futures):future.result()
        except Exception:
            stopped.set()
            for future in futures:future.cancel()
            raise


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city",choices=["nyc","boulder","both"],default="both")
    parser.add_argument("--date",type=date.fromisoformat,required=True,help="UTC archive date")
    parser.add_argument("--feed",choices=["both","station_status","station_information"],default="both")
    parser.add_argument("--max-objects",type=int,default=20)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--concurrency",type=int,choices=range(1,5),default=1)
    parser.add_argument("--upload-manifest",action="store_true")
    parser.add_argument("--worker",default="https://ridepulse-worker-qkkoqlp5ra-uc.a.run.app")
    args=parser.parse_args()
    if not 1<=args.max_objects<=2000:
        parser.error("Choose a bounded max-objects between 1 and 2000")
    manifest=json.loads(args.manifest.read_text()) if args.manifest.exists() else plan(args.city,args.date,args.max_objects,args.feed)
    if manifest.get("project") != PROJECT or manifest.get("format") != 1:
        parser.error("Manifest belongs to a different project or format")
    save(args.manifest,manifest)
    print(f"Manifest has {len(manifest['tasks'])} pinned objects")
    if args.execute:
        execute(manifest,args.manifest,args.worker,args.concurrency)
    if args.upload_manifest:
        print(gcloud("storage","cp",str(args.manifest),f"gs://{BUCKET}/manifests/{args.manifest.name}"))

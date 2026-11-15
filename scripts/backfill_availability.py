"""Bounded recent observation history; older raw archives remain replayable."""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path

from cloud_support import BUCKET,gcloud
from replay_gbfs import plan,execute,save


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int,default=7,help='Complete UTC days before today; also includes today')
    parser.add_argument('--concurrency',type=int,choices=range(1,5),default=4)
    parser.add_argument('--directory',type=Path,default=Path('work/availability-backfill'))
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if not 1<=args.days<=14:parser.error('Use a bounded 1-14-day backfill')
    today=datetime.now(timezone.utc).date()
    summary=[]
    for offset in range(args.days,-1,-1):
        day=today-timedelta(days=offset)
        path=args.directory/f'gbfs-{day.isoformat()}.json'
        manifest=json.loads(path.read_text()) if path.exists() else plan('both',day,2000)
        save(path,manifest)
        print(day,'planned',len(manifest['tasks']),flush=True)
        if args.execute:
            execute(manifest,path,'https://ridepulse-worker-qkkoqlp5ra-uc.a.run.app',args.concurrency)
            gcloud('storage','cp',str(path),f'gs://{BUCKET}/manifests/availability-backfill/{path.name}')
        summary.append({'date':day.isoformat(),'objects':len(manifest['tasks']),
                        'committed':sum(task['status']=='committed' for task in manifest['tasks']),
                        'rejected':sum(task['status']=='rejected' for task in manifest['tasks']),
                        'rows_written':sum(task.get('result',{}).get('rows_written',0) for task in manifest['tasks'])})
        save(args.directory/'summary.json',summary)

"""Repeated fixed-data Locust trials with/without Redis on matched API revisions."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import requests
from cloud_support import token,PROJECT


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cached-url',required=True)
    parser.add_argument('--bypass-url',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=30)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--users',type=int,nargs='+',default=[1,5,10,20])
    args=parser.parse_args()
    if not 10<=args.seconds<=120 or not 1<=args.repeats<=5 or any(not 1<=n<=40 for n in args.users):
        parser.error('Use 10-120 seconds, 1-5 repeats and 1-40 users')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    identity=token(identity=True)
    headers={'Authorization':'Bearer '+identity}
    cities=requests.get(args.cached_url+'/v1/cities',headers=headers,timeout=30);cities.raise_for_status()
    if not cities.json()['data'][0]['trip_coverage']['first_event_at']:
        raise RuntimeError('Cannot benchmark an empty dataset')
    reference={'project':PROJECT,'created_at':datetime.now(timezone.utc).isoformat(),'queries':[],'cold_observations':[]}
    common={'city':'nyc','start_date':'2026-04-01','end_date':'2026-05-01'}
    query_specs=[('pickups','/v1/trips/leaderboard',dict(common,metric='pickups')),
                 ('dropoffs','/v1/trips/leaderboard',dict(common,metric='dropoffs')),
                 ('routes','/v1/trips/routes',common),
                 ('hourly','/v1/trips/patterns',dict(common,granularity='hour')),
                 ('daily','/v1/trips/patterns',dict(common,granularity='day'))]
    for name,path,params in query_specs:
        replies=[]
        for base in (args.cached_url,args.bypass_url):
            r=requests.get(base+path,params=params,headers=headers,timeout=35);r.raise_for_status()
            payload=r.json();replies.append(payload)
            reference['cold_observations'].append({'name':name,'base':base,'seconds':r.elapsed.total_seconds(),'cache':payload['meta']['cache']})
        if replies[0]['data']!=replies[1]['data'] or replies[0]['meta'].get('dataset_versions')!=replies[1]['meta'].get('dataset_versions'):
            raise AssertionError('Compared services must serve identical fixed-data results and versions')
        if replies[1]['meta']['cache']!='bypass' or replies[0]['meta']['cache'] not in ('hit','miss'):
            raise RuntimeError('Cache modes are not configured as required')
        reference['queries'].append({'name':name,'path':path,'params':params,
            'data_sha256':hashlib.sha256(json.dumps(replies[0]['data'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'dataset_versions':replies[0]['meta']['dataset_versions']})
    expected=args.output_dir/'reference.json';expected.write_text(json.dumps(reference,indent=2))
    root=Path(__file__).resolve().parents[1]
    for repeat in range(args.repeats):
        modes=[('cached',args.cached_url),('bypass',args.bypass_url)]
        if repeat%2:modes.reverse()
        for mode,host in modes:
            for users in args.users:
                name=f'{mode}-u{users}-r{repeat+1}'
                prefix=args.output_dir/name
                env={**os.environ,'RIDEPULSE_EXPECTED_PATH':str(expected.resolve()),
                     'RIDEPULSE_COUNTERS_PATH':str(prefix)+'.json','RIDEPULSE_BENCHMARK_TOKEN':token(identity=True)}
                with prefix.with_suffix('.log').open('w') as log:
                    completed=subprocess.run([sys.executable,'-m','locust','-f',str(root/'experiments/locustfile.py'),
                                    '--headless','--host',host,'--users',str(users),'--spawn-rate',str(users),
                                    '--run-time',str(args.seconds)+'s','--stop-timeout','35','--only-summary',
                                    '--csv',str(prefix),'--exit-code-on-error','1'],env=env,stdout=log,stderr=subprocess.STDOUT,check=False)
                counters_path=Path(str(prefix)+'.json')
                if completed.returncode not in (0,1) or not counters_path.exists():
                    raise RuntimeError('Locust did not complete: '+name)
                counters=json.loads(counters_path.read_text())
                if any(counters.get(key,0) for key in ('result_mismatches','version_drift','invalid_responses')):
                    raise RuntimeError('Correctness failure during measured trial: '+name)
                if not counters.get('requests'):
                    raise RuntimeError('No requests were measured: '+name)
                print(name,'complete',json.dumps(counters),flush=True)

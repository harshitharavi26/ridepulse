"""Cloud read-only smoke checks and durable fact/aggregate reconciliation."""
import argparse
from datetime import date,datetime,timedelta,timezone
import json
from pathlib import Path

import requests
from cloud_support import database,gcloud,token


def verify_api(base, headers, require_fresh=False, require_cache=False):
    """Check the actual demo selections; dates come from stored coverage."""
    checks=[]
    require_cache = require_cache or require_fresh
    def get(path, params=None, nonempty=True):
        params=params or {}
        response=requests.get(base+path,params=params,headers=headers,timeout=40)
        response.raise_for_status();payload=response.json()
        item={'path':path,'params':params,'seconds':response.elapsed.total_seconds(),'status':response.status_code}
        if 'data' in payload:
            item.update(rows=len(payload['data']),cache=payload.get('meta',{}).get('cache'))
            if nonempty:assert payload['data'], 'Expected nonempty real data for '+path
        checks.append(item);print(json.dumps(item),flush=True)
        return payload,item
    get('/health/live')
    ready,_=get('/health/ready')
    if require_cache:
        assert ready.get('cache')=='available', 'Redis is not available; run the full session.py start before recording'
    get('/v1/cities')
    common={'city':'nyc','start_date':'2026-04-01','end_date':'2026-05-01'}
    history=[('/v1/trips/leaderboard',common),
             ('/v1/trips/leaderboard',dict(common,metric='dropoffs')),
             ('/v1/trips/routes',common),
             ('/v1/trips/routes',dict(common,hide_round_trips=True)),
             ('/v1/trips/patterns',dict(common,granularity='hour')),
             ('/v1/trips/patterns',dict(common,granularity='day'))]
    reference={}
    for path,params in history:
        payload,_=get(path,params)
        if path in ('/v1/trips/leaderboard','/v1/trips/routes'):
            reference[(path,tuple(sorted(params.items())))]=payload['data']
    if require_cache:
        for path,params in history[:4]:
            payload,_=get(path,params)
            assert payload.get('meta',{}).get('cache')=='hit', 'Expected a warmed Redis hit for '+path+'; inspect cache configuration or concurrent data changes'
            assert payload['data']==reference[(path,tuple(sorted(params.items())))], 'Repeated historical result changed for '+path
    for city in ('nyc','boulder'):
        payload,item=get('/v1/availability/latest',{'city':city,'limit':200})
        item['fresh_rows']=sum(not row['is_stale'] for row in payload['data'])
        item['newest_collected_at']=max(row['collected_at'] for row in payload['data'])
        if require_fresh:assert item['fresh_rows']>0, 'No fresh observations for '+city
        coverage,_=get('/v1/availability/coverage',{'city':city},nonempty=False)
        newest=coverage.get('meta',{}).get('latest_local_date')
        assert newest, 'No stored observation date for '+city
        day=date.fromisoformat(newest)
        get('/v1/availability/low',{'city':city,'start_date':day.isoformat(),
                                 'end_date':(day+timedelta(days=1)).isoformat()})
    return checks


def verify(require_fresh=False, require_cache=False):
    base=gcloud('run','services','describe','ridepulse-api','--region=us-central1','--format=value(status.url)')
    headers={'Authorization':'Bearer '+token(identity=True)}
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'api':base}
    anonymous=requests.get(base+'/health/live',timeout=30)
    assert anonymous.status_code in (401,403), 'API unexpectedly accepts anonymous requests'
    result['requests']=verify_api(base,headers,require_fresh,require_cache)
    with database() as conn:
        facts,known_routes=conn.execute("""SELECT COUNT(*),COUNT(*) FILTER(WHERE NULLIF(BTRIM(start_station_id),'') IS NOT NULL
          AND NULLIF(BTRIM(end_station_id),'') IS NOT NULL) FROM trips WHERE city='nyc'""").fetchone()
        hourly=conn.execute("SELECT SUM(trip_count) FROM hourly_trip_counts WHERE city='nyc'").fetchone()[0]
        routes=conn.execute("SELECT SUM(trip_count) FROM route_counts WHERE city='nyc'").fetchone()[0]
        stations=dict(conn.execute("SELECT metric,SUM(trip_count) FROM station_hour_counts WHERE city='nyc' GROUP BY metric").fetchall())
        state=conn.execute("SELECT a.valid,a.source_version,v.version FROM aggregate_state a JOIN dataset_versions v USING(city) WHERE a.city='nyc' AND v.domain='trips'").fetchone()
        assert facts==3860371 and facts==hourly==stations['pickups']==stations['dropoffs']
        assert routes==known_routes and state[0] and state[1]==state[2]
        result['reconciliation']={'facts':facts,'hourly':int(hourly),'routes':int(routes),'known_route_facts':known_routes,
            'station_counts':{key:int(value) for key,value in stations.items()},'source_version':state[1],'durable_version':state[2]}
    path=Path('work/cloud-serving-final.json');path.write_text(json.dumps(result,indent=2));print(path)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-fresh',action='store_true',help='Require fresh observations in both cities and a working, proven Redis cache')
    parser.add_argument('--require-cache',action='store_true',help='Require actual warm Redis hits without requiring current station readings')
    args=parser.parse_args()
    verify(args.require_fresh,args.require_cache)

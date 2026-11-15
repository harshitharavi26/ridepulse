"""Cloud read-only smoke checks and durable fact/aggregate reconciliation."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path

import requests
from cloud_support import database,gcloud,token


def verify(require_fresh=False):
    base=gcloud('run','services','describe','ridepulse-api','--region=us-central1','--format=value(status.url)')
    headers={'Authorization':'Bearer '+token(identity=True)}
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'api':base,'requests':[]}
    anonymous=requests.get(base+'/health/live',timeout=30)
    assert anonymous.status_code in (401,403), 'API unexpectedly accepts anonymous requests'
    common={'city':'nyc','start_date':'2026-04-01','end_date':'2026-05-01'}
    specs=[('/health/live',{}),('/health/ready',{}),('/v1/cities',{}),
           ('/v1/trips/leaderboard',common),('/v1/trips/leaderboard',dict(common,metric='dropoffs')),
           ('/v1/trips/routes',common),('/v1/trips/patterns',dict(common,granularity='hour')),
           ('/v1/availability/latest',{'city':'nyc','limit':200}),
           ('/v1/availability/latest',{'city':'boulder','limit':200}),
           ('/v1/availability/low',{'city':'nyc','start_date':'2026-11-13','end_date':'2026-11-14'}),
           ('/v1/availability/low',{'city':'boulder','start_date':'2026-11-13','end_date':'2026-11-14'})]
    for path,params in specs:
        response=requests.get(base+path,params=params,headers=headers,timeout=40)
        response.raise_for_status();payload=response.json()
        item={'path':path,'params':params,'seconds':response.elapsed.total_seconds(),'status':response.status_code}
        if 'data' in payload:
            item.update(rows=len(payload['data']),cache=payload.get('meta',{}).get('cache'))
            assert payload['data'], 'Expected nonempty real data for '+path
        if path=='/v1/availability/latest':
            item['fresh_rows']=sum(not row['is_stale'] for row in payload['data'])
            item['newest_collected_at']=max(row['collected_at'] for row in payload['data'])
            if require_fresh:assert item['fresh_rows']>0, 'No fresh observations for '+params['city']
        result['requests'].append(item);print(json.dumps(item),flush=True)
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
    parser.add_argument('--require-fresh',action='store_true')
    verify(parser.parse_args().require_fresh)

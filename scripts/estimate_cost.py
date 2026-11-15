"""Reproducible gross USD estimate from retained worker handler timings.

Not an invoice: excludes startup, request overhead, failed requests not logged,
collector/API/dashboard/build time, storage, network, discounts and free tier.
"""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RATES={'run_vcpu_second':.000024,'run_gib_second':.0000025,'run_request':.40/1_000_000,
       'sql_g1_small_hour':.035,'redis_basic_gib_hour':.049}

def estimate():
    study=json.loads((ROOT/'experiments/results/workers.json').read_text())
    rows=[]
    for run in study['runs']:
        logs=json.loads((ROOT/f"experiments/results/{run['run_id']}-logs.json").read_text())
        records=[x['jsonPayload'] for x in logs if x.get('jsonPayload',{}).get('message')=='task_completed']
        seconds=sum(x['latency_ms'] for x in records)/1000
        dollars=seconds*(RATES['run_vcpu_second']+.5*RATES['run_gib_second'])+len(records)*RATES['run_request']
        rows.append({'run_id':run['run_id'],'maximum_workers':run['configured_max_instances'],'repeat':run['repeat'],
                     'logged_handler_seconds':seconds,'logged_requests':len(records),'handler_compute_usd':round(dollars,6)})
    result={'currency':'USD','region':'us-central1','rates':RATES,'pricing_checked_local_date':'2026-11-14',
            'method':'Sum logged task_completed latency seconds × (1 vCPU × CPU rate + 0.5 GiB × memory rate), plus logged requests. Includes logged retries. Handler-time estimate, not billable duration or invoice.',
            'runs':rows,'total_handler_compute_usd':round(sum(r['handler_compute_usd'] for r in rows),6),
            'one_hour_demo_sql_and_redis_usd':.084,'all_month_730h_sql_and_redis_usd':61.32,
            'sources':['https://cloud.google.com/run/pricing','https://cloud.google.com/sql/pricing','https://cloud.google.com/memorystore/docs/redis/pricing']}
    path=ROOT/'experiments/results/cost-estimate.json';path.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result

if __name__=='__main__':estimate()

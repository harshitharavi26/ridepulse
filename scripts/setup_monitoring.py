"""Create reviewable Logging metrics and a project Monitoring dashboard."""
import json
from pathlib import Path
import requests

from cloud_support import token,PROJECT


def configuration():
    api='resource.type="cloud_run_revision" AND resource.labels.service_name="ridepulse-api"'
    worker='resource.type="cloud_run_revision" AND resource.labels.service_name="ridepulse-worker"'
    metrics=[
        {"name":"ridepulse_cache_hits","filter":api+' AND jsonPayload.message="cache_hit"'},
        {"name":"ridepulse_cache_lookups","filter":api+' AND (jsonPayload.message="cache_hit" OR jsonPayload.message="cache_result")'},
        {"name":"ridepulse_worker_commits","filter":worker+' AND jsonPayload.message="task_completed" AND jsonPayload.result="committed"'},
        {"name":"ridepulse_worker_errors","filter":worker+' AND jsonPayload.message="task_completed" AND jsonPayload.http_status>=500'},
    ]
    for name,field in [('ridepulse_api_latency_ms','latency_ms'),('ridepulse_sql_latency_ms','sql_ms')]:
        metrics.append({"name":name,"filter":api+f' AND jsonPayload.{field}>=0',
                        "metricDescriptor":{"metricKind":"DELTA","valueType":"DISTRIBUTION","unit":"ms"},
                        "valueExtractor":f"EXTRACT(jsonPayload.{field})",
                        "bucketOptions":{"exponentialBuckets":{"numFiniteBuckets":25,"growthFactor":1.8,"scale":1}}})
    charts=[
        ('API p95 latency','run.googleapis.com/request_latencies','resource.labels.service_name="ridepulse-api"','ALIGN_PERCENTILE_95','ms'),
        ('Worker active instances','run.googleapis.com/container/instance_count','resource.labels.service_name="ridepulse-worker" AND metric.labels.state="active"','ALIGN_MAX','instances'),
        ('Task backlog','pubsub.googleapis.com/subscription/num_undelivered_messages','resource.labels.subscription_id="ridepulse-worker-push"','ALIGN_MAX','messages'),
        ('Oldest unacknowledged task','pubsub.googleapis.com/subscription/oldest_unacked_message_age','resource.labels.subscription_id="ridepulse-worker-push"','ALIGN_MAX','seconds'),
        ('Cloud SQL CPU','cloudsql.googleapis.com/database/cpu/utilization','','ALIGN_MEAN','utilization'),
        ('Cloud SQL connections','cloudsql.googleapis.com/database/postgresql/num_backends','','ALIGN_MAX','connections'),
        ('Redis keyspace hits','redis.googleapis.com/stats/keyspace_hits','','ALIGN_RATE','hits/sec'),
        ('Redis keyspace misses','redis.googleapis.com/stats/keyspace_misses','','ALIGN_RATE','misses/sec'),
    ]
    tiles=[]
    for i,(title,metric,scope,aligner,label) in enumerate(charts):
        query='metric.type="'+metric+'"'+(' AND '+scope if scope else '')
        tiles.append({'xPos':(i%2)*6,'yPos':(i//2)*4,'width':6,'height':4,
                      'widget':{'title':title,'xyChart':{'dataSets':[{'plotType':'LINE','timeSeriesQuery':{'timeSeriesFilter':{
                          'filter':query,'aggregation':{'alignmentPeriod':'60s','perSeriesAligner':aligner}}}}],
                          'yAxis':{'label':label,'scale':'LINEAR'}}}})
    return metrics,{'displayName':'RidePulse pipeline and serving','mosaicLayout':{'columns':12,'tiles':tiles}}


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    target=root/'infra/monitoring';target.mkdir(parents=True,exist_ok=True)
    metrics,dashboard=configuration()
    (target/'log_metrics.json').write_text(json.dumps(metrics,indent=2))
    (target/'dashboard.json').write_text(json.dumps(dashboard,indent=2))
    session=requests.Session();session.headers.update({'Authorization':'Bearer '+token(),'x-goog-user-project':PROJECT})
    results={'metrics':[]}
    for metric in metrics:
        response=session.post(f'https://logging.googleapis.com/v2/projects/{PROJECT}/metrics',json=metric,timeout=20)
        results['metrics'].append({'name':metric['name'],'status':response.status_code,
                                   'error':response.json().get('error',{}).get('message') if response.status_code not in (200,201,409) else None})
        print(metric['name'],response.status_code,flush=True)
    response=session.post(f'https://monitoring.googleapis.com/v1/projects/{PROJECT}/dashboards',json=dashboard,timeout=20)
    results['dashboard']={'status':response.status_code,'body':response.json()}
    path=root/'work/monitoring-state.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(results,indent=2))
    print('Dashboard:',response.status_code,response.json().get('name',response.json().get('error',{}).get('message')))

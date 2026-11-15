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
        {"name":"ridepulse_worker_tasks","filter":worker+' AND jsonPayload.message="task_completed"'},
        {"name":"ridepulse_worker_commits","filter":worker+' AND jsonPayload.message="task_completed" AND jsonPayload.result="committed"'},
        {"name":"ridepulse_worker_errors","filter":worker+' AND jsonPayload.message="task_completed" AND jsonPayload.http_status>=500'},
    ]
    for name,field in [('ridepulse_api_latency_ms','latency_ms'),('ridepulse_sql_latency_ms','sql_ms')]:
        metrics.append({"name":name,"filter":api+f' AND jsonPayload.{field}>=0',
                        "metricDescriptor":{"metricKind":"DELTA","valueType":"DISTRIBUTION","unit":"ms"},
                        "valueExtractor":f"EXTRACT(jsonPayload.{field})",
                        "bucketOptions":{"exponentialBuckets":{"numFiniteBuckets":25,"growthFactor":1.8,"scale":1}}})
    latency=next(m for m in metrics if m['name']=='ridepulse_api_latency_ms')
    latency['metricDescriptor']['labels']=[{'key':'endpoint','valueType':'STRING','description':'Bounded API path'}]
    latency['labelExtractors']={'endpoint':'EXTRACT(jsonPayload.path)'}
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
    for title,numerator,denominator in [('Cache hit ratio','ridepulse_cache_hits','ridepulse_cache_lookups'),
                                        ('Worker retryable error ratio','ridepulse_worker_errors','ridepulse_worker_tasks')]:
        def series(name):
            return {'filter':f'metric.type="logging.googleapis.com/user/{name}" AND resource.type="cloud_run_revision"',
                    'aggregation':{'alignmentPeriod':'300s','perSeriesAligner':'ALIGN_RATE','crossSeriesReducer':'REDUCE_SUM'}}
        i=len(tiles)
        tiles.append({'xPos':(i%2)*6,'yPos':(i//2)*4,'width':6,'height':4,'widget':{'title':title,'xyChart':{
            'dataSets':[{'plotType':'LINE','timeSeriesQuery':{'timeSeriesFilterRatio':{'numerator':series(numerator),'denominator':series(denominator)}}}],
            'yAxis':{'label':'fraction (0–1)','scale':'LINEAR'}}}})
    i=len(tiles)
    tiles.append({'xPos':0,'yPos':(i//2)*4,'width':12,'height':4,'widget':{'title':'Per-endpoint API p95 (logged milliseconds)',
        'xyChart':{'dataSets':[{'plotType':'LINE','timeSeriesQuery':{'timeSeriesFilter':{
            'filter':'metric.type="logging.googleapis.com/user/ridepulse_api_latency_ms" AND resource.type="cloud_run_revision"',
            'aggregation':{'alignmentPeriod':'300s','perSeriesAligner':'ALIGN_PERCENTILE_95','crossSeriesReducer':'REDUCE_MAX','groupByFields':['metric.label.endpoint']}}}}]}}})
    return metrics,{'displayName' :'RidePulse pipeline and serving','mosaicLayout':{'columns':12,'tiles':tiles}}


def alert_configuration(channels=()):
    common={'duration':'300s','trigger':{'count':1},'comparison':'COMPARISON_GT'}
    latency={**common,'filter':'metric.type="logging.googleapis.com/user/ridepulse_api_latency_ms" AND resource.type="cloud_run_revision"',
             'thresholdValue':1000,'aggregations':[{'alignmentPeriod':'300s','perSeriesAligner':'ALIGN_PERCENTILE_95',
                 'crossSeriesReducer':'REDUCE_MAX','groupByFields':['metric.label.endpoint']}]}
    errors={**common,'filter':'metric.type="logging.googleapis.com/user/ridepulse_worker_errors" AND resource.type="cloud_run_revision"',
            'denominatorFilter':'metric.type="logging.googleapis.com/user/ridepulse_worker_tasks" AND resource.type="cloud_run_revision"',
            'thresholdValue':.05}
    rate={'alignmentPeriod':'300s','perSeriesAligner':'ALIGN_RATE','crossSeriesReducer':'REDUCE_SUM'}
    errors.update(aggregations=[rate],denominatorAggregations=[rate])
    return [{'displayName':name,'combiner':'OR','enabled':True,'notificationChannels':list(channels),
             'conditions':[{'displayName':name,'conditionThreshold':condition}],
             'alertStrategy':{'autoClose':'1800s'},
             'documentation':{'content':'RidePulse: inspect structured request/task logs and RUNBOOK.md. Missing data during planned shutdown is not an error.','mimeType':'text/markdown'}}
            for name,condition in [('RidePulse API p95 above 1 second',latency),('RidePulse worker retryable errors above 5%',errors)]]


def apply():
    root=Path(__file__).resolve().parents[1]
    target=root/'infra/monitoring';target.mkdir(parents=True,exist_ok=True)
    metrics,dashboard=configuration()
    (target/'log_metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
    session=requests.Session();session.headers.update({'Authorization':'Bearer '+token(),'x-goog-user-project':PROJECT})
    results={'metrics':[]}
    def request(method,url,body=None):
        r=session.request(method,url,json=body,timeout=30)
        return r,{'status':r.status_code,'body':r.json() if r.content else {}}
    for metric in metrics:
        base=f'https://logging.googleapis.com/v2/projects/{PROJECT}/metrics'
        existing=session.get(base+'/'+metric['name'],timeout=20)
        r,result=request('PUT' if existing.status_code==200 else 'POST',base+'/'+metric['name'] if existing.status_code==200 else base,metric)
        results['metrics'].append({'name':metric['name'],**result})
        print(metric['name'],r.status_code,flush=True)
    channel_base=f'https://monitoring.googleapis.com/v3/projects/{PROJECT}/notificationChannels'
    channels=session.get(channel_base,timeout=20).json().get('notificationChannels',[])
    channel=next((c for c in channels if c.get('displayName')=='RidePulse university email'),None)
    if not channel:
        r,result=request('POST',channel_base,{'type':'email','displayName':'RidePulse university email','labels':{'email_address':'shpa6505@colorado.edu'}})
        results['notification_channel']=result
        channel=result['body'] if r.ok else None
    policies=alert_configuration([channel['name']] if channel else [])
    (target/'alerts.json').write_text(json.dumps(policies,indent=2)+'\n')
    base=f'https://monitoring.googleapis.com/v3/projects/{PROJECT}/alertPolicies'
    existing=session.get(base,timeout=20).json().get('alertPolicies',[])
    results['alerts']=[]
    for policy in policies:
        old=next((x for x in existing if x.get('displayName')==policy['displayName']),None)
        if old:policy['name']=old['name']
        r,result=request('PATCH' if old else 'POST','https://monitoring.googleapis.com/v3/'+old['name'] if old else base,policy)
        results['alerts'].append(result);print(policy['displayName'],r.status_code,flush=True)
    (target/'dashboard.json').write_text(json.dumps(dashboard,indent=2)+'\n')
    base=f'https://monitoring.googleapis.com/v1/projects/{PROJECT}/dashboards'
    existing=session.get(base,timeout=20).json().get('dashboards',[])
    old=next((d for d in existing if d.get('displayName')==dashboard['displayName']),None)
    if old:
        detail=session.get('https://monitoring.googleapis.com/v1/'+old['name'],timeout=20).json()
        dashboard.update(name=old['name'],etag=detail.get('etag',''))
    r,result=request('PATCH' if old else 'POST','https://monitoring.googleapis.com/v1/'+old['name'] if old else base,dashboard)
    results['dashboard']=result
    path=root/'work/monitoring-final-setup.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(results,indent=2)+'\n')
    print('Dashboard',r.status_code,flush=True)
    return results

if __name__=='__main__':apply()

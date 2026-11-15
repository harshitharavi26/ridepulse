"""Capture non-secret experiment settings, not complete service configurations."""
from datetime import datetime,timezone
import json
from pathlib import Path
from cloud_support import gcloud,PROJECT,ACCOUNT


def capture():
    result={'captured_at':datetime.now(timezone.utc).isoformat(),'project':PROJECT,'account':ACCOUNT,'services':{}}
    for name in ('ridepulse-api','ridepulse-worker','ridepulse-dashboard'):
        service=json.loads(gcloud('run','services','describe',name,'--region=us-central1','--format=json'))
        template=service['spec']['template'];container=template['spec']['containers'][0]
        revision=service['status']['latestReadyRevisionName']
        details=json.loads(gcloud('run','revisions','describe',revision,'--region=us-central1','--format=json'))
        annotations=template['metadata'].get('annotations',{})
        safe_env={v['name']:v.get('value') for v in container.get('env',[]) if v['name'] in
                  ('DB_POOL_SIZE','DB_STATEMENT_TIMEOUT_MS','DB_SCHEMA','RIDEPULSE_MAINTAIN_AGGREGATES','RIDEPULSE_CACHE_BYPASS')}
        result['services'][name]={'revision':revision,'image':container['image'],
            'image_digest':details['status'].get('imageDigest'),'resources':container.get('resources'),
            'concurrency':template['spec'].get('containerConcurrency'),'timeout_seconds':template['spec'].get('timeoutSeconds'),
            'min_instances':annotations.get('autoscaling.knative.dev/minScale','0'),
            'max_instances':annotations.get('autoscaling.knative.dev/maxScale'),'settings':safe_env}
    sql=json.loads(gcloud('sql','instances','describe','ridepulse-postgres','--format=json'))
    result['sql']={'version':sql['databaseVersion'],'tier':sql['settings']['tier'],
        'disk_gb':sql['settings']['dataDiskSizeGb'],'disk_autoresize':sql['settings'].get('storageAutoResize'),
        'activation_policy':sql['settings']['activationPolicy']}
    redis=json.loads(gcloud('redis','instances','describe','ridepulse-cache','--region=us-central1','--format=json'))
    result['redis']={key:redis.get(key) for key in ('tier','memorySizeGb','redisVersion','state')}
    path=Path('experiments/results/environment.json');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2));print(path,flush=True)


if __name__=='__main__':capture()

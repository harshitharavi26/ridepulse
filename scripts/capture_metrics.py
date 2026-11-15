"""Retain native resource telemetry over the measured experiment window."""
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import requests
from cloud_support import PROJECT,token


def capture():
    root=Path('experiments/results')
    start=json.loads((root/'api/reference.json').read_text())['created_at']
    runs=json.loads((root/'workers.json').read_text())['runs']
    end=max(datetime.fromisoformat(run['started_at'])+timedelta(seconds=run['seconds']) for run in runs).isoformat()
    headers={'Authorization':'Bearer '+token()}
    filters={
        'sql_cpu':'metric.type="cloudsql.googleapis.com/database/cpu/utilization" AND resource.labels.database_id="'+PROJECT+':ridepulse-postgres"',
        'sql_connections':'metric.type="cloudsql.googleapis.com/database/postgresql/num_backends" AND resource.labels.database_id="'+PROJECT+':ridepulse-postgres"',
    }
    result={'start':start,'end':end,'series':{}}
    for name,metric_filter in filters.items():
        series=[];page=None
        while True:
            params={'filter':metric_filter,'interval.startTime':start,'interval.endTime':end,'view':'FULL','pageSize':1000}
            if page:params['pageToken']=page
            response=requests.get(f'https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries',headers=headers,params=params,timeout=30)
            response.raise_for_status();body=response.json();series.extend(body.get('timeSeries',[]))
            page=body.get('nextPageToken')
            if not page:break
        result['series'][name]=series
    (root/'native-metrics.json').write_text(json.dumps(result,indent=2))
    print({name:sum(len(s.get('points',[])) for s in series) for name,series in result['series'].items()})


if __name__=='__main__':capture()

"""Private API load mix; credentials stay in the process environment."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

from locust import HttpUser,between,events,task

EXPECTED=json.loads(Path(os.environ['RIDEPULSE_EXPECTED_PATH']).read_text())
COUNTS=Counter()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class AnalyticsUser(HttpUser):
    wait_time=between(0.05,0.15)

    @task
    def historical_query(self):
        import random
        entry=random.choice(EXPECTED['queries'])
        headers={'Authorization':'Bearer '+os.environ['RIDEPULSE_BENCHMARK_TOKEN']}
        with self.client.get(entry['path'],params=entry['params'],headers=headers,name=entry['name'],catch_response=True,timeout=35) as response:
            COUNTS['requests']+=1
            if response.status_code!=200:
                COUNTS['http_errors']+=1
                response.failure('HTTP '+str(response.status_code));return
            try:
                payload=response.json()
                COUNTS['cache_'+payload['meta']['cache']]+=1
                if fingerprint(payload['data'])!=entry['data_sha256']:
                    COUNTS['result_mismatches']+=1
                    response.failure('Result differs from fixed-data reference')
                if payload['meta'].get('dataset_versions')!=entry['dataset_versions']:
                    COUNTS['version_drift']+=1
                    response.failure('Dataset version changed during experiment')
            except (ValueError,KeyError,TypeError):
                COUNTS['invalid_responses']+=1
                response.failure('Invalid response contract')


@events.quitting.add_listener
def save_results(environment,**kwargs):
    Path(os.environ['RIDEPULSE_COUNTERS_PATH']).write_text(json.dumps(dict(COUNTS),indent=2))

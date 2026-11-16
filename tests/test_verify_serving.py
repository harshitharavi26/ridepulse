"""Recording preflight regression checks; never starts paid resources."""
from collections import Counter
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verify_serving import verify_api

class PreflightTests(unittest.TestCase):
    def responses(self,cache='available',hit=True,stale=False):
        calls=Counter()
        def get(url,params,**kwargs):
            key=(url,tuple(sorted(params.items())));calls[key]+=1
            if url.endswith('/health/ready'):body={'cache':cache}
            elif '/health/' in url:body={'status':'alive'}
            elif url.endswith('/coverage'):
                body={'data':[],'meta':{'latest_local_date':'2026-12-05' if params['city']=='nyc' else '2026-12-03'}}
            elif url.endswith('/latest'):
                body={'data':[{'is_stale':stale,'collected_at':'2026-12-05T12:00:00Z'}]}
            else:body={'data':[{'value':'stable'}],'meta':{'cache':'hit' if hit and calls[key]>1 else 'miss'}}
            return Mock(status_code=200,elapsed=timedelta(milliseconds=5),json=lambda:body)
        return get
    def test_latest_city_dates_and_both_route_cache_variants_are_checked(self):
        with patch('verify_serving.requests.get',side_effect=self.responses()):
            checks=verify_api('https://test',{},require_fresh=True)
        low=[c for c in checks if c['path'].endswith('/low')]
        self.assertEqual([(c['params']['city'],c['params']['start_date'],c['params']['end_date']) for c in low],
                         [('nyc','2026-12-05','2026-12-06'),('boulder','2026-12-03','2026-12-04')])
        hits=[c for c in checks if c.get('cache')=='hit']
        self.assertEqual(len(hits),4)
        self.assertTrue(any(c['params'].get('hide_round_trips') is True for c in hits))
    def test_recording_rejects_missing_redis(self):
        with patch('verify_serving.requests.get',side_effect=self.responses(cache='unavailable')):
            with self.assertRaisesRegex(AssertionError,'Redis is not available'):
                verify_api('https://test',{},require_fresh=True)
    def test_ready_cache_alone_does_not_prove_hits(self):
        with patch('verify_serving.requests.get',side_effect=self.responses(hit=False)):
            with self.assertRaisesRegex(AssertionError,'warmed Redis hit'):
                verify_api('https://test',{},require_cache=True)
    def test_cache_only_check_allows_stale_readings_but_fresh_check_rejects_them(self):
        with patch('verify_serving.requests.get',side_effect=self.responses(stale=True)):
            verify_api('https://test',{},require_cache=True)
        with patch('verify_serving.requests.get',side_effect=self.responses(stale=True)):
            with self.assertRaisesRegex(AssertionError,'No fresh observations'):
                verify_api('https://test',{},require_fresh=True)

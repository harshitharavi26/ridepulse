"""Actual worker concurrency must come from observed task intervals."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from benchmark_workers import activity


class ActivityTests(unittest.TestCase):
    def test_overlapping_tasks_and_reused_instances(self):
        entries=[]
        for task,instance,start,end in [('a','one',0,5),('b','two',1,3),('c','two',4,6)]:
            for second,event in [(start,'task_started'),(end,'task_completed')]:
                entries.append({'timestamp':f'2026-11-14T00:00:{second:02d}Z','labels':{'instanceId':instance},
                                'jsonPayload':{'task_id':task,'message':event}})
        self.assertEqual(activity(entries),{'observed_peak_active_instances':2,'distinct_instances':2,'task_intervals':3})

    def test_missing_instance_evidence_is_not_invented(self):
        self.assertEqual(activity([])['observed_peak_active_instances'],0)

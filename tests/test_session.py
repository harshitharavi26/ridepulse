"""Cloud SQL's RUNNABLE state also represents owner-stopped instances."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
spec=importlib.util.spec_from_file_location('ridepulse_session',Path(__file__).resolve().parents[1]/'scripts/session.py')
session=importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class SessionPolicyTests(unittest.TestCase):
    def test_stopped_sql_can_remain_runnable(self):
        replies=[json.dumps({'name':'stop-operation'}),json.dumps({'status':'DONE'}),
                 json.dumps({'state':'RUNNABLE','settings':{'activationPolicy':'NEVER'}})]
        with patch.object(session,'gcloud',side_effect=replies) as call:
            session.set_sql_policy('NEVER')
        self.assertIn('--activation-policy=NEVER',call.call_args_list[0].args)
        self.assertEqual(call.call_args_list[1].args[:3],('sql','operations','describe'))

    def test_failed_policy_change_is_not_success(self):
        replies=[json.dumps({'name':'stop-operation'}),json.dumps({'status':'DONE','error':{'errors':[{'code':'FAILED'}]}})]
        with patch.object(session,'gcloud',side_effect=replies):
            with self.assertRaisesRegex(RuntimeError,'operation failed'):
                session.set_sql_policy('NEVER')

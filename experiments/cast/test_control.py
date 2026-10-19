import copy
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from control import Blocked, DiagnosticControl
from service import handler
from http.server import ThreadingHTTPServer


def fixture():
    return {'topologyFresh': True, 'revision': 1, 'topologyAt': 1,
        'rooms': [{'id': 'a', 'zoneId': 'za', 'rendererIds': ['pair-a'], 'fresh': True},
                  {'id': 'b', 'zoneId': 'zb', 'rendererIds': ['pair-b'], 'fresh': True}],
        'zones': [{'id':'za','roomIds':['a']}, {'id':'zb','roomIds':['b']}],
        'renderers': [{'id':i,'transport':'STOPPED','fresh':True} for i in ('pair-a','pair-b','za','zb')]}


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = DiagnosticControl({'rooms':[{'id':'a','name':'Synthetic A'},
            {'id':'b','name':'Synthetic B'}]}, self.temp.name)

    def test_grouping_never_mutates_and_reports_actual_topology(self):
        snapshot = fixture()
        before = copy.deepcopy(snapshot)
        for enabled in (True, False):
            with self.assertRaises(Blocked) as caught:
                self.control.group(enabled, snapshot)
            self.assertEqual(caught.exception.code, 'cast_compatibility_blocked')
            self.assertEqual(caught.exception.status, 501)
        self.assertEqual(snapshot, before)
        self.assertEqual(self.control.status(snapshot)['grouping'], 'individual')
        snapshot['zones'] = [{'id':'group','roomIds':['a','b']}]
        self.assertEqual(self.control.status(snapshot)['grouping'], 'all_configured')
        snapshot['zones'][0]['roomIds'].append('unconfigured')
        self.assertEqual(self.control.status(snapshot)['grouping'], 'mixed_or_external_members')
        snapshot['topologyFresh'] = False
        self.assertEqual(self.control.status(snapshot)['grouping'], 'unknown')
        self.assertFalse(self.control.status(snapshot)['pending'])

    def test_group_change_requires_confirmed_stopped_in_all_members(self):
        for state in ('PLAYING','PAUSED_PLAYBACK','TRANSITIONING','UNKNOWN'):
            snapshot = fixture()
            snapshot['renderers'][0]['transport'] = state
            with self.assertRaises(Blocked) as caught:
                self.control.group(True, snapshot)
            self.assertEqual(caught.exception.code, 'playback_not_confirmed_stopped')
        snapshot['renderers'][0].update(transport='STOPPED', fresh=False)
        self.assertFalse(self.control.status(snapshot)['playbackStopped'])

    def test_selection_only_configured_logical_ids_and_preserves_stereo(self):
        snapshot = fixture()
        before = copy.deepcopy(snapshot)
        self.control.select('b')
        self.assertEqual(self.control.status(snapshot)['selectedRoom'], 'b')
        self.assertEqual(self.control.members, frozenset(('a','b')))
        with self.assertRaises(Blocked):
            self.control.select('pair-a')
        self.assertEqual(snapshot, before)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_unavailable_and_unknown_never_count_as_stopped(self):
        snapshot = fixture()
        snapshot['rooms'].pop()
        status = self.control.status(snapshot)
        self.assertFalse(status['playbackStopped'])
        self.assertEqual(status['grouping'], 'unknown')
        self.assertFalse(next(r for r in status['rooms'] if r['id']=='b')['available'])

    def test_recovery_snapshot_private_explicit_and_never_auto_restored(self):
        for _ in range(3):
            self.control.status(fixture())
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.control.save_snapshot(fixture())
        path = next((Path(self.temp.name)/'recovery').iterdir())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text())['rooms'][0]['rendererIds'], ['pair-a'])
        with self.assertRaises(Blocked):
            self.control.save_snapshot({'topologyFresh':False})

    def test_http_auth_group_rejection_and_gui_safety(self):
        with patch.dict(os.environ, {'CAST_API_TOKEN':'x'*32}), patch('service.observer_snapshot', return_value=fixture()):
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.control, Path(self.temp.name)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection(*server.server_address)
                conn.request('GET','/api/status'); response = conn.getresponse()
                self.assertEqual(response.status,401);response.read()
                conn.request('POST','/api/group',json.dumps({'enabled':True}),
                    {'Authorization':'Bearer '+'x'*32})
                response=conn.getresponse();self.assertEqual(response.status,501)
                self.assertFalse(json.loads(response.read())['mutationSent'])
                conn.request('GET','/');response=conn.getresponse();page=response.read().decode()
                self.assertIn('Group all rooms',page)
                self.assertIn('type="checkbox" disabled',page)
                conn.close()
            finally:
                server.shutdown();server.server_close();thread.join()
        self.assertNotIn('innerHTML', (Path(__file__).parent/'ui.js').read_text())


if __name__ == '__main__':
    unittest.main()

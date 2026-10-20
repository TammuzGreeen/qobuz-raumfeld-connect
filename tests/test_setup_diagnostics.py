"""Setup failures must be actionable, redacted and transactionally safe."""
import errno
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer
from qobuz.service import Service, save_json, load_json
from qobuz.setup_diagnostics import StorageError, advertisement_diagnostics


class StorageTests(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, 'Requires an unprivileged runtime UID')
    def test_real_filesystem_permission_failures_are_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'credentials.json'
            save_json(path,{'synthetic':True})
            path.chmod(0)
            try:
                with self.assertRaises(StorageError) as caught:
                    load_json(path,{})
                self.assertEqual(caught.exception.detail['code'],'storage_permission_denied')
            finally:
                path.chmod(0o600)
            Path(tmp).chmod(0o500)
            try:
                with self.assertRaises(StorageError) as caught:
                    save_json(path,{'replace':True})
                self.assertEqual(caught.exception.detail['code'],'storage_permission_denied')
            finally:
                Path(tmp).chmod(0o700)
            self.assertEqual(load_json(path,{}),{'synthetic':True})

    def test_atomic_private_write_and_failed_replace_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            save_json(path, {'old': True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for code, expected in [(errno.EACCES, 'storage_permission_denied'),
                                   (errno.EROFS, 'storage_read_only'), (errno.ENOSPC, 'storage_full')]:
                with patch('qobuz.setup_diagnostics.os.replace', side_effect=OSError(code, 'PRIVATE_TOKEN_URL')):
                    with self.assertRaises(StorageError) as caught:
                        save_json(path, {'new': True})
                self.assertEqual(caught.exception.detail['code'], expected)
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertEqual(load_json(path, {}), {'old': True})
                self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_read_permission_and_invalid_json_are_not_missing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'credentials.json'
            self.assertEqual(load_json(path, {}), {})
            with patch('pathlib.Path.read_text', side_effect=PermissionError(errno.EACCES, 'PRIVATE')):
                with self.assertRaises(StorageError) as caught:
                    load_json(path, {})
            self.assertEqual(caught.exception.detail, {'file':'credentials.json', 'operation':'read',
                                                       'code':'storage_permission_denied'})
            path.write_text('{PRIVATE malformed')
            with self.assertRaises(StorageError) as caught:
                load_json(path, {})
            self.assertEqual(caught.exception.detail['code'], 'storage_invalid_json')
            self.assertEqual(path.read_text(), '{PRIVATE malformed')


class SetupDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.node = MagicMock()
        self.state = {'topologyFresh':True, 'rooms':[{'id':'synthetic-room', 'name':'PRIVATE_NAME',
            'rendererIds':['physical'], 'fresh':True, 'zoneId':None}], 'zones':[]}
        self.node.state = AsyncMock(return_value=self.state)
        self.service = Service(self.node, 'x'*32, self.tmp.name, '192.0.2.2')
        self.client = TestClient(TestServer(self.service.app))
        await self.client.start_server()
        self.headers = {'Authorization':'Bearer '+'x'*32}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_config_permission_error_is_actionable_and_keeps_memory_and_sessions(self):
        self.service.stop_receivers = AsyncMock()
        before = dict(self.service.settings)
        output = io.StringIO()
        error = StorageError('/private/path/config.json', 'write', 'storage_permission_denied')
        with redirect_stdout(output), patch('qobuz.service.save_json', side_effect=error):
            response = await self.client.post('/api/config', headers=self.headers,
                json={'rooms':['synthetic-room'],'quality':6})
        self.assertEqual(response.status,503)
        data = await response.json()
        self.assertEqual(data['error'], 'storage_permission_denied')
        self.assertIn('UID/GID',data['message'])
        self.assertEqual(self.service.settings,before)
        self.service.stop_receivers.assert_not_awaited()
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertNotIn('/private/path',output.getvalue()+json.dumps(data))
        self.assertNotIn('PRIVATE_NAME',output.getvalue())

    async def test_successful_save_clears_storage_error(self):
        self.service.storage_failure(StorageError('config.json','write','storage_permission_denied'))
        response = await self.client.post('/api/config', headers=self.headers,
            json={'rooms':['synthetic-room'],'quality':6})
        self.assertEqual(response.status,200)
        self.assertEqual(self.service.storage_errors,{})
        self.assertEqual(load_json(Path(self.tmp.name)/'config.json',{})['rooms'][0]['id'],'synthetic-room')

    async def test_failed_token_persistence_never_leaves_closed_api_connected(self):
        save_json(Path(self.tmp.name)/'credentials.json',{'user_id':'synthetic','user_auth_token':'PRIVATE_TOKEN'})
        api = MagicMock(user_auth_token='PRIVATE_REFRESHED_TOKEN')
        api.login_with_token = AsyncMock(return_value=True)
        api.__aexit__ = AsyncMock()
        with patch('qobuz.service.QobuzAPIClient',return_value=api), patch('qobuz.service.save_json',
            side_effect=StorageError('credentials.json','write','storage_permission_denied')):
            await self.service.authenticate()
        self.assertIsNone(self.service.api)
        self.assertEqual(self.service.auth_status,'storage_error')
        self.assertGreater(self.service.retry_at,time.monotonic())
        api.__aexit__.assert_awaited_once()
        self.assertEqual(load_json(Path(self.tmp.name)/'credentials.json',{})['user_auth_token'],'PRIVATE_TOKEN')
        self.node.control.assert_not_called()

    async def test_unreadable_credentials_not_misreported_as_speaker_failure(self):
        with patch('qobuz.service.load_json',side_effect=StorageError('credentials.json','read','storage_permission_denied')):
            await self.service.authenticate()
        self.assertEqual(self.service.auth_status,'storage_error')
        self.assertIsNone(self.service.state_error)

    async def test_callback_storage_failure_keeps_prior_auth_and_does_not_release_sessions(self):
        self.service.nonces['synthetic-nonce'] = time.monotonic()+60
        self.service.stop_receivers = AsyncMock()
        with patch('qobuz.service.exchange_code',AsyncMock(return_value={'user_auth_token':'PRIVATE'})), \
             patch('qobuz.service.save_json',side_effect=StorageError('credentials.json','write','storage_permission_denied')):
            response = await self.client.get('/oauth/callback?state=synthetic-nonce&code_autorisation=PRIVATE')
        self.assertEqual(response.status,503)
        self.service.stop_receivers.assert_not_awaited()
        self.assertNotIn('PRIVATE',await response.text())

    async def test_startup_invalid_json_is_visible_and_preserved(self):
        path=Path(self.tmp.name)/'config.json';path.write_text('{PRIVATE')
        output=io.StringIO()
        with redirect_stdout(output):
            service=Service(self.node,'x'*32,self.tmp.name,'192.0.2.2')
            service.report_state()
        self.assertEqual(service.storage_errors['config.json']['code'],'storage_invalid_json')
        self.assertEqual(path.read_text(),'{PRIVATE')
        self.assertNotIn('PRIVATE',output.getvalue())

    async def test_each_advertisement_gate_is_explained_without_changing_admission(self):
        self.service.settings['rooms']=[{'id':'synthetic-room','name':'PRIVATE_NAME','port':8790},
                                        {'id':'missing','name':'PRIVATE_OTHER','port':8791}]
        self.service.state=self.state
        diagnostic=advertisement_diagnostics(self.service,False)
        self.assertIn('authentication_required',diagnostic['blockers'])
        self.assertIn('lan_address_not_local',diagnostic['blockers'])
        self.assertIn('configured_room_not_discovered',diagnostic['rooms'][1]['blockers'])
        self.state['rooms'][0].update(fresh=False,zoneId='zone')
        self.state['zones']=[{'id':'zone','roomIds':['synthetic-room','other']}]
        reasons=advertisement_diagnostics(self.service,True)['rooms'][0]['blockers']
        self.assertIn('grouped_or_unresolved_zone',reasons)
        self.assertIn('incomplete_renderer_observation',reasons)
        self.service.receiver_errors['synthetic-room']='receiver_port_in_use'
        self.assertIn('receiver_port_in_use',advertisement_diagnostics(self.service,True)['rooms'][0]['blockers'])
        self.node.control.assert_not_called()

    async def test_logs_are_transition_only_and_redacted(self):
        self.service.settings['rooms']=[{'id':'PRIVATE_ID','name':'PRIVATE_NAME','port':8790}]
        output=io.StringIO()
        with redirect_stdout(output):
            self.service.report_state();self.service.report_state()
            self.service.auth_status='retrying_login'
            self.service.report_state()
        self.assertEqual(len(output.getvalue().splitlines()),2)
        self.assertNotIn('PRIVATE',output.getvalue())
        self.assertNotIn('192.0.2.2',output.getvalue())
        self.assertIn('room_01',output.getvalue())


if __name__=='__main__':
    unittest.main()

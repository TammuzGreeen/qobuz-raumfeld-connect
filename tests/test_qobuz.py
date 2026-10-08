import asyncio
import errno
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp.test_utils import TestClient, TestServer
from qobuz.backend import RaumfeldBackend, AudioRelay, SeekUnsupported
from qobuz.player import RaumfeldPlayer
from qobuz_proxy.playback import QobuzQueue
from qobuz_proxy.backends.types import PlaybackState
from qobuz.client import OwnershipLost
from qobuz.receiver import Receiver
from qobuz.service import Service, save_json, load_json, local_address
from qobuz_proxy.backends.types import BackendTrackMetadata
from qobuz_proxy.connect.types import ConnectTokens, JWTConnectToken


class BackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.control = AsyncMock(return_value={'token':'lease-1'})
        self.client.state = AsyncMock(return_value={'rooms':[], 'renderers':[]})
        self.relay = MagicMock()
        self.relay.register.return_value = 'http://192.0.2.2:8790/audio/' + 'a'*32
        self.backend = RaumfeldBackend(self.client, 'room', 'Room', self.relay)

    async def test_initial_stop_and_volume_do_not_touch_native_audio(self):
        await self.backend.stop()
        await self.backend.set_volume(20)
        self.client.control.assert_not_called()
        with self.assertRaises(OwnershipLost):
            await self.backend.pause()

    async def test_play_requires_selection_and_routes_through_node(self):
        await self.backend.select('explicit-selection')
        await self.backend.play('https://audio.qobuz.test/file', BackendTrackMetadata('1', title='Track'))
        self.assertEqual(self.client.control.call_args.args, ('room','play'))
        self.assertEqual(self.client.control.call_args.kwargs['token'],'lease-1')
        self.assertTrue(self.backend.started)

    async def test_command_failure_revokes_and_notifies_cloud_without_stop(self):
        await self.backend.select('explicit-selection')
        self.backend.on_external=AsyncMock()
        self.client.control.side_effect=OwnershipLost('spotify')
        with self.assertRaises(OwnershipLost):
            await self.backend.pause()
        self.assertIsNone(self.backend.token)
        self.backend.on_external.assert_awaited_once()
        self.assertFalse(any(c.args[1]=='stop' for c in self.client.control.call_args_list))
        self.assertEqual(self.backend.last_playback_command, {'action': 'pause', 'result': 'failed'})
        self.assertEqual(self.backend.last_control_error, 'control_request_failed')

    async def test_disconnect_releases_permission_without_stopping_audio(self):
        await self.backend.select('explicit-selection')
        await self.backend.disconnect()
        self.assertIsNone(self.backend.token)
        self.assertEqual(self.client.control.call_args.args, ('room','release'))

    async def test_unsupported_seek_retains_started_backend_and_does_not_notify_external(self):
        await self.backend.select('explicit-selection')
        await self.backend.play('https://audio.qobuz.test/file', BackendTrackMetadata('1', title='Track'))
        self.backend.on_external = AsyncMock()
        self.client.control.return_value = {'accepted': False, 'applied': False,
                                            'error': 'seek_mode_not_supported', 'upnpErrorCode': 710}
        with self.assertRaises(SeekUnsupported):
            await self.backend.seek(65000)
        self.assertTrue(self.backend.started)
        self.assertEqual(self.backend.token, 'lease-1')
        self.backend.on_external.assert_not_called()
        self.assertEqual(self.backend.last_playback_command, {'action': 'seek', 'result': 'unsupported'})
        self.assertEqual(self.backend.last_control_error, 'seek_mode_not_supported')
        self.assertFalse(any(c.args[1] in ('stop', 'release') for c in self.client.control.call_args_list))

    async def test_actual_upstream_initial_play_rejection_reports_observed_not_requested_position(self):
        await self.backend.select('explicit-selection')
        self.backend.started = True
        self.client.control.side_effect = lambda room, action, **kwargs: (
            {'accepted': False, 'applied': False, 'error': 'seek_mode_not_supported', 'upnpErrorCode': 710}
            if action == 'seek' else {'owned': True})
        self.client.state.return_value = {'rooms': [{'id': 'room', 'fresh': True, 'owned': True,
                                                    'zoneId': 'zone', 'rendererIds': ['physical']}],
                                          'renderers': [{'id': 'zone', 'fresh': True, 'positionMs': 1200}]}
        player = RaumfeldPlayer(queue=QobuzQueue(), metadata_service=MagicMock(), backend=self.backend)
        player._current_track = MagicMock()
        player._state = PlaybackState.STOPPED
        player._start_playback = AsyncMock(return_value=True)
        player._send_state_update = AsyncMock()
        self.assertTrue(await player.play(65000))
        self.assertEqual(player._position_value_ms, 1200)
        player._send_state_update.assert_awaited_once()
        self.assertTrue(self.backend.started)
        self.assertEqual(self.backend.token, 'lease-1')
        self.assertEqual([c.args[1] for c in self.client.control.call_args_list], ['select', 'seek', 'heartbeat'])

    async def test_unsupported_seek_does_not_hide_subsequent_ownership_loss(self):
        await self.backend.select('explicit-selection')
        self.backend.started = True
        self.client.control.side_effect = [
            {'accepted': False, 'applied': False, 'error': 'seek_mode_not_supported', 'upnpErrorCode': 710},
            OwnershipLost('ownership_lost')]
        player = RaumfeldPlayer(queue=QobuzQueue(), metadata_service=MagicMock(), backend=self.backend)
        player._current_track = MagicMock()
        player._state = PlaybackState.STOPPED
        player._start_playback = AsyncMock(return_value=True)
        with self.assertRaises(OwnershipLost):
            await player.play(65000)
        self.assertIsNone(self.backend.token)

    async def test_actual_upstream_user_seek_rejected_without_falsifying_position(self):
        await self.backend.select('explicit-selection')
        self.backend.started = True
        self.client.control.return_value = {'accepted': False, 'applied': False,
                                            'error': 'seek_mode_not_supported', 'upnpErrorCode': 710}
        player = RaumfeldPlayer(queue=QobuzQueue(), metadata_service=MagicMock(), backend=self.backend)
        player._current_track = MagicMock()
        player._current_duration_ms = 180000
        player._state = PlaybackState.PLAYING
        player._position_value_ms = 1200
        player._send_state_update = AsyncMock()
        with self.assertLogs('qobuz_proxy', level='ERROR'):
            self.assertFalse(await player.seek(65000))
        self.assertEqual(player._position_value_ms, 1200)
        player._send_state_update.assert_not_called()
        self.assertEqual(self.backend.token, 'lease-1')

    async def test_observed_position_requires_fresh_owned_renderer_evidence(self):
        for owned, fresh in [(False, True), (True, False)]:
            await self.backend.select('explicit-selection')
            self.client.state.return_value = {'rooms': [{'id': 'room', 'fresh': True, 'owned': owned,
                                                        'zoneId': 'zone', 'rendererIds': ['physical']}],
                                              'renderers': [{'id': 'zone', 'fresh': fresh, 'positionMs': 1200}]}
            with self.assertRaises(OwnershipLost):
                await self.backend.observed_position()
            self.assertIsNone(self.backend.token)


class SetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.node=MagicMock()
        self.node.state=AsyncMock(return_value={'rooms':[{'id':'r','name':'Living'}]})
        self.service=Service(self.node,'x'*32,self.tmp.name,'192.0.2.2')
        self.client=TestClient(TestServer(self.service.app))
        await self.client.start_server()
        self.headers={'Authorization':'Bearer '+'x'*32}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_setup_requires_authentication_and_does_not_expose_credentials(self):
        self.assertEqual((await self.client.get('/api/status')).status,401)
        response=await self.client.get('/api/status',headers=self.headers)
        self.assertEqual(response.status,200)
        self.assertNotIn('user_auth_token',await response.text())
        self.assertEqual((await self.client.get('/')).status,200)

    async def test_room_configuration_persists_stable_ports(self):
        for quality in (6,7):
            response=await self.client.post('/api/config',headers=self.headers,json={'rooms':['r'],'quality':quality})
            self.assertEqual(response.status,200)
            self.assertEqual(load_json(Path(self.tmp.name)/'config.json',{})['rooms'][0]['port'],8790)

    async def test_oauth_callback_requires_single_use_nonce(self):
        response=await self.client.get('/oauth/callback?state=bad&code_autorisation=x')
        self.assertEqual(response.status,400)
        response=await self.client.post('/api/login',headers=self.headers,json={})
        self.assertIn('qobuz.com/signin/oauth',(await response.json())['url'])
        nonce=next(iter(self.service.nonces))
        with patch('qobuz.service.exchange_code',AsyncMock(return_value={'user_id':'1','user_auth_token':'secret'})):
            response=await self.client.get('/oauth/callback?state='+nonce+'&code_autorisation=x',allow_redirects=False)
            self.assertEqual(response.status,302)
            response=await self.client.get('/oauth/callback?state='+nonce+'&code_autorisation=x')
            self.assertEqual(response.status,400)


class ReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_acknowledges_only_after_admission_and_redacts_errors(self):
        node = MagicMock()
        receiver = Receiver({'id': 'r', 'name': 'Room', 'port': 8790}, node,
                            MagicMock(app_id='app'), '192.0.2.2', 6)
        entered, admit = asyncio.Event(), asyncio.Event()
        async def selected(tokens):
            entered.set()
            await admit.wait()
            receiver.discovery.set_session(tokens)
            return True
        receiver.select = AsyncMock(side_effect=selected)
        await receiver.discovery._start_http_server()
        client = TestClient(TestServer(receiver.app))
        await client.start_server()
        payload = {'session_id': 'new-test-session', 'jwt_qconnect': {
            'jwt': 'synthetic-test-token', 'exp': 9999999999, 'endpoint': 'wss://example.test'}}
        pending = asyncio.create_task(client.post('/streamcore/connect-to-qconnect', json=payload))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertFalse(pending.done())
            self.assertIsNone(receiver.discovery.get_received_tokens())
            admit.set()
            response = await pending
            self.assertEqual(response.status, 200)
            self.assertEqual(receiver.discovery._current_session_id, 'new-test-session')
            receiver.select.side_effect = RuntimeError('PRIVATE token and endpoint')
            response = await client.post('/streamcore/connect-to-qconnect', json=payload)
            self.assertEqual(response.status, 503)
            self.assertEqual(await response.json(), {'error': 'connect_request_failed'})
            node.control.assert_not_called()
        finally:
            admit.set()
            await client.close()
            await receiver.stop()

    async def test_actual_http_replay_does_not_reinstall_released_session(self):
        node = MagicMock()
        node.control = AsyncMock()
        receiver = Receiver({'id': 'r', 'name': 'Room', 'port': 8790}, node,
                            MagicMock(app_id='app'), '192.0.2.2', 6)
        session = 'released-test-session'
        receiver.seen[hashlib.sha256(session.encode()).hexdigest()] = time.monotonic() + 86400
        await receiver.discovery._start_http_server()
        client = TestClient(TestServer(receiver.app))
        await client.start_server()
        try:
            response = await client.post('/streamcore/connect-to-qconnect', json={
                'session_id': session,
                'jwt_qconnect': {'jwt': 'synthetic-test-token', 'exp': 9999999999,
                                 'endpoint': 'wss://example.test'}})
            self.assertEqual(response.status, 409)
            self.assertEqual(await response.json(), {'error': 'selection_rejected'})
            await asyncio.sleep(0)
            info = await (await client.get('/streamcore/get-connect-info')).json()
            self.assertEqual(info['current_session_id'], '')
            self.assertFalse(receiver.diagnostics()['sessionPresent'])
            self.assertEqual(receiver.diagnostics()['error'], 'selection_replayed_or_limit')
            node.control.assert_not_called()
            response = await client.post('/streamcore/connect-to-qconnect', json=[])
            self.assertEqual(response.status, 400)
        finally:
            await client.close()
            await receiver.stop()

    async def test_protocol_error_and_message_counts_are_numeric_only_and_do_not_control_speakers(self):
        node = MagicMock()
        receiver = Receiver({'id': 'r', 'name': 'Room', 'port': 8790}, node,
                            MagicMock(app_id='app'), '192.0.2.2', 6)
        message = MagicMock()
        message.HasField.return_value = True
        message.error.code = 7
        message.error.message = 'PRIVATE JWT and stream URL'
        receiver.dispatch(receiver.note_protocol_error, 1, message)
        diagnostic = receiver.diagnostics()
        self.assertEqual(diagnostic['messageCounts'], {'1': 1})
        self.assertEqual(diagnostic['protocolError'], {'code': 7})
        self.assertNotIn('PRIVATE', json.dumps(diagnostic))
        diagnostic['protocolError']['code'] = 999
        self.assertEqual(receiver.diagnostics()['protocolError']['code'], 7)
        node.control.assert_not_called()
        await receiver.stop()

    async def test_session_failure_diagnostics_do_not_expose_exception_text_or_grant_control(self):
        node = MagicMock()
        node.control = AsyncMock(side_effect=RuntimeError('PRIVATE signed URL and JWT'))
        receiver = Receiver({'id': 'r', 'name': 'Room', 'port': 8790}, node,
                            MagicMock(app_id='app'), '192.0.2.2', 6)
        tokens = ConnectTokens(session_id='new-session', ws_token=JWTConnectToken('PRIVATE-JWT', 9999999999, 'wss://example.test'))
        await receiver.select(tokens)
        diagnostic = receiver.diagnostics()
        self.assertEqual(diagnostic['stage'], 'selecting_backend')
        self.assertEqual(diagnostic['error'], 'session_start_failed')
        self.assertFalse(diagnostic['backendSelected'])
        self.assertFalse(diagnostic['sessionPresent'])
        self.assertNotIn('PRIVATE', json.dumps(diagnostic))
        self.assertEqual([c.args[1] for c in node.control.call_args_list], ['select'])
        await receiver.stop()

    async def test_real_upstream_player_wiring_and_handshake_replay_guard(self):
        node=MagicMock()
        node.control=AsyncMock(return_value={'token':'lease-1'})
        node.state=AsyncMock(return_value={'rooms':[{'id':'r','fresh':True,'zoneId':'z','rendererIds':['p']}],
                                         'renderers':[{'id':'z','transport':'STOPPED'}]})
        api=MagicMock(app_id='app')
        api.get_ws_token=AsyncMock()
        receiver=Receiver({'id':'r','name':'Room','port':8790},node,api,'192.0.2.2',6)
        tokens=ConnectTokens(session_id='new-session',ws_token=JWTConnectToken('token',9999999999,'wss://example.test'))
        ws=MagicMock()
        ws.start=AsyncMock();ws.stop=AsyncMock();ws.send_state_update=AsyncMock()
        with patch('qobuz.receiver.WsManager',return_value=ws):
            await receiver.select(tokens)
            self.assertIsNotNone(receiver.player)
            self.assertIsNotNone(receiver.backend.token)
            diagnostic = receiver.diagnostics()
            self.assertEqual(diagnostic['stage'], 'session_started')
            self.assertTrue(diagnostic['backendSelected'])
            self.assertFalse(diagnostic['playbackStarted'])
            self.assertNotIn('lease-1', json.dumps(diagnostic))
            self.assertNotIn('new-session', json.dumps(diagnostic))
            before = len(node.control.call_args_list)
            receiver.discovery.set_session(tokens)
            await receiver.select(tokens)
            self.assertEqual(len(node.control.call_args_list), before)
            self.assertEqual(receiver.backend.token, 'lease-1')
            self.assertIsNotNone(receiver.discovery.get_received_tokens())
            await receiver.backend.external()
            self.assertEqual(receiver.diagnostics()['stage'], 'ownership_released')
            before=len(node.control.call_args_list)
            # Actual upstream HTTP discovery stores incoming tokens before
            # calling Receiver.connected, including replayed dead sessions.
            receiver.discovery.set_session(tokens)
            await receiver.select(tokens)
            self.assertEqual(len(node.control.call_args_list),before)
            self.assertIsNone(receiver.discovery.get_received_tokens())
            self.assertEqual(receiver.discovery._current_session_id, '')
            self.assertEqual(receiver.diagnostics()['error'], 'selection_replayed_or_limit')
            await receiver.stop()
        self.assertFalse(any(c.args[1] in ('play','stop','volume') for c in node.control.call_args_list))


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.node = MagicMock()
        self.node.control = AsyncMock()
        self.state = {'topologyFresh': True, 'topologyAt': 100, 'observedAt': 101,
                      'rooms': [{'id': 'r', 'name': 'Room', 'source': 'spotify',
                                 'fresh': True, 'zoneId': None, 'rendererIds': ['p']}],
                      'zones': [], 'renderers': [], 'observationErrors': []}
        self.node.state = AsyncMock(return_value=self.state)
        self.service = Service(self.node, 'x' * 32, self.tmp.name, '192.0.2.2')
        self.service.api = MagicMock()
        self.service.settings['rooms'] = [{'id': 'r', 'name': 'Room Qobuz', 'port': 8790}]
        self.receiver = MagicMock()
        self.receiver.start = AsyncMock()
        self.receiver.stop = AsyncMock()
        self.receiver.diagnostics.return_value = {}

    async def asyncTearDown(self):
        await self.service.stop_receivers()
        self.tmp.cleanup()

    async def test_fresh_spotify_is_advertised_without_speaker_commands(self):
        with patch('qobuz.service.local_address', return_value=True), patch('qobuz.service.Receiver', return_value=self.receiver):
            await self.service.reconcile_once()
        self.assertEqual(list(self.service.receivers), ['r'])
        self.receiver.start.assert_awaited_once()
        self.node.control.assert_not_called()

    async def test_address_validation_checks_interfaces_not_nonlocal_bind_permission(self):
        adapter = MagicMock(ips=[MagicMock(ip='192.0.2.2'), MagicMock(ip=('::1', 0, 0))])
        with patch('qobuz.service.ifaddr.get_adapters', return_value=[adapter]):
            self.assertTrue(local_address('192.0.2.2'))
            self.assertFalse(local_address('192.0.2.3'))
            self.assertFalse(local_address('0.0.0.0'))
            self.assertFalse(local_address('127.0.0.1'))
            self.assertFalse(local_address('224.0.0.1'))

    async def test_missing_observation_or_grouped_topology_still_excludes_room(self):
        with patch('qobuz.service.local_address', return_value=True), patch('qobuz.service.Receiver') as receiver:
            self.state['rooms'][0]['fresh'] = False
            await self.service.reconcile_once()
            self.state['rooms'][0].update(fresh=True, zoneId='z')
            self.state['zones'] = [{'id': 'z', 'roomIds': ['r', 'other']}]
            await self.service.reconcile_once()
            receiver.assert_not_called()
        self.assertEqual(self.service.receivers, {})
        self.node.control.assert_not_called()

    async def test_wrong_lan_address_explained_without_starting_receiver(self):
        with patch('qobuz.service.local_address', return_value=False), patch('qobuz.service.Receiver') as receiver:
            await self.service.reconcile_once()
            receiver.assert_not_called()
        self.assertEqual(self.service.receiver_errors, {'r': 'lan_address_not_local'})
        self.node.control.assert_not_called()

    async def test_one_receiver_bind_failure_does_not_remove_other_advertisements(self):
        self.state['rooms'].append({**self.state['rooms'][0], 'id': 'other'})
        self.service.settings['rooms'].append({'id': 'other', 'name': 'Other', 'port': 8791})
        broken = MagicMock()
        broken.start = AsyncMock(side_effect=OSError(errno.EADDRINUSE, 'private address details'))
        with patch('qobuz.service.local_address', return_value=True), patch('qobuz.service.Receiver', side_effect=[broken, self.receiver]):
            await self.service.reconcile_once()
        self.assertEqual(list(self.service.receivers), ['other'])
        self.assertEqual(self.service.receiver_errors, {'r': 'receiver_port_in_use'})
        self.node.control.assert_not_called()

    async def test_diagnostics_show_timestamps_and_safe_errors_not_exception_text(self):
        self.state['observationErrors'] = [{'id': 'p', 'failedAt': 101, 'action': 'QueryLastChange', 'code': 'ECONNRESET'}]
        self.receiver.start.side_effect = RuntimeError('secret account and stream URL')
        with patch('qobuz.service.local_address', return_value=True), patch('qobuz.service.Receiver', return_value=self.receiver):
            await self.service.reconcile_once()
            response = await self.service.status(None)
        data = json.loads(response.text)
        self.assertEqual(data['receiverErrors'], {'r': 'receiver_start_failed'})
        self.assertEqual(data['observationErrors'], self.state['observationErrors'])
        self.assertEqual(data['topologyAt'], 100)
        self.assertNotIn('secret', response.text)

    async def test_stale_state_stops_advertisement_without_stop_command(self):
        self.service.receivers['r'] = self.receiver
        self.state['rooms'][0]['fresh'] = False
        with patch('qobuz.service.local_address', return_value=True):
            await self.service.reconcile_once()
        self.receiver.stop.assert_awaited_once()
        self.assertEqual(self.service.receivers, {})
        self.node.control.assert_not_called()


if __name__=='__main__':
    unittest.main()

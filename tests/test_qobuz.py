import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp.test_utils import TestClient, TestServer
from qobuz.backend import RaumfeldBackend, AudioRelay
from qobuz.client import OwnershipLost
from qobuz.receiver import Receiver
from qobuz.service import Service, save_json, load_json
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

    async def test_disconnect_releases_permission_without_stopping_audio(self):
        await self.backend.select('explicit-selection')
        await self.backend.disconnect()
        self.assertIsNone(self.backend.token)
        self.assertEqual(self.client.control.call_args.args, ('room','release'))


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
            await receiver.backend.external()
            before=len(node.control.call_args_list)
            await receiver.select(tokens)
            self.assertEqual(len(node.control.call_args_list),before)
            await receiver.stop()
        self.assertFalse(any(c.args[1] in ('play','stop','volume') for c in node.control.call_args_list))


if __name__=='__main__':
    unittest.main()

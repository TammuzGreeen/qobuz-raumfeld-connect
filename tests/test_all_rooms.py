"""Actual upstream per-room lifecycle, sessions, dispatch and port isolation."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from qobuz_proxy.config import SpeakerConfig
from qobuz_proxy.connect.discovery import DiscoveryService
from qobuz_proxy.connect.ws_manager import WsManager
from qobuz_proxy.playback import QobuzPlayer
import qobuz_proxy.app as app_module
import qobuz_proxy.speaker as speaker_module
from qobuz.upstream_app import load_runtime, install_runtime
from qobuz.upstream_backend import CompanionClient, EndpointUnavailable, operation


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class AllRoomTests(unittest.IsolatedAsyncioTestCase):
    async def test_room_a_takeover_during_room_b_loading_cannot_cancel_b(self):
        from upstream_partial_replay import Replay, fake_soap, state_message
        from qobuz_proxy.backends.dlna.client import DLNAClient
        a, b = Replay(True), Replay(True)
        with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
            await a.start()
            await b.start(paused=True)
            entered, proceed = asyncio.Event(), asyncio.Event()
            original = b.player.metadata.get_streaming_url
            async def delayed(track):
                entered.set()
                await proceed.wait()
                return await original(track)
            b.player.metadata.get_streaming_url = delayed
            task = b.dispatch(state_message('102', 2))
            await asyncio.wait_for(entered.wait(), 5)
            a.row['spotifyVersion'] += 1
            a.renderer.uri = 'spotify:synthetic-room-a-takeover'
            await a.backend.refresh()
            self.assertTrue(a.backend._external_playback)
            self.assertFalse(b.backend._external_playback)
            proceed.set()
            await asyncio.wait_for(task, 5)
        self.assertEqual(b.player.current_track.track_id, '102')
        self.assertEqual(b.renderer.state, 'PLAYING')
        self.assertFalse(b.backend._external_playback)
        self.assertEqual(b.result()['writes'], ['Stop', 'SetAVTransportURI', 'Play'])
        operation.set(None)

    async def test_loader_all_rooms_stable_ids_and_collision_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rooms = [{'id': 'synthetic-'+str(i), 'name': 'Room '+str(i), 'port': 8790+i} for i in range(6)]
            path = root / 'config.json'
            path.write_text(json.dumps({'rooms': rooms, 'quality': 6}))
            returned, config, runtime = load_runtime(root, '192.0.2.10')
            self.assertEqual(returned, rooms)
            self.assertEqual(len(config.speakers), 6)
            self.assertEqual(len({s.uuid for s in config.speakers}), 6)
            self.assertEqual(len({p for s in config.speakers for p in (s.http_port, s.proxy_port)}), 12)
            rooms[1]['port'] = rooms[0]['port'] + 100
            path.write_text(json.dumps({'rooms': rooms, 'quality': 6}))
            with self.assertRaisesRegex(ValueError, 'collision'):
                load_runtime(root, '192.0.2.10')

    async def test_concurrent_actual_speakers_sessions_callbacks_and_endpoint_binding_are_isolated(self):
        original = (speaker_module.BackendFactory, speaker_module.DiscoveryService, speaker_module.WsManager,
                    speaker_module.VolumeCommandHandler, app_module.Speaker)
        rooms = [{'id': 'synthetic-'+str(i), 'name': 'Synthetic '+str(i), 'port': free_port()} for i in range(2)]
        install_runtime(rooms, 's'*32, diagnostics=True)
        speakers = [app_module.Speaker(config=SpeakerConfig(name=r['name'], uuid='synthetic-device-'+str(i),
            http_port=r['port'], proxy_port=free_port(), bind_address='127.0.0.1', max_quality=6,
            dlna_description_url='raumfeld-room:'+r['id']), api_client=MagicMock(), app_id='synthetic-app')
            for i, r in enumerate(rooms)]
        try:
            with patch.object(DiscoveryService, '_register_mdns', new=AsyncMock()), \
                 patch.object(CompanionClient, 'lookup', new=AsyncMock(side_effect=EndpointUnavailable())):
                self.assertEqual(await asyncio.gather(*(s.start() for s in speakers)), [True, True])
            for i, s in enumerate(speakers):
                tokens = s._discovery._parse_connect_request({'session_id': '00000000-0000-0000-0000-'+str(i+1).zfill(12),
                    'jwt_qconnect': {'jwt': 'synthetic-token', 'exp': 4102444800, 'endpoint': 'wss://example.test/connect'}})
                with patch.object(WsManager, 'start', new=AsyncMock()), patch.object(WsManager, 'send_volume_changed', new=AsyncMock()):
                    await s._setup_websocket(tokens)
                s._playback_handler._note_active(True)
                operation.set(None)
                self.assertIs(type(s._player), QobuzPlayer)
                self.assertIs(s._ws_manager._room_backend, s._backend)
            a, b = speakers
            self.assertIsNot(a._player, b._player)
            self.assertIsNot(a._queue, b._queue)
            self.assertIsNot(a._backend.companion, b._backend.companion)
            self.assertNotEqual(a._backend.diagnostic_alias, b._backend.diagnostic_alias)
            self.assertNotEqual(a._discovery.get_received_tokens().session_id, b._discovery.get_received_tokens().session_id)
            calls = []
            for s in speakers:
                def handler(mt, message, backend=s._backend):
                    backend.check_operation()
                    calls.append(operation.get()[3])
                s._ws_manager.register_handler(999, handler)
            for s in speakers:
                s._ws_manager._handlers[999](999, None)
            self.assertEqual(calls, [id(a._backend), id(b._backend)])
            token = operation.set(a._backend.capture_operation())
            try:
                with self.assertRaises(EndpointUnavailable):
                    b._backend.check_operation()
            finally:
                operation.reset(token)
            before = b._backend.capture_operation()
            await a._backend.release_source('fresh_spotify_transition')
            self.assertEqual(b._backend.capture_operation(), before)
            self.assertTrue(b._playback_handler._active)
            self.assertIsNotNone(b._discovery.get_received_tokens())
            await b._backend.rebind(None)
            self.assertIs(a._ws_manager._room_backend, a._backend)
            operation.set(None)
        finally:
            for s in speakers:
                await s.stop()
            speaker_module.BackendFactory, speaker_module.DiscoveryService, speaker_module.WsManager, \
                speaker_module.VolumeCommandHandler, app_module.Speaker = original

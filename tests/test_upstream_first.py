"""Pinned upstream components with synthetic DLNA/companion, no real accounts."""
import asyncio
import html
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import xml.etree.ElementTree as ET

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from qobuz_proxy.backends.types import BackendTrackMetadata, PlaybackState
from qobuz_proxy.playback import QobuzPlayer, QobuzQueue
from qobuz_proxy.playback.play_reporter import PlayReporter
from qobuz_proxy.playback.queue import RepeatMode
from qobuz_proxy.connect.discovery import DiscoveryService
from qobuz_proxy.connect.ws_manager import WsManager
from qobuz_proxy.config import Config, SpeakerConfig
from qobuz_proxy.speaker import Speaker
import qobuz_proxy.app as app_module
import qobuz_proxy.speaker as speaker_module

from qobuz.upstream_backend import CompanionClient, DeferredRoomBackend, EndpointUnavailable, operation
from qobuz.endpoint_catalog import Endpoint
from qobuz.upstream_app import install_runtime, load_runtime, OneRoomApplication


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class UpstreamRoomTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        operation.set(None)
        self.commands = []
        self.uri = 'spotify:synthetic-baseline'
        self.transport = 'PLAYING'
        self.volume = 4
        self.failure = None
        self.hook = None
        self.fragment = False
        self.udn = 'synthetic-zone'
        self.node = MagicMock()
        self.row = {'roomId': 'synthetic-room', 'assigned': False, 'endpoint': None,
                    'rendererIds': ['synthetic-physical'], 'spotifyVersion': 0}
        self.node.lookup = AsyncMock(side_effect=lambda _: dict(self.row))
        self.node.close = AsyncMock()
        async def create(_):
            self.row.update(assigned=True, endpoint=self.endpoint)
        self.node.create_for_play = AsyncMock(side_effect=create)

        async def description(request):
            services = ''.join(f'<service><serviceType>urn:schemas-upnp-org:service:{s}:1</serviceType>'
                f'<serviceId>urn:upnp-org:serviceId:{s}</serviceId><controlURL>/soap</controlURL>'
                '<eventSubURL>/events</eventSubURL><SCPDURL>/scpd</SCPDURL></service>'
                for s in ('AVTransport', 'RenderingControl', 'ConnectionManager'))
            text = '<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
            text += '<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>'
            text += '<friendlyName>Synthetic</friendlyName><manufacturer>Raumfeld</manufacturer>'
            text += f'<UDN>{self.udn}</UDN><serviceList>{services}</serviceList></device></root>'
            if not self.fragment:
                return web.Response(text=text, content_type='text/xml')
            body = text.encode()
            response = web.StreamResponse(headers={'Content-Length': str(len(body))})
            await response.prepare(request)
            await response.write(body[:100])
            await asyncio.sleep(.02)
            await response.write(body[100:])
            await response.write_eof()
            return response

        async def soap(request):
            action = request.headers['SOAPAction'].strip('"').split('#')[-1]
            body = await request.text()
            self.commands.append(action)
            if self.hook:
                await self.hook(action)
            if action == self.failure:
                return web.Response(status=500, text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                    '<s:Body><s:Fault><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
                    '<errorCode>501</errorCode><errorDescription>Synthetic</errorDescription>'
                    '</UPnPError></detail></s:Fault></s:Body></s:Envelope>')
            if action == 'SetAVTransportURI':
                self.uri = next(e.text for e in ET.fromstring(body).iter() if e.tag.endswith('CurrentURI'))
            if action == 'Play':
                self.transport = 'PLAYING'
            if action == 'Pause':
                self.transport = 'PAUSED_PLAYBACK'
            if action == 'Stop':
                self.transport = 'STOPPED'
            if action == 'SetVolume':
                self.volume = int(next(e.text for e in ET.fromstring(body).iter() if e.tag.endswith('DesiredVolume')))
            fields = {'GetMediaInfo': f'<CurrentURI>{html.escape(self.uri)}</CurrentURI>',
                'GetTransportInfo': f'<CurrentTransportState>{self.transport}</CurrentTransportState>',
                'GetPositionInfo': '<RelTime>00:00:07</RelTime><TrackDuration>00:03:00</TrackDuration>',
                'GetVolume': f'<CurrentVolume>{self.volume}</CurrentVolume>'}.get(action, '')
            return web.Response(text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                f'<s:Body><u:{action}Response xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
                f'{fields}</u:{action}Response></s:Body></s:Envelope>', content_type='text/xml')

        async def scpd(request):
            args = ''.join(f'<argument><name>{name}</name><direction>in</direction></argument>'
                          for name in ('InstanceID', 'Channel', 'DesiredVolume'))
            return web.Response(text='<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
                f'<actionList><action><name>SetVolume</name><argumentList>{args}</argumentList>'
                '</action></actionList></scpd>', content_type='text/xml')
        app = web.Application()
        app.router.add_get('/device.xml', description)
        app.router.add_get('/changed.xml', description)
        app.router.add_post('/soap', soap)
        app.router.add_get('/scpd', scpd)
        app.router.add_get('/audio.flac', lambda request: web.Response(body=b'synthetic-flac-bytes', content_type='audio/flac'))
        self.server = TestServer(app)
        await self.server.start_server()
        self.endpoint = {'roomId': 'synthetic-room', 'rendererId': self.udn,
                         'descriptionUrl': str(self.server.make_url('/device.xml'))}
        self.backend = DeferredRoomBackend(self.node, 'synthetic-room', 'Synthetic', poll_seconds=1000)
        self.backend.on_external_playback(AsyncMock())
        self.metadata = BackendTrackMetadata('101', title='Synthetic', duration_ms=180000)
        await self.backend.connect()

    async def asyncTearDown(self):
        await self.backend.disconnect()
        await self.server.close()
        operation.set(None)

    def mutations(self):
        return [action for action in self.commands if not action.startswith('Get')]

    async def start(self):
        self.backend.prepare_for_selection()
        await self.backend.play('http://audio.example.test/synthetic.flac', self.metadata)

    async def test_deferred_startup_and_background_refresh_never_create_or_control(self):
        for _ in range(3):
            await self.backend.refresh()
        self.assertIsNone(self.backend._client)
        self.node.create_for_play.assert_not_awaited()
        self.assertEqual(self.commands, [])
        with self.assertRaises(EndpointUnavailable):
            await self.backend.play('http://audio.example.test/synthetic.flac', self.metadata)
        self.node.create_for_play.assert_not_awaited()

    async def test_explicit_play_creates_once_and_upstream_transport_controls_work(self):
        await self.start()
        self.node.create_for_play.assert_awaited_once()
        await self.backend.pause()
        self.assertTrue(await self.backend.resume())
        await self.backend.seek(3000)
        await self.backend.set_volume(5)
        await self.backend.set_volume(6)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play', 'Pause', 'Play', 'Seek', 'SetVolume', 'SetVolume'])

    async def test_assigned_missing_renderer_never_recreates(self):
        self.row['assigned'] = True
        self.backend.prepare_for_selection()
        with self.assertRaises(EndpointUnavailable):
            await self.backend.play('http://audio.example.test/synthetic.flac', self.metadata)
        self.node.create_for_play.assert_not_awaited()

    async def test_rebinding_and_disappearance_preserve_player_and_do_not_write(self):
        await self.start()
        player = MagicMock()
        self.backend.bind_player(player)
        previous = self.backend._client
        before = self.mutations()
        self.row['endpoint'] = dict(self.endpoint, descriptionUrl=str(self.server.make_url('/changed.xml')))
        await self.backend.refresh()
        self.assertTrue(previous.retired)
        self.assertIs(self.backend.player, player)
        self.assertEqual(self.mutations(), before)
        self.row['endpoint'] = None
        await self.backend.refresh()
        self.assertIsNone(self.backend._client)
        self.assertIs(self.backend.player, player)
        self.assertEqual(self.mutations(), before)
        self.row['endpoint'] = self.endpoint
        await self.backend.refresh()
        self.assertTrue(await self.backend.resume())

    async def test_native_spotify_release_blocks_old_callbacks_and_all_writes(self):
        await self.start()
        before = self.mutations()
        self.row['spotifyVersion'] += 1
        await self.backend.refresh()
        self.backend._on_external_playback.assert_awaited_once()
        for callback in (lambda: self.backend.play('http://audio.example.test/next.flac', self.metadata),
                         self.backend.resume, self.backend.pause, lambda: self.backend.set_volume(7)):
            with self.assertRaises(EndpointUnavailable):
                await callback()
        self.assertEqual(self.mutations(), before)
        self.backend.prepare_for_selection()
        await self.backend.play('http://audio.example.test/new.flac', self.metadata)
        self.assertEqual(self.mutations()[-2:], ['SetAVTransportURI', 'Play'])

    async def test_source_switch_between_uri_and_play_is_terminal(self):
        async def hook(action):
            if action == 'SetAVTransportURI':
                self.row['spotifyVersion'] += 1
        self.hook = hook
        with self.assertRaises(EndpointUnavailable):
            await self.start()
        self.assertEqual(self.mutations(), ['SetAVTransportURI'])
        self.assertTrue(self.backend._external_playback)

    async def test_failed_mutation_is_not_retried_and_shutdown_does_not_stop(self):
        self.failure = 'Play'
        with self.assertRaises(EndpointUnavailable):
            await self.start()
        await self.backend.disconnect()
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])

    async def test_fragmented_description_is_read_completely(self):
        self.fragment = True
        await self.start()
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])

    async def test_old_client_read_cannot_revoke_newer_selection(self):
        await self.start()
        original = self.backend._client
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def old_read():
            entered.set()
            await proceed.wait()
            return 'spotify:late-old-read'
        original.get_media_info = old_read
        task = asyncio.create_task(self.backend._owns_transport())
        await entered.wait()
        self.backend.prepare_for_selection()
        self.row['endpoint'] = dict(self.endpoint, descriptionUrl=str(self.server.make_url('/changed.xml')))
        await self.backend.refresh()
        proceed.set()
        self.assertFalse(await task)
        self.assertFalse(self.backend._external_playback)

    async def test_stock_player_manual_next_natural_repeat_and_reporting(self):
        metadata = MagicMock()
        metadata.get_streaming_url = AsyncMock(return_value='http://audio.example.test/synthetic.flac')
        metadata.get_track_format.return_value = (6, 44100, 16)
        metadata.get_track_actual_quality.return_value = 6
        metadata.get_track_blob.return_value = 'synthetic-blob'
        api = MagicMock()
        api.report_streaming_start = AsyncMock(return_value=True)
        api.report_streaming_end = AsyncMock(return_value=True)
        player = QobuzPlayer(queue=QobuzQueue(), metadata_service=metadata,
            backend=self.backend, play_reporter=PlayReporter(api))
        self.backend.bind_player(player)
        track = MagicMock()
        track.track_id, track.streaming_url = '101', 'http://audio.example.test/synthetic.flac'
        track.url_is_stale.return_value = False
        track.metadata, track.duration_ms = {'title': 'Synthetic', 'duration_ms': 180000}, 180000
        track.context_uuid = bytes([1]) * 16
        player._current_track = track
        self.backend.prepare_for_selection()
        self.assertTrue(await player._start_playback())
        await player.queue.set_repeat_mode(RepeatMode.ONE)
        self.transport = 'STOPPED'
        await player._handle_track_ended(track)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'] * 2)
        api.report_streaming_end.assert_awaited_once()
        self.assertEqual(api.report_streaming_start.await_count, 2)
        next_track = MagicMock()
        next_track.track_id, next_track.streaming_url = '102', 'http://audio.example.test/next.flac'
        next_track.url_is_stale.return_value = False
        next_track.metadata, next_track.duration_ms = track.metadata, 180000
        player.queue.advance_to_next = AsyncMock(return_value=next_track)
        self.assertTrue(await player.next_track())
        self.assertEqual(self.mutations()[-3:], ['Stop', 'SetAVTransportURI', 'Play'])

    async def test_old_pause_cannot_write_after_new_selection_even_on_same_uri(self):
        await self.start()
        original = self.backend._client.get_media_info
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def delayed():
            entered.set()
            await proceed.wait()
            return self.uri
        self.backend._client.get_media_info = delayed
        task = asyncio.create_task(self.backend.pause())
        await entered.wait()
        await self.backend.release_source()
        self.backend.prepare_for_selection()
        proceed.set()
        with self.assertRaises(EndpointUnavailable):
            await task
        self.backend._client.get_media_info = original
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])
        self.assertFalse(self.backend._external_playback)

    async def test_old_metadata_callback_cannot_load_after_new_selection(self):
        metadata = MagicMock()
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def stream(_):
            entered.set()
            await proceed.wait()
            return 'http://audio.example.test/stale.flac'
        metadata.get_streaming_url = AsyncMock(side_effect=stream)
        metadata.get_track_format.return_value = (6, 44100, 16)
        player = QobuzPlayer(queue=QobuzQueue(), metadata_service=metadata, backend=self.backend)
        self.backend.bind_player(player)
        track = MagicMock()
        track.track_id, track.streaming_url = '101', None
        track.url_is_stale.return_value = True
        track.metadata, track.duration_ms = {'title': 'Synthetic'}, 180000
        player._current_track = track
        self.backend.prepare_for_selection()
        task = asyncio.create_task(player._start_playback())
        await entered.wait()
        await self.backend.release_source()
        self.backend.prepare_for_selection()
        proceed.set()
        self.assertFalse(await task)
        self.assertEqual(self.mutations(), [])
        self.node.create_for_play.assert_not_awaited()

    async def test_late_catalog_response_does_not_replace_newer_endpoint(self):
        await self.start()
        entered, proceed = asyncio.Event(), asyncio.Event()
        old = dict(self.row)
        calls = 0
        async def lookup(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await proceed.wait()
                return old
            return dict(self.row)
        self.node.lookup = AsyncMock(side_effect=lookup)
        task = asyncio.create_task(self.backend.refresh())
        await entered.wait()
        self.row['endpoint'] = dict(self.endpoint, descriptionUrl=str(self.server.make_url('/changed.xml')))
        await self.backend.refresh()
        proceed.set()
        await task
        self.assertEqual(self.backend.endpoint.description_url, self.row['endpoint']['descriptionUrl'])

    async def test_missing_transport_read_is_not_natural_completion(self):
        await self.start()
        self.failure = 'GetTransportInfo'
        self.assertEqual(await self.backend.get_state(), PlaybackState.PLAYING)

    async def test_real_node_companion_to_upstream_speaker_soap_and_audio_proxy_bytes(self):
        original = (speaker_module.BackendFactory, speaker_module.DiscoveryService, speaker_module.WsManager, speaker_module.VolumeCommandHandler, app_module.Speaker)
        child = subprocess.Popen(['node', 'tests/fake-companion-node.js', self.endpoint['descriptionUrl']],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        speaker = None
        try:
            ready = json.loads(await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 5))
            base = f'http://127.0.0.1:{ready["port"]}'
            room = {'id': 'synthetic-room', 'name': 'Example Qobuz', 'port': free_port()}
            install_runtime(room, 's' * 32, companion_base=base)
            api = MagicMock()
            api.report_streaming_start = AsyncMock(return_value=True)
            api.report_streaming_end = AsyncMock(return_value=True)
            speaker = app_module.Speaker(config=SpeakerConfig(name=room['name'], uuid='synthetic-room-uuid',
                http_port=room['port'], proxy_port=free_port(), bind_address='127.0.0.1', max_quality=6,
                dlna_description_url='raumfeld-room:synthetic-room'), api_client=api, app_id='synthetic-app')
            with patch.object(DiscoveryService, '_register_mdns', new=AsyncMock()):
                self.assertTrue(await speaker.start())
            self.assertIs(type(speaker._player), QobuzPlayer)
            self.assertIsNone(speaker._backend._client)
            tokens = speaker._discovery._parse_connect_request({
                'session_id': '00000000-0000-0000-0000-000000000001',
                'jwt_qconnect': {'jwt': 'synthetic-token', 'exp': 4102444800, 'endpoint': 'wss://example.test/connect'}})
            with patch.object(WsManager, 'start', new=AsyncMock()), \
                 patch.object(WsManager, 'send_volume_changed', new=AsyncMock()):
                await speaker._setup_websocket(tokens)
            manager, player, queue = speaker._ws_manager, speaker._player, speaker._queue
            session_uuid = manager._session_uuid
            metadata = speaker._metadata_service
            metadata.get_track_format = MagicMock(return_value=(6, 44100, 16))
            metadata.get_track_actual_quality = MagicMock(return_value=6)
            metadata.get_track_blob = MagicMock(return_value='synthetic-blob')
            track = MagicMock()
            track.track_id, track.streaming_url = '101', str(self.server.make_url('/audio.flac'))
            track.url_is_stale.return_value = False
            track.metadata, track.duration_ms = {'title': 'Synthetic', 'duration_ms': 180000}, 180000
            track.context_uuid = bytes([1]) * 16
            speaker._player._current_track = track
            self.assertTrue(await speaker._player._start_playback())
            self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])
            async with aiohttp.ClientSession() as session:
                async with session.get(self.uri) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.read(), b'synthetic-flac-bytes')
            api.report_streaming_start.assert_awaited_once()
            before = self.mutations()
            child.stdin.write('changed\n'); child.stdin.flush()
            await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 5)
            await speaker._backend.refresh()
            self.assertTrue(speaker._backend.endpoint.description_url.endswith('/changed.xml'))
            self.assertIs(speaker._player, player)
            self.assertIs(speaker._queue, queue)
            self.assertIs(speaker._ws_manager, manager)
            self.assertEqual(manager._session_uuid, session_uuid)
            self.assertEqual(speaker._discovery.get_received_tokens().session_id, tokens.session_id)
            self.assertEqual(self.mutations(), before)
            child.stdin.write('native\n'); child.stdin.flush()
            event = json.loads(await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 5))
            self.assertEqual(event['creations'], 1)
            await speaker._backend.refresh()
            self.assertTrue(speaker._backend._external_playback)
            before = self.mutations()
            await speaker.stop()
            self.assertEqual(self.mutations(), before)
        finally:
            if speaker:
                await speaker.stop()
            child.stdin.close()
            try:
                await asyncio.wait_for(asyncio.to_thread(child.wait), 5)
            except TimeoutError:
                child.kill()
                await asyncio.to_thread(child.wait)
            child.stdout.close()
            speaker_module.BackendFactory, speaker_module.DiscoveryService, speaker_module.WsManager, speaker_module.VolumeCommandHandler, app_module.Speaker = original

    async def test_scheduled_natural_callback_cannot_reclaim_after_source_switch(self):
        await self.start()
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def stale_callback():
            entered.set()
            await proceed.wait()
            await self.backend.play('http://audio.example.test/stale-next.flac', self.metadata)
        tasks = []
        self.backend.on_track_ended(lambda: tasks.append(asyncio.create_task(stale_callback())))
        self.backend._notify_track_ended()
        await entered.wait()
        await self.backend.release_source()
        self.backend.prepare_for_selection()
        proceed.set()
        with self.assertRaises(EndpointUnavailable):
            await tasks[0]
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])

    async def test_changed_room_membership_revokes_without_zone_repair(self):
        await self.start()
        self.row['rendererIds'] = ['synthetic-replacement-physical']
        before = self.mutations()
        await self.backend.refresh()
        self.assertTrue(self.backend._external_playback)
        self.assertEqual(self.mutations(), before)
        self.assertEqual(self.node.create_for_play.await_count, 1)

    async def test_knob_readback_reports_changed_value_without_setter(self):
        await self.start()
        player = QobuzPlayer(queue=QobuzQueue(), metadata_service=MagicMock(), backend=self.backend)
        player._volume = self.volume
        callback = AsyncMock()
        player.set_volume_report_callback(callback)
        self.backend.bind_player(player)
        self.volume = 8
        before = self.mutations()
        await self.backend.reconcile_volume()
        await self.backend.reconcile_volume()
        callback.assert_awaited_once_with(8)
        self.assertEqual(self.mutations(), before)

    async def test_volume_missing_readback_releases_without_repeating_setter(self):
        await self.start()
        self.failure = 'GetVolume'
        with self.assertRaises(EndpointUnavailable):
            await self.backend.set_volume(8)
        self.assertTrue(self.backend._external_playback)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play', 'SetVolume'])
        with self.assertRaises(EndpointUnavailable):
            await self.backend.set_volume(8)
        self.assertEqual(self.mutations().count('SetVolume'), 1)

    async def test_replaced_virtual_identity_cannot_inherit_old_transport_authority(self):
        await self.start()
        self.row['endpoint'] = None
        await self.backend.refresh()
        self.udn = 'synthetic-replacement-zone'
        self.row['endpoint'] = dict(self.endpoint, rendererId=self.udn)
        before = self.mutations()
        await self.backend.refresh()
        self.assertTrue(self.backend._external_playback)
        self.assertEqual(self.mutations(), before)


class UpstreamWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_loader_preserves_identity_and_original_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = {'rooms': [{'id': 'synthetic-room', 'name': 'Example Qobuz', 'port': 8790}], 'quality': 6}
            (root / 'config.json').write_text(json.dumps(settings))
            (root / 'credentials.json').write_text(json.dumps({'user_id': 'synthetic-user', 'user_auth_token': 'synthetic-token'}))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            room, config, runtime = load_runtime(root, '192.0.2.10')
            self.assertEqual(config.speakers[0].uuid, str(uuid.uuid5(uuid.NAMESPACE_URL, 'raumfeld:synthetic-room')))
            self.assertEqual(config.speakers[0].name, settings['rooms'][0]['name'])
            self.assertEqual(config.speakers[0].http_port, 8790)
            self.assertEqual(config.qobuz.auth_token, 'synthetic-token')
            self.assertEqual(config.server.bind_address, '127.0.0.1')
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
            self.assertEqual(runtime, root / 'upstream-first')

    async def test_actual_upstream_speaker_constructs_stock_components_and_accepts_repeat_connect(self):
        original = (speaker_module.BackendFactory, speaker_module.DiscoveryService, speaker_module.WsManager, speaker_module.VolumeCommandHandler, app_module.Speaker)
        room = {'id': 'synthetic-room', 'name': 'Example Qobuz', 'port': free_port()}
        install_runtime(room, 's' * 32)
        speaker = app_module.Speaker(config=SpeakerConfig(name=room['name'], uuid='synthetic-room-uuid',
            http_port=room['port'], proxy_port=free_port(), bind_address='127.0.0.1', max_quality=6,
            dlna_description_url='raumfeld-room:synthetic-room'), api_client=MagicMock(), app_id='synthetic-app')
        try:
            with patch.object(DiscoveryService, '_register_mdns', new=AsyncMock()), \
                 patch('qobuz.upstream_backend.CompanionClient.lookup', new=AsyncMock(side_effect=EndpointUnavailable())):
                self.assertTrue(await speaker.start())
            self.assertIsInstance(speaker, Speaker)
            self.assertIs(type(speaker._player), QobuzPlayer)
            self.assertIs(type(speaker._queue), QobuzQueue)
            self.assertIs(type(speaker._player._play_reporter), PlayReporter)
            self.assertIsInstance(speaker._backend, DeferredRoomBackend)
            self.assertEqual(speaker._discovery._handle_connect.__func__, DiscoveryService._handle_connect)
            speaker._discovery.on_connect = MagicMock()
            payload = {'session_id': '00000000-0000-0000-0000-000000000001',
                'jwt_qconnect': {'jwt': 'synthetic-token', 'exp': 4102444800, 'endpoint': 'wss://example.test/connect'}}
            async with aiohttp.ClientSession() as session:
                for _ in range(2):
                    async with session.post(f'http://127.0.0.1:{room["port"]}/streamcore/connect-to-qconnect', json=payload) as response:
                        self.assertEqual(response.status, 200)
            self.assertEqual(speaker._discovery.on_connect.call_count, 2)
            tokens = speaker._discovery.get_received_tokens()
            self.assertEqual(tokens.session_id, payload['session_id'])
            with patch.object(WsManager, 'start', new=AsyncMock()), \
                 patch.object(WsManager, 'send_volume_changed', new=AsyncMock()):
                await speaker._setup_websocket(tokens)
            self.assertIsInstance(speaker._ws_manager, WsManager)
            self.assertEqual(speaker._setup_websocket.__func__, Speaker._setup_websocket)
            self.assertEqual(speaker._on_app_connected.__func__, Speaker._on_app_connected)
            self.assertTrue(speaker._backend.selection_pending)
            speaker._player.set_volume = AsyncMock()
            await speaker._volume_handler._handle_volume_changed(MagicMock())
            speaker._player.set_volume.assert_not_awaited()
            player, queue, manager = speaker._player, speaker._queue, speaker._ws_manager
            await speaker._backend.release_source()
            self.assertIsNone(speaker._discovery.get_received_tokens())
            self.assertTrue(manager._external_playback)
            self.assertIs(speaker._player, player)
            self.assertIs(speaker._queue, queue)
        finally:
            await speaker.stop()
            speaker_module.BackendFactory, speaker_module.DiscoveryService, speaker_module.WsManager, speaker_module.VolumeCommandHandler, app_module.Speaker = original

    async def test_upstream_application_ui_room_changes_are_disabled(self):
        app = OneRoomApplication(Config())
        with self.assertRaises(ValueError):
            await app._on_edit_speaker('synthetic-room', {})

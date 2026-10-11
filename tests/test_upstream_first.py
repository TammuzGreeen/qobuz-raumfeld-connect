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
from qobuz_proxy.connect.protocol import payload_pb2
from qobuz_proxy.playback.command_handler import PlaybackCommandHandler
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
        self.failure_code = 501
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
                    f'<errorCode>{self.failure_code}</errorCode><errorDescription>Synthetic</errorDescription>'
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

    async def test_definite_unsupported_seek_preserves_upstream_behavior_without_retry(self):
        await self.start()
        self.failure, self.failure_code = 'Seek', 710
        await self.backend.seek(3000)
        self.assertFalse(self.backend._external_playback)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play', 'Seek'])
        self.assertEqual(self.backend._position_ms, 0)
        self.backend._on_external_playback.assert_not_awaited()
        await self.backend.pause()
        self.assertTrue(await self.backend.resume())

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

    def stock_player(self):
        metadata = MagicMock()
        metadata.get_track_format.return_value = (6, 44100, 16)
        metadata.get_track_actual_quality.return_value = 6
        metadata.get_track_blob.return_value = 'synthetic-blob'
        api = MagicMock()
        api.report_streaming_start = AsyncMock(return_value=True)
        api.report_streaming_end = AsyncMock(return_value=True)
        player = QobuzPlayer(queue=QobuzQueue(), metadata_service=metadata,
            backend=self.backend, play_reporter=PlayReporter(api))
        self.backend.bind_player(player)
        player.set_playback_permission_check(self.backend.can_apply_remote_state)
        track = MagicMock()
        track.track_id, track.streaming_url = '101', 'http://audio.example.test/synthetic.flac'
        track.queue_item_id = 1
        track.url_is_stale.return_value = False
        track.metadata, track.duration_ms = {'title': 'Synthetic', 'duration_ms': 180000}, 180000
        track.context_uuid = bytes([1]) * 16
        player._current_track = track
        return player, api

    async def test_split_next_item_then_state_only_play_during_pre_stop_read_starts_new_item(self):
        player, api = self.stock_player()
        meta = MagicMock()
        meta.to_dict.return_value = {'title': 'Synthetic next', 'duration_ms': 180000}
        player.metadata.get_metadata = AsyncMock(return_value=meta)
        player.metadata.get_streaming_url = AsyncMock(return_value='http://audio.example.test/next.flac')
        handler = PlaybackCommandHandler(player)
        self.backend.bind_authority(lambda: (handler._generation, handler._active))
        handler._note_active(True)
        self.backend.prepare_for_selection()
        operation.set(None)
        self.assertTrue(await player.play())
        pending = []
        reads = 0
        async def hook(action):
            nonlocal reads
            if action == 'GetMediaInfo':
                reads += 1
            if action == 'GetMediaInfo' and reads == 2 and not pending:
                msg = payload_pb2.QConnectMessage()
                msg.srvrRndrSetState.playingState = 2
                pending.append(handler.dispatch_message(41, msg))
                await asyncio.sleep(0)
        self.hook = hook
        msg = payload_pb2.QConnectMessage()
        msg.srvrRndrSetState.currentQueueItem.trackId = 102
        msg.srvrRndrSetState.currentQueueItem.queueItemId = 2
        try:
            await handler.dispatch_message(41, msg)
            await asyncio.wait_for(asyncio.gather(*pending), 5)
            self.assertEqual(player.current_track.track_id, '102')
            self.assertEqual(player.state, PlaybackState.PLAYING)
            self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play', 'Stop', 'SetAVTransportURI', 'Play'])
            self.assertEqual(api.report_streaming_start.await_count, 2)
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def split_play_interruption(self, interrupt):
        player, api = self.stock_player()
        meta = MagicMock()
        meta.to_dict.return_value = {'title': 'Synthetic next', 'duration_ms': 180000}
        player.metadata.get_metadata = AsyncMock(return_value=meta)
        player.metadata.get_streaming_url = AsyncMock(return_value='http://audio.example.test/next.flac')
        handler = PlaybackCommandHandler(player)
        self.backend.bind_authority(lambda: (handler._generation, handler._active))
        handler._note_active(True)
        self.backend.prepare_for_selection()
        operation.set(None)
        self.assertTrue(await player.play())
        pending = []
        reads = 0
        if interrupt == 'failed_stop':
            self.failure = 'Stop'
        async def hook(action):
            nonlocal reads
            if action == 'GetMediaInfo':
                reads += 1
            if action != 'GetMediaInfo' or reads != 2 or pending:
                return
            msg = payload_pb2.QConnectMessage()
            msg.srvrRndrSetState.playingState = 2
            pending.append(handler.dispatch_message(41, msg))
            await asyncio.sleep(0)
            if interrupt == 'native':
                self.row['spotifyVersion'] += 1
            elif interrupt == 'selection':
                self.backend.prepare_for_selection()
            elif interrupt == 'deactivation':
                handler._note_active(False)
            elif interrupt == 'disconnect':
                handler.note_disconnected()
                handler._note_active(True)
            elif interrupt == 'pause':
                msg = payload_pb2.QConnectMessage()
                msg.srvrRndrSetState.playingState = 3
                pending.append(handler.dispatch_message(41, msg))
                await asyncio.sleep(0)
            elif interrupt == 'different_item':
                msg = payload_pb2.QConnectMessage()
                msg.srvrRndrSetState.currentQueueItem.trackId = 103
                msg.srvrRndrSetState.currentQueueItem.queueItemId = 3
                pending.append(handler.dispatch_message(41, msg))
                await asyncio.sleep(0)
        self.hook = hook
        msg = payload_pb2.QConnectMessage()
        msg.srvrRndrSetState.currentQueueItem.trackId = 102
        msg.srvrRndrSetState.currentQueueItem.queueItemId = 2
        try:
            await handler.dispatch_message(41, msg)
            await asyncio.wait_for(asyncio.gather(*pending), 5)
            writes = self.mutations()[2:]
            self.assertNotIn('SetAVTransportURI', writes)
            self.assertNotIn('Play', writes)
            self.assertEqual(api.report_streaming_start.await_count, 1)
            self.assertIsNone(self.backend.pending_track_load)
            if interrupt == 'failed_stop':
                self.assertEqual(writes, ['Stop'])
                self.assertTrue(self.backend._external_playback)
            if interrupt == 'different_item':
                self.assertEqual(player.current_track.track_id, '103')
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_split_play_does_not_reclaim_native_source(self):
        await self.split_play_interruption('native')

    async def test_split_play_does_not_inherit_new_selection(self):
        await self.split_play_interruption('selection')

    async def test_split_play_does_not_survive_deactivation(self):
        await self.split_play_interruption('deactivation')

    async def test_split_play_does_not_survive_disconnect_and_reactivation(self):
        await self.split_play_interruption('disconnect')

    async def test_genuine_pause_supersedes_split_play(self):
        await self.split_play_interruption('pause')

    async def test_newer_item_selection_supersedes_old_split_play(self):
        await self.split_play_interruption('different_item')

    async def test_split_play_does_not_retry_failed_stop(self):
        await self.split_play_interruption('failed_stop')

    async def test_passive_snapshot_before_uri_cannot_cancel_load_or_strand_playback(self):
        player, api = self.stock_player()
        pending = []
        context = bytes([2]) * 16
        async def hook(action):
            if action == 'GetMediaInfo' and not pending:
                pending.append(asyncio.create_task(player.apply_remote_state(
                    track_id='101', queue_item_id=1, position_ms=None, playing_state=None,
                    context_uuid=context)))
                await asyncio.sleep(0)
        self.hook = hook
        self.backend.prepare_for_selection()
        try:
            async with player._playback_lock:
                self.assertTrue(await player._start_playback())
            await asyncio.wait_for(pending[0], 5)
            self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])
            self.assertEqual(player.state, PlaybackState.PLAYING)
            self.assertEqual(player._current_track.context_uuid, context)
            api.report_streaming_start.assert_awaited_once()
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_real_pause_before_uri_still_supersedes_load_and_passive_feedback(self):
        player, api = self.stock_player()
        pending = []
        async def hook(action):
            if action == 'GetMediaInfo' and not pending:
                pending.append(asyncio.create_task(player.apply_remote_state(
                    track_id='101', queue_item_id=1, position_ms=None, playing_state=None)))
                await asyncio.sleep(0)
                pending.append(asyncio.create_task(player.apply_remote_state(
                    track_id='101', queue_item_id=1, position_ms=None, playing_state=3)))
                await asyncio.sleep(0)
        self.hook = hook
        self.backend.prepare_for_selection()
        try:
            async with player._playback_lock:
                self.assertFalse(await player._start_playback())
            await asyncio.wait_for(asyncio.gather(*pending), 5)
            self.assertEqual(self.mutations(), [])
            self.assertNotEqual(player.state, PlaybackState.PLAYING)
            api.report_streaming_start.assert_not_awaited()
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_passive_snapshot_during_failed_uri_does_not_retry_or_reclaim(self):
        player, api = self.stock_player()
        pending = []
        self.failure = 'SetAVTransportURI'
        async def hook(action):
            if action == 'GetMediaInfo' and not pending:
                pending.append(asyncio.create_task(player.apply_remote_state(
                    track_id='101', queue_item_id=1, position_ms=None, playing_state=None)))
                await asyncio.sleep(0)
        self.hook = hook
        self.backend.prepare_for_selection()
        try:
            async with player._playback_lock:
                self.assertFalse(await player._start_playback())
            await asyncio.wait_for(pending[0], 5)
            self.assertEqual(self.mutations(), ['SetAVTransportURI'])
            self.assertTrue(self.backend._external_playback)
            api.report_streaming_start.assert_not_awaited()
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_natural_advance_then_manual_selection_and_play_with_stock_feedback_dispatch(self):
        player, api = self.stock_player()
        meta = MagicMock()
        meta.to_dict.return_value = {'title': 'Synthetic', 'duration_ms': 180000}
        player.metadata.get_metadata = AsyncMock(return_value=meta)
        player.metadata.get_streaming_url = AsyncMock(return_value='http://audio.example.test/next.flac')
        handler = PlaybackCommandHandler(player)
        self.backend.bind_authority(lambda: (handler._generation, handler._active))
        handler._note_active(True)
        player.set_next_track_callbacks(handler.get_next_track_info, handler.clear_next_track_info)

        def message(track_id, item_id, playing=None):
            msg = payload_pb2.QConnectMessage()
            msg.srvrRndrSetState.currentQueueItem.trackId = int(track_id)
            msg.srvrRndrSetState.currentQueueItem.queueItemId = item_id
            if playing is not None:
                msg.srvrRndrSetState.playingState = playing
            return msg

        feedback = []
        seen = set()
        async def hook(action):
            load = self.backend.pending_load
            if action == 'GetMediaInfo' and load and load[2].track_id not in seen:
                track = load[2]
                seen.add(track.track_id)
                feedback.append(handler.dispatch_message(41, message(track.track_id, track.queue_item_id)))
                await asyncio.sleep(0)
        self.hook = hook
        self.backend.prepare_for_selection()
        try:
            operation.set(None)  # Fresh cloud dispatch captures current selection.
            self.assertTrue(await player.play())
            await asyncio.wait_for(asyncio.gather(*feedback), 5)
            handler._next_track_info = {'queueItemId': 2, 'trackId': '102', 'contextUuid': bytes([1]) * 16}
            self.transport = 'STOPPED'
            await player._handle_track_ended(player._current_track)
            await asyncio.wait_for(asyncio.gather(*feedback), 5)
            self.assertEqual(player._current_track.track_id, '102')
            self.assertEqual(player.state, PlaybackState.PLAYING)
            self.transport = 'STOPPED'
            await player._handle_track_ended(player._current_track)
            self.assertEqual(player.state, PlaybackState.STOPPED)
            await handler.dispatch_message(41, message('103', 3))
            self.assertEqual(player._current_track.track_id, '103')
            self.assertEqual(player.state, PlaybackState.STOPPED)
            await handler.dispatch_message(41, message('103', 3, 2))
            await asyncio.wait_for(asyncio.gather(*feedback), 5)
            self.assertEqual(player.state, PlaybackState.PLAYING)
            self.assertEqual(self.mutations().count('SetAVTransportURI'), 3)
            self.assertEqual(self.mutations().count('Play'), 3)
            self.assertEqual(api.report_streaming_start.await_count, 3)
            self.assertEqual(api.report_streaming_end.await_count, 2)
        finally:
            for task in feedback:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*feedback, return_exceptions=True)

    async def test_native_takeover_with_passive_snapshot_waiting_cannot_reclaim(self):
        player, api = self.stock_player()
        pending = []
        async def hook(action):
            if action == 'GetMediaInfo' and not pending:
                pending.append(asyncio.create_task(player.apply_remote_state(
                    track_id='101', queue_item_id=1, position_ms=None, playing_state=None)))
                await asyncio.sleep(0)
                self.row['spotifyVersion'] += 1
        self.hook = hook
        self.backend.prepare_for_selection()
        try:
            async with player._playback_lock:
                self.assertFalse(await player._start_playback())
            await asyncio.wait_for(pending[0], 5)
            self.assertEqual(self.mutations(), [])
            self.assertTrue(self.backend._external_playback)
            api.report_streaming_start.assert_not_awaited()
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_different_item_metadata_snapshot_is_selection_not_deferred_feedback(self):
        player, api = self.stock_player()
        pending = []
        async def hook(action):
            if action == 'GetMediaInfo' and not pending:
                pending.append(asyncio.create_task(player.apply_remote_state(
                    track_id='101', queue_item_id=2, position_ms=None, playing_state=None)))
                await asyncio.sleep(0)
        self.hook = hook
        self.backend.prepare_for_selection()
        try:
            async with player._playback_lock:
                self.assertFalse(await player._start_playback())
            await asyncio.wait_for(pending[0], 5)
            self.assertEqual(player._current_track.queue_item_id, 2)
            self.assertEqual(self.mutations(), [])
            api.report_streaming_start.assert_not_awaited()
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_selection_change_discards_waiting_feedback_without_reviving_old_load(self):
        player, api = self.stock_player()
        pending = []
        async def hook(action):
            if action == 'GetMediaInfo' and not pending:
                pending.append(asyncio.create_task(player.apply_remote_state(
                    track_id='101', queue_item_id=1, position_ms=None, playing_state=None)))
                await asyncio.sleep(0)
                self.backend.prepare_for_selection()
        self.hook = hook
        self.backend.prepare_for_selection()
        try:
            async with player._playback_lock:
                self.assertFalse(await player._start_playback())
            await asyncio.wait_for(pending[0], 5)
            self.assertEqual(self.mutations(), [])
            api.report_streaming_start.assert_not_awaited()
            self.assertIsNone(self.backend.pending_load)
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_same_session_new_snapshot_during_uri_ack_finishes_atomic_load_and_reports_playing(self):
        player, api = self.stock_player()
        async def hook(action):
            if action == 'SetAVTransportURI':
                # A newer SET_STATE registers its generation while waiting on
                # the upstream playback lock. Authority/source have not changed.
                player._next_generation()
        self.hook = hook
        self.backend.prepare_for_selection()
        self.assertTrue(await player._start_playback())
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])
        self.assertEqual(player.state, PlaybackState.PLAYING)
        api.report_streaming_start.assert_awaited_once()

    async def test_upstream_deactivation_during_uri_ack_still_prevents_play_and_report(self):
        player, api = self.stock_player()
        handler = PlaybackCommandHandler(player)
        self.backend.bind_authority(lambda: (handler._generation, handler._active))
        self.backend.prepare_for_selection()
        handler._note_active(True)
        async def hook(action):
            if action == 'SetAVTransportURI':
                handler._note_active(False)
        self.hook = hook
        self.assertFalse(await player._start_playback())
        self.assertEqual(self.mutations(), ['SetAVTransportURI'])
        api.report_streaming_start.assert_not_awaited()

    async def test_real_queued_remote_state_after_uri_ack_does_not_reload_or_lose_playing(self):
        player, api = self.stock_player()
        pending = []
        async def hook(action):
            if action == 'SetAVTransportURI' and not pending:
                pending.append(asyncio.create_task(player.apply_remote_state(
                    track_id='101', queue_item_id=1, position_ms=None, playing_state=2)))
                await asyncio.sleep(0)  # New intent registers, then waits on lock.
        self.hook = hook
        self.backend.prepare_for_selection()
        try:
            async with player._playback_lock:
                self.assertTrue(await player._start_playback())
            await asyncio.wait_for(pending[0], 5)
            self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])
            self.assertEqual(player.state, PlaybackState.PLAYING)
            api.report_streaming_start.assert_awaited_once()
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_disconnect_and_reactivation_cannot_revive_committed_old_load(self):
        player, api = self.stock_player()
        handler = PlaybackCommandHandler(player)
        self.backend.bind_authority(lambda: (handler._generation, handler._active))
        self.backend.prepare_for_selection()
        handler._note_active(True)
        async def hook(action):
            if action == 'SetAVTransportURI':
                handler.note_disconnected()
                handler._note_active(True)
        self.hook = hook
        self.assertFalse(await player._start_playback())
        self.assertEqual(self.mutations(), ['SetAVTransportURI'])
        api.report_streaming_start.assert_not_awaited()

    async def test_foreign_virtual_uri_during_committed_load_is_terminal_not_a_retry_window(self):
        readings = 0
        async def hook(action):
            nonlocal readings
            if action == 'GetMediaInfo' and 'SetAVTransportURI' in self.commands:
                readings += 1
                self.uri = ('http://competitor.example.test/foreign' if readings == 1
                            else self.backend.loading_uri)
        self.hook = hook
        with self.assertRaises(EndpointUnavailable):
            await self.start()
        self.assertTrue(self.backend._external_playback)
        self.assertEqual(readings, 1)
        self.assertEqual(self.mutations(), ['SetAVTransportURI'])

    async def test_newer_player_intent_before_first_mutation_still_supersedes_old_metadata(self):
        player, api = self.stock_player()
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def stream(_):
            entered.set()
            await proceed.wait()
            return 'http://audio.example.test/stale.flac'
        player.metadata.get_streaming_url = AsyncMock(side_effect=stream)
        player._current_track.streaming_url = None
        player._current_track.url_is_stale.return_value = True
        self.backend.prepare_for_selection()
        task = asyncio.create_task(player._start_playback())
        await entered.wait()
        player._next_generation()
        proceed.set()
        self.assertFalse(await task)
        self.assertEqual(self.mutations(), [])
        api.report_streaming_start.assert_not_awaited()

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
            install_runtime(room, 's' * 32, companion_base=base, diagnostics=True)
            api = MagicMock()
            api.report_streaming_start = AsyncMock(return_value=True)
            api.report_streaming_end = AsyncMock(return_value=True)
            speaker = app_module.Speaker(config=SpeakerConfig(name=room['name'], uuid='synthetic-room-uuid',
                http_port=room['port'], proxy_port=free_port(), bind_address='127.0.0.1', max_quality=6,
                dlna_description_url='raumfeld-room:synthetic-room'), api_client=api, app_id='synthetic-app')
            with patch.object(DiscoveryService, '_register_mdns', new=AsyncMock()):
                self.assertTrue(await speaker.start())
            self.assertIs(type(speaker._player), QobuzPlayer)
            self.assertIsNotNone(speaker._backend.diagnostics)
            self.assertIsNone(speaker._backend._client)
            tokens = speaker._discovery._parse_connect_request({
                'session_id': '00000000-0000-0000-0000-000000000001',
                'jwt_qconnect': {'jwt': 'synthetic-token', 'exp': 4102444800, 'endpoint': 'wss://example.test/connect'}})
            with patch.object(WsManager, 'start', new=AsyncMock()), \
                 patch.object(WsManager, 'send_volume_changed', new=AsyncMock()):
                await speaker._setup_websocket(tokens)
            speaker._playback_handler._note_active(True)
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

"""Real pinned DLNA HTTP/SOAP against synthetic renderers, no hardware/accounts."""
import asyncio
import html
import unittest
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import TestServer
from qobuz_proxy.backends.types import BackendTrackMetadata, PlaybackState
from qobuz.backend import VolumeUncertain, SeekUnsupported, AudioRelay
from qobuz.client import OwnershipLost, RaumfeldClient
from qobuz.diagnostics import PlaybackTimeline, ObservedReportingAPI
from qobuz.dlna_backend import RaumfeldDLNABackend, FencedDLNAClient
from qobuz.receiver import DirectVolumeHandler
from qobuz.service import Service
from qobuz.player import RaumfeldPlayer
from qobuz_proxy.playback import QobuzQueue
from qobuz_proxy.playback.play_reporter import PlayReporter
from qobuz_proxy.playback.queue import RepeatMode


class DirectDLNATests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.commands = []
        self.uri = 'spotify:synthetic-before-selection'
        self.transport = 'PLAYING'
        self.volume = 3
        self.fail_action = None
        self.fail_code = 501
        self.missing_reads = False
        self.udn = 'synthetic-zone'
        self.before_media = None
        self.fragment_descriptions = False
        self.fragment_scpd = False
        async def xml_response(request, text, fragmented):
            if not fragmented:
                return web.Response(text=text, content_type='text/xml')
            body = text.encode()
            response = web.StreamResponse(headers={'Content-Type': 'text/xml', 'Content-Length': str(len(body))})
            await response.prepare(request)
            await response.write(body[:len(body)//2])
            await asyncio.sleep(.02)
            await response.write(body[len(body)//2:])
            await response.write_eof()
            return response
        async def description(request):
            base = str(self.server.make_url('/')).rstrip('/')
            services = ''.join(f'<service><serviceType>urn:schemas-upnp-org:service:{name}:1</serviceType>'
                f'<serviceId>urn:upnp-org:serviceId:{name}</serviceId><controlURL>{base}/soap</controlURL>'
                '<eventSubURL>/events</eventSubURL><SCPDURL>/scpd</SCPDURL></service>'
                for name in ['AVTransport', 'RenderingControl', 'ConnectionManager'])
            return await xml_response(request, f'<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
                '<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>'
                '<friendlyName>Synthetic</friendlyName><manufacturer>Raumfeld</manufacturer>'
                f'<modelName>Synthetic</modelName><UDN>{self.udn}</UDN><serviceList>{services}</serviceList>'
                '</device></root>', self.fragment_descriptions)
        async def soap(request):
            action = request.headers['SOAPAction'].strip('"').split('#')[-1]
            body = await request.text()
            self.commands.append((action, body))
            if action == self.fail_action or (self.missing_reads and action.startswith('Get')):
                return web.Response(status=500, text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                    '<s:Body><s:Fault><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
                    f'<errorCode>{self.fail_code}</errorCode><errorDescription>Synthetic</errorDescription>'
                    '</UPnPError></detail></s:Fault></s:Body></s:Envelope>', content_type='text/xml')
            if action == 'GetMediaInfo' and self.before_media:
                await self.before_media()
            if action == 'SetAVTransportURI':
                import xml.etree.ElementTree as ET
                self.uri = next(e.text for e in ET.fromstring(body).iter() if e.tag.endswith('CurrentURI'))
            if action == 'Play':
                self.transport = 'PLAYING'
            if action == 'Pause':
                self.transport = 'PAUSED_PLAYBACK'
            if action == 'Stop':
                self.transport = 'STOPPED'
            if action == 'SetVolume':
                import xml.etree.ElementTree as ET
                self.volume = int(next(e.text for e in ET.fromstring(body).iter() if e.tag.endswith('DesiredVolume')))
            fields = {'GetMediaInfo': f'<CurrentURI>{html.escape(self.uri)}</CurrentURI>',
                'GetTransportInfo': f'<CurrentTransportState>{self.transport}</CurrentTransportState>',
                'GetPositionInfo': '<RelTime>00:00:07</RelTime><TrackDuration>00:03:00</TrackDuration>',
                'GetVolume': f'<CurrentVolume>{self.volume}</CurrentVolume>'}.get(action, '')
            return web.Response(text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                f'<s:Body><u:{action}Response xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
                f'{fields}</u:{action}Response></s:Body></s:Envelope>', content_type='text/xml')
        app = web.Application()
        app.router.add_get('/device.xml', description)
        app.router.add_get('/changed.xml', description)
        async def scpd(request):
            args = ''.join(f'<argument><name>{name}</name><direction>in</direction>'
                '<relatedStateVariable>Synthetic</relatedStateVariable></argument>'
                for name in ['InstanceID', 'Channel', 'DesiredVolume'])
            return await xml_response(request, '<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
                f'<actionList><action><name>SetVolume</name><argumentList>{args}</argumentList>'
                '</action></actionList></scpd>', self.fragment_scpd)
        app.router.add_get('/scpd', scpd)
        app.router.add_post('/soap', soap)
        self.server = TestServer(app)
        await self.server.start_server()
        self.binding = {'descriptionUrl': str(self.server.make_url('/device.xml')),
                        'rendererId': 'synthetic-zone', 'roomId': 'synthetic-room', 'rendererIds': ['synthetic-physical']}
        self.revoked = False
        self.binding_calls = []
        async def binding(room_id, action, **kwargs):
            self.binding_calls.append(action)
            if action == 'select':
                return {'token': 'synthetic-lease'}
            if action == 'release':
                return {}
            if self.revoked:
                raise OwnershipLost('ownership_lost')
            return {'binding': self.binding, 'physicalReady': True, 'initial': False,
                    'physicalReads': [len(self.binding_calls)]}
        self.node = MagicMock()
        self.node.binding = AsyncMock(side_effect=binding)
        self.relay = MagicMock()
        self.relay.timeline = PlaybackTimeline()
        self.relay.register.return_value = 'http://192.0.2.10:8790/audio/synthetic'
        self.backend = RaumfeldDLNABackend(self.node, 'synthetic-room', 'Synthetic', self.relay)
        self.backend.on_external = AsyncMock()
        await self.backend.select('a' * 64)
        self.metadata = BackendTrackMetadata('101', title='Synthetic', duration_ms=180000)

    async def asyncTearDown(self):
        await self.backend.disconnect()
        await self.server.close()

    def mutations(self):
        return [a for a, _ in self.commands if not a.startswith('Get')]

    async def start(self):
        await self.backend.play('https://example.test/audio', self.metadata)

    async def test_explicit_play_pause_resume_seek_volume_use_real_upstream_soap(self):
        await self.start()
        await self.backend.pause()
        self.assertEqual(await self.backend.get_state(), PlaybackState.PAUSED)
        self.assertTrue(await self.backend.resume())
        await self.backend.seek(7000)
        await self.backend.set_volume(4)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play', 'Pause', 'Play', 'Seek', 'SetVolume'])
        self.assertIn('<Speed>1</Speed>', next(body for action, body in self.commands if action == 'Play'))
        self.assertEqual(self.backend.last_volume_result['observedVolume'], 4)
        self.assertFalse(self.backend.supports_gapless)
        self.assertFalse(await self.backend.set_next_track('synthetic', self.metadata))

    async def test_same_lease_next_track_is_single_shot_and_uses_virtual_evidence(self):
        await self.start()
        self.relay.register.return_value = 'http://192.0.2.10:8790/audio/synthetic-next'
        await self.start()
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play', 'SetAVTransportURI', 'Play'])
        self.assertEqual(self.uri, self.relay.register.return_value)

    async def test_fragmented_description_is_read_to_eof_before_identity_validation(self):
        self.fragment_descriptions = True
        await self.start()
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])
        self.assertTrue(self.backend.started)

    async def test_fragmented_scpd_is_read_to_eof_before_single_volume_setter(self):
        await self.start()
        self.fragment_scpd = True
        await self.backend.set_volume(4)
        self.assertEqual(self.mutations().count('SetVolume'), 1)
        self.assertEqual(self.backend.last_volume_result['observedVolume'], 4)

    async def test_xml_read_keeps_the_size_bound_across_fragments(self):
        async def chunks():
            yield b'x' * 262144
            yield b'x'
        response = MagicMock()
        response.content.iter_chunked.return_value = chunks()
        with self.assertRaises(OwnershipLost):
            await FencedDLNAClient.read_xml(response)
        self.assertEqual(self.mutations(), [])

    async def test_uri_failure_has_no_retry_stop_play_or_success_state(self):
        self.fail_action = 'SetAVTransportURI'
        await self.assert_start_failure()
        self.assertEqual(self.mutations(), ['SetAVTransportURI'])
        self.assertFalse(self.backend.started)
        self.assertIsNone(self.backend.token)

    async def assert_start_failure(self):
        with self.assertRaises(OwnershipLost):
            await self.start()

    async def test_play_failure_does_not_reset_or_retry(self):
        self.fail_action = 'Play'
        await self.assert_start_failure()
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])
        self.assertFalse(self.backend.started)

    async def test_takeover_blocks_queued_next_and_all_old_transport_volume_callbacks(self):
        await self.start()
        self.revoked = True
        before = self.mutations()
        self.assertFalse(await self.backend.can_play())
        with self.assertRaises(OwnershipLost):
            await self.start()
        with self.assertRaises(OwnershipLost):
            await self.backend.pause()
        with self.assertRaises(OwnershipLost):
            await self.backend.set_volume(4)
        self.assertEqual(self.mutations(), before)
        self.backend.on_external.assert_awaited_once()

    async def test_source_notification_during_final_read_defeats_the_send(self):
        await self.start()
        count = 0
        async def revoke():
            nonlocal count
            count += 1
            if count >= 3:
                self.revoked = True
        self.before_media = revoke
        before = self.mutations()
        with self.assertRaises(OwnershipLost):
            await self.backend.pause()
        self.assertEqual(self.mutations(), before)

    async def test_changed_description_rebind_keeps_selection_without_stop(self):
        await self.start()
        old, token = self.backend._client, self.backend.token
        self.binding = {**self.binding, 'descriptionUrl': str(self.server.make_url('/changed.xml'))}
        self.assertTrue(await self.backend.can_play())
        self.assertTrue(old.retired)
        self.assertIsNone(old._session)
        self.assertEqual(self.backend.token, token)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])
        # Even a copied old context cannot send through the retired client.
        context = self.backend.context.set((self.backend.generation, old.epoch, False))
        try:
            with self.assertRaises(OwnershipLost):
                await old.pause()
        finally:
            self.backend.context.reset(context)

    async def test_missing_renderer_retains_session_but_never_uses_old_endpoint(self):
        await self.start()
        token = self.backend.token
        old_binding = self.binding
        self.binding = None
        self.assertFalse(await self.backend.can_play())
        self.assertEqual(self.backend.token, token)
        self.assertIsNone(self.backend._client)
        self.binding = old_binding
        self.assertTrue(await self.backend.can_play())
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])

    async def test_wrong_identity_is_rejected_before_any_mutation(self):
        self.udn = 'synthetic-unrelated-zone'
        await self.assert_start_failure()
        self.assertEqual(self.mutations(), [])

    async def test_missing_state_read_is_not_reported_as_natural_stop(self):
        await self.start()
        self.missing_reads = True
        self.assertEqual(await self.backend.get_state(), PlaybackState.LOADING)
        self.assertFalse(await self.backend.can_play())

    async def test_competing_virtual_source_revokes_even_during_post_play_grace(self):
        await self.start()
        self.uri = 'https://example.test/competing-source'
        self.assertFalse(await self.backend.can_play())
        self.assertIsNone(self.backend.token)
        self.backend.on_external.assert_awaited_once()

    async def test_volume_missing_declaration_never_sends_fallback(self):
        await self.start()
        self.backend._client.volume_scpd = None
        with self.assertRaises(OwnershipLost):
            await self.backend.set_volume(4)
        self.assertNotIn('SetVolume', self.mutations())

    async def test_seek_710_is_nonfatal_and_observed_position_is_read_only(self):
        await self.start()
        self.fail_action, self.fail_code = 'Seek', 710
        with self.assertRaises(SeekUnsupported):
            await self.backend.seek(90000)
        self.assertIsNotNone(self.backend.token)
        self.assertEqual(await self.backend.observed_position(), 7000)
        self.assertEqual(self.mutations().count('Seek'), 1)

    async def test_uncertain_volume_keeps_only_fresh_owner_and_blocks_later_writes(self):
        await self.start()
        self.fail_action = 'SetVolume'
        with self.assertRaises(VolumeUncertain):
            await self.backend.set_volume(4)
        self.assertIsNotNone(self.backend.token)
        self.assertEqual(self.backend.last_volume_result['observedVolume'], 3)
        with self.assertRaises(VolumeUncertain):
            await self.backend.set_volume(5)
        self.assertEqual(self.mutations().count('SetVolume'), 1)

    async def test_uncertain_volume_without_new_complete_physical_reads_releases(self):
        await self.start()
        self.fail_action = 'SetVolume'
        self.backend.volume_reconcile_seconds = .02
        async def stale(room, action, **kwargs):
            return {'binding': self.binding, 'physicalReady': True, 'physicalReads': [1]}
        self.node.binding.side_effect = stale
        with self.assertRaises(OwnershipLost):
            await self.backend.set_volume(4)
        self.assertIsNone(self.backend.token)
        self.assertEqual(self.mutations().count('SetVolume'), 1)

    async def test_old_read_completion_after_rebinding_cannot_revoke_current_binding(self):
        await self.start()
        old = self.backend._client
        entered, finish = asyncio.Event(), asyncio.Event()
        async def delayed_media():
            entered.set()
            await finish.wait()
        self.before_media = delayed_media
        read = asyncio.create_task(old.get_media_info())
        await asyncio.wait_for(entered.wait(), 1)
        self.before_media = None
        self.binding = {**self.binding, 'descriptionUrl': str(self.server.make_url('/changed.xml'))}
        await self.backend.resolve()
        self.uri = 'https://example.test/late-old-source'
        finish.set()
        self.assertIsNone(await read)
        self.assertIsNotNone(self.backend.token)
        self.assertIsNot(self.backend._client, old)

    async def test_hardware_volume_readback_reports_without_a_setter(self):
        await self.start()
        self.volume = 7
        self.backend.on_volume = AsyncMock()
        self.backend._is_connected = True
        task = asyncio.create_task(self.backend.observe())
        try:
            for _ in range(100):
                if self.backend.on_volume.await_count:
                    break
                await asyncio.sleep(.01)
            self.backend.on_volume.assert_awaited_once_with(7)
            self.assertNotIn('SetVolume', self.mutations())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_volume_broadcast_echo_is_not_a_setter(self):
        player = MagicMock()
        player.set_volume = AsyncMock()
        await DirectVolumeHandler(player)._handle_volume_changed(MagicMock())
        player.set_volume.assert_not_awaited()

    async def test_close_releases_authority_and_http_without_stop(self):
        await self.start()
        client = self.backend._client
        await self.backend.release()
        await self.backend.stop()
        await self.backend.disconnect()
        self.assertTrue(client.retired)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play'])

    async def test_two_process_binding_to_direct_soap_and_real_relay_bytes_then_takeover(self):
        await self.backend.disconnect()
        child = subprocess.Popen(['node', 'tests/fake-binding-node.js', self.binding['descriptionUrl']],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        relay_server = None
        try:
            port = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 5)
            self.node = RaumfeldClient('http://127.0.0.1:' + port.strip(), 'synthetic-integration-token-1234567890')
            async def audio(request):
                return web.Response(body=b'fLaC-synthetic-delivery', content_type='audio/flac')
            source_app = web.Application()
            source_app.router.add_get('/synthetic-audio', audio)
            source_server = TestServer(source_app)
            await source_server.start_server()
            self.addAsyncCleanup(source_server.close)
            relay_app = web.Application()
            relay = AudioRelay(relay_app, '127.0.0.1', 8790, timeline=PlaybackTimeline())
            relay_server = TestServer(relay_app, port=8790)
            await relay_server.start_server()
            self.backend = RaumfeldDLNABackend(self.node, 'synthetic-room', 'Synthetic', relay)
            await self.backend.select('a' * 64)
            await self.backend.play(str(source_server.make_url('/synthetic-audio')), self.metadata)
            import aiohttp
            async with aiohttp.ClientSession() as speaker:
                async with speaker.get(self.uri) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.read(), b'fLaC-synthetic-delivery')
            child.stdin.write('loaded\n'); child.stdin.flush()
            await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 5)
            await self.backend.pause()
            self.assertTrue(await self.backend.resume())
            child.stdin.write('native\n'); child.stdin.flush()
            await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 5)
            before = self.mutations()
            self.assertFalse(await self.backend.can_play())
            with self.assertRaises(OwnershipLost):
                await self.backend.pause()
            self.assertEqual(self.mutations(), before)
            self.assertTrue(any(e['event'] == 'audio_delivery' and e['bytesWritten'] > 0
                                for e in relay.timeline.snapshot()))
        finally:
            await self.backend.disconnect()
            if relay_server:
                await relay_server.close()
            child.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(child.wait), 5)
            except TimeoutError:
                child.kill()
                await asyncio.to_thread(child.wait)
            child.stdin.close(); child.stdout.close()

    async def test_real_player_natural_repeat_reports_end_then_start_on_same_selection(self):
        metadata = MagicMock()
        metadata.get_streaming_url = AsyncMock(return_value='https://example.test/audio')
        metadata.get_track_format.return_value = (6, 44100, 16)
        metadata.get_track_actual_quality.return_value = 6
        metadata.get_track_blob.return_value = 'synthetic-blob'
        api = MagicMock()
        api.report_streaming_start = AsyncMock(return_value=True)
        api.report_streaming_end = AsyncMock(return_value=True)
        reporter = PlayReporter(ObservedReportingAPI(api, self.backend.relay.timeline))
        player = RaumfeldPlayer(queue=QobuzQueue(), metadata_service=metadata, backend=self.backend, play_reporter=reporter)
        player.set_playback_permission_check(self.backend.can_play)
        track = MagicMock()
        track.track_id = '101'
        track.streaming_url = 'https://example.test/audio'
        track.url_is_stale.return_value = False
        track.metadata = {'title': 'Synthetic', 'duration_ms': 180000}
        track.duration_ms = 180000
        track.context_uuid = bytes([1]) * 16
        player._current_track = track
        self.assertTrue(await player._start_playback())
        token = self.backend.token
        self.transport = 'STOPPED'
        await player.queue.set_repeat_mode(RepeatMode.ONE)
        await player._handle_track_ended(track)
        self.assertEqual(self.backend.token, token)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play', 'SetAVTransportURI', 'Play'])
        self.assertEqual(api.report_streaming_start.await_count, 2)
        api.report_streaming_end.assert_awaited_once()
        self.assertTrue(all(e['attempt'] > 0 for e in self.backend.relay.timeline.snapshot()
                            if e['event'] in {'play_attempt', 'report_start_attempt'}))
        next_track = MagicMock()
        next_track.track_id = '102'
        next_track.streaming_url = 'https://example.test/next-audio'
        next_track.url_is_stale.return_value = False
        next_track.metadata = track.metadata
        next_track.duration_ms = 180000
        next_track.context_uuid = bytes([2]) * 16
        player.queue.advance_to_next = AsyncMock(return_value=next_track)
        self.assertTrue(await player.next_track())
        self.assertEqual(self.mutations()[-3:], ['Stop', 'SetAVTransportURI', 'Play'])
        self.assertEqual(api.report_streaming_start.await_count, 3)
        await player.release_external_playback()
        self.revoked = True
        before = self.mutations()
        await player._handle_track_ended(next_track)
        self.assertEqual(self.mutations(), before)


class DirectServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_renderer_does_not_remove_configured_receiver_or_create_zone(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'PLAYBACK_PATH': 'direct_dlna'}):
            node = MagicMock()
            node.state = AsyncMock(return_value={'rooms': [], 'zones': [], 'renderers': [], 'topologyFresh': True})
            service = Service(node, 'x' * 32, directory, '192.0.2.10')
            service.settings = {'rooms': [{'id': 'synthetic-room', 'name': 'Synthetic', 'port': 8790}], 'quality': 6}
            service.api = MagicMock()
            receiver = MagicMock()
            receiver.start = AsyncMock()
            receiver.stop = AsyncMock()
            with patch('qobuz.service.local_address', return_value=True), patch('qobuz.service.Receiver', return_value=receiver):
                await service.reconcile_once()
                await service.reconcile_once()
            receiver.start.assert_awaited_once()
            receiver.stop.assert_not_awaited()
            node.control.assert_not_called()
            node.binding.assert_not_called()
            await service.stop_receivers()


class ReportingDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_proxy_delegates_to_current_api_and_retains_only_safe_fields(self):
        api = MagicMock()
        api.report_streaming_start = AsyncMock(return_value=True)
        api.report_streaming_end = AsyncMock(return_value=False)
        timeline = PlaybackTimeline()
        proxy = ObservedReportingAPI(api, timeline)
        self.assertTrue(await proxy.report_streaming_start(track_id='101', format_id=6))
        self.assertFalse(await proxy.report_streaming_end(track_id='101', format_id=6, blob='private-synthetic',
            context_uuid='opaque-synthetic', started_at_ms=1, played_seconds=60))
        text = str(timeline.snapshot())
        self.assertNotIn('private-synthetic', text)
        self.assertNotIn('opaque-synthetic', text)
        self.assertTrue(timeline.snapshot()[-1]['blobPresent'])
        self.assertFalse(timeline.snapshot()[-1]['success'])

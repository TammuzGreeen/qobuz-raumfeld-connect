"""Offline architecture probes against the pinned upstream, never real accounts."""
import json
import logging
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import TestServer
from qobuz_proxy.auth.api_client import QobuzAPIClient
from qobuz_proxy.backends.dlna.backend import DLNABackend
from qobuz_proxy.backends.types import BackendTrackMetadata
from qobuz_proxy.config import SpeakerConfig
from qobuz_proxy.playback import QobuzQueue
from qobuz_proxy.playback.play_reporter import PlayReporter
from qobuz_proxy.speaker import Speaker
from qobuz.player import RaumfeldPlayer
from qobuz.diagnostics import ConnectDiagnostics


class ReportingPathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.report_status = 201
        async def session(request):
            return web.json_response({'session_id': 'synthetic-session', 'expires_at': int(time.time()) + 3600})
        async def report(request):
            self.assertEqual(request.headers.get('X-User-Auth-Token'), 'synthetic-auth-token')
            self.assertEqual(request.headers.get('X-App-Id'), 'synthetic-app')
            self.assertEqual(request.headers.get('X-Session-Id'), 'synthetic-session')
            if request.path.endswith('Start'):
                # Mirrors upstream's actual raw form body; no API methods mocked.
                body = await request.text()
                self.assertTrue(body.startswith('events='))
                payload = json.loads(body[len('events='):])
            else:
                payload = await request.json()
            self.received.append((request.path, payload))
            return web.json_response({'status': 'synthetic-response'}, status=self.report_status)
        app = web.Application()
        app.router.add_post('/session/start', session)
        app.router.add_post('/track/reportStreamingStart', report)
        app.router.add_post('/track/reportStreamingEndJson', report)
        self.server = TestServer(app)
        await self.server.start_server()
        self.api = QobuzAPIClient('synthetic-app', 'synthetic-secret')
        self.api.API_BASE = str(self.server.make_url('/')).rstrip('/')
        self.api.user_id = '1'
        self.api.user_auth_token = 'synthetic-auth-token'
        self.now = [0]
        self.reporter = PlayReporter(self.api, clock=lambda: self.now[0])
        metadata = MagicMock()
        metadata.get_track_format.return_value = (6, 44100, 16)
        metadata.get_track_actual_quality.return_value = 6
        metadata.get_track_blob.return_value = 'synthetic-blob'
        backend = MagicMock()
        backend.play = AsyncMock()
        backend.relay.timeline = None
        self.player = RaumfeldPlayer(queue=QobuzQueue(), metadata_service=metadata,
            backend=backend, play_reporter=self.reporter)
        track = MagicMock()
        track.track_id = '101'
        track.streaming_url = 'https://example.test/synthetic-audio'
        track.url_is_stale.return_value = False
        track.metadata = {'title': 'Synthetic', 'duration_ms': 180000}
        track.duration_ms = 180000
        track.context_uuid = bytes([1]) * 16
        self.player._current_track = track

    async def asyncTearDown(self):
        await self.api.__aexit__(None, None, None)
        await self.server.close()

    async def test_current_player_reports_start_and_end_with_paused_time_excluded(self):
        self.assertTrue(await self.player._start_playback(0))
        self.now[0] = 20000
        self.player._report_paused()
        self.now[0] = 30000
        await self.player._report_playing(10000)  # Resume does not duplicate start.
        self.now[0] = 80000
        await self.player.release_external_playback()
        self.assertEqual([p for p, _ in self.received], ['/track/reportStreamingStart', '/track/reportStreamingEndJson'])
        self.assertEqual(self.received[0][1][0]['format_id'], 6)
        end = self.received[1][1]['events'][0]
        self.assertEqual(end['duration'], 70)
        self.assertEqual(end['blob'], 'synthetic-blob')
        self.assertTrue(end['track_context_uuid'])
        self.player.backend.stop.assert_not_called()

    async def test_api_acknowledgement_accepts_201_and_rejects_403(self):
        self.assertTrue(await self.api.report_streaming_start(track_id='101', format_id=6))
        self.report_status = 403
        with self.assertLogs('qobuz_proxy.auth.api_client', level='WARNING'):
            self.assertFalse(await self.api.report_streaming_start(track_id='101', format_id=6))

    async def test_adopted_handoff_suppresses_start_but_sends_end(self):
        self.assertTrue(await self.player._start_playback(6000))
        self.assertEqual(self.received, [])
        self.now[0] = 60000
        await self.player.release_external_playback()
        self.assertEqual([p for p, _ in self.received], ['/track/reportStreamingEndJson'])

    async def test_rejected_report_is_not_a_playback_failure(self):
        self.report_status = 403
        with self.assertLogs('qobuz_proxy.auth.api_client', level='WARNING'):
            self.assertTrue(await self.player._start_playback(0))
        self.assertEqual(len(self.received), 1)
        self.assertIsNotNone(self.reporter._active)
        # Existing diagnostics omit this warning; account-visible reporting is
        # not proven by login, SOAP or player success.
        diagnostics = ConnectDiagnostics()
        diagnostics.handle(logging.LogRecord('test', logging.WARNING, '', 0,
            'Streaming report (start) failed: HTTP 403 — synthetic-response', (), None))
        self.assertEqual(diagnostics.snapshot(), [])


class StockBackendLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_stock_backend_retries_transport_with_stop_after_uri_failure(self):
        backend = DLNABackend('192.0.2.1', description_url='http://192.0.2.1:55001/device.xml')
        client = MagicMock()
        client.set_av_transport_uri = AsyncMock(return_value=False)
        client.reset_session = AsyncMock()
        client.stop = AsyncMock(return_value=True)
        client.play = AsyncMock(return_value=True)
        backend._client = client
        with self.assertLogs('qobuz_proxy.backends.dlna.backend', level='WARNING'):
            result = await backend.play('https://example.test/synthetic-audio', BackendTrackMetadata('101', title='Synthetic'))
        self.assertIsNone(result)  # Failure was not raised to the player.
        self.assertEqual(client.set_av_transport_uri.await_count, 2)
        client.stop.assert_awaited_once()
        client.play.assert_not_awaited()

    async def test_stock_speaker_does_not_advertise_if_backend_creation_fails(self):
        speaker = Speaker(config=SpeakerConfig(name='Synthetic', backend_type='dlna',
            dlna_ip='192.0.2.1', dlna_description_url='http://192.0.2.1:55001/device.xml'),
            api_client=MagicMock(), app_id='synthetic-app')
        with patch('qobuz_proxy.speaker.BackendFactory.create_from_config',
                   AsyncMock(side_effect=RuntimeError('synthetic missing renderer'))), \
             patch('qobuz_proxy.speaker.DiscoveryService') as discovery, \
             self.assertLogs('qobuz_proxy.speaker', level='ERROR'):
            self.assertFalse(await speaker.start())
            discovery.assert_not_called()

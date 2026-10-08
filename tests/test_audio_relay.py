"""Synthetic byte transport, not decoder or audible-speaker acceptance."""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from qobuz.backend import AudioRelay
from qobuz.diagnostics import PlaybackTimeline, ObservedMetadata


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.status = 200
        self.seen = []
        async def source(request):
            self.seen.append(dict(request.headers))
            headers = {'Content-Type': 'audio/flac', 'Accept-Ranges': 'bytes'}
            if self.status == 206:
                headers['Content-Range'] = 'bytes 0-3/8'
            if self.status == 416:
                headers['Content-Range'] = 'bytes */8'
            return web.Response(status=self.status, body=b'fLaC', headers=headers)
        upstream = web.Application()
        upstream.router.add_get('/synthetic-audio', source)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        self.timeline = PlaybackTimeline()
        self.app = web.Application()
        self.relay = AudioRelay(self.app, '192.0.2.2', 8790, timeline=self.timeline)
        token = self.timeline.begin()
        url = self.relay.register(str(self.upstream.make_url('/synthetic-audio')))
        self.timeline.attempt.reset(token)
        self.path = '/audio/' + url.rsplit('/', 1)[-1]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.upstream.close()

    async def test_full_delivery_records_bytes_not_an_audible_claim(self):
        response = await self.client.get(self.path)
        self.assertEqual(await response.read(), b'fLaC')
        self.assertEqual(response.status, 200)
        events = self.timeline.snapshot()
        self.assertEqual([e['event'] for e in events], ['play_attempt', 'relay_registered',
            'relay_request', 'upstream_response', 'audio_delivery', 'audio_delivery'])
        self.assertTrue(all(e['attempt'] == 1 for e in events))
        self.assertEqual(events[-1]['bytesWritten'], 4)
        self.assertEqual(events[3]['contentType'], 'flac')
        self.assertNotIn(self.path, json.dumps(events))
        self.assertNotIn('127.0.0.1', json.dumps(events))

    async def test_range_and_if_range_preserve_existing_transport_behavior(self):
        self.status = 206
        response = await self.client.get(self.path, headers={'Range': 'bytes=0-3', 'If-Range': 'synthetic-etag'})
        self.assertEqual(response.status, 206)
        self.assertEqual(response.headers['Content-Range'], 'bytes 0-3/8')
        self.assertEqual(await response.read(), b'fLaC')
        self.assertEqual(self.seen[0]['Range'], 'bytes=0-3')
        self.assertEqual(self.seen[0]['If-Range'], 'synthetic-etag')
        self.assertEqual(self.seen[0]['Accept-Encoding'], 'identity')
        events = self.timeline.snapshot()
        self.assertTrue(next(e for e in events if e['event'] == 'relay_request')['rangeRequested'])
        self.assertNotIn('synthetic-etag', json.dumps(events))

    async def test_head_never_claims_audio_bytes(self):
        response = await self.client.head(self.path)
        self.assertEqual(await response.read(), b'')
        delivery = [e for e in self.timeline.snapshot() if e['event'] == 'audio_delivery']
        self.assertEqual(delivery[-1]['bytesWritten'], 0)

    async def test_416_and_bad_upstream_are_distinguished(self):
        self.status = 416
        response = await self.client.get(self.path, headers={'Range': 'bytes=999-'})
        await response.read()
        self.assertEqual(response.status, 416)
        self.assertEqual(response.headers['Content-Range'], 'bytes */8')
        self.status = 403
        response = await self.client.get(self.path)
        await response.read()
        self.assertEqual(response.status, 502)
        events = self.timeline.snapshot()
        self.assertEqual([e['status'] for e in events if e['event'] == 'upstream_response'], [416, 403])
        self.assertEqual(events[-1]['event'], 'relay_error')
        self.assertEqual(events[-1]['bytesWritten'], 0)

    async def test_expired_relay_key_is_observed_without_upstream_request(self):
        for _ in range(4):
            self.relay.register(str(self.upstream.make_url('/synthetic-audio')))
        response = await self.client.get(self.path)
        await response.read()
        self.assertEqual(response.status, 404)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.timeline.snapshot()[-1]['status'], 404)


class TimelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_metadata_methods_keep_upstream_logic_and_correlate_lookup(self):
        api = MagicMock()
        api.get_track_metadata = AsyncMock(return_value={'title': 'Synthetic', 'duration_ms': 1000})
        api.get_track_url = AsyncMock(return_value={'url': 'https://example.test/synthetic',
            'format_id': 6, 'bit_depth': 16, 'sampling_rate': 44.1})
        timeline = PlaybackTimeline()
        token = timeline.begin()
        try:
            metadata = ObservedMetadata(api, max_quality=6, timeline=timeline)
            self.assertEqual(await metadata.get_streaming_url('synthetic-track'), 'https://example.test/synthetic')
        finally:
            timeline.attempt.reset(token)
        events = timeline.snapshot()
        self.assertEqual([e['event'] for e in events], ['play_attempt', 'metadata_lookup', 'stream_lookup'])
        self.assertTrue(all(e['attempt'] == 1 for e in events))
        self.assertNotIn('synthetic-track', json.dumps(events))
        self.assertNotIn('https://', json.dumps(events))

    async def test_timeline_rejects_sensitive_fields_and_is_bounded_copied(self):
        timeline = PlaybackTimeline()
        for _ in range(80):
            timeline.note('relay_request', attempt='PRIVATE', url='PRIVATE', exception='PRIVATE', contentType='PRIVATE', status=200)
        timeline.note('PRIVATE')
        events = timeline.snapshot()
        self.assertEqual(len(events), 64)
        self.assertNotIn('PRIVATE', json.dumps(events))
        events[0]['event'] = 'modified'
        self.assertEqual(timeline.snapshot()[0]['event'], 'relay_request')

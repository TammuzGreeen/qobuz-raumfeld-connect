"""Categorical explicit-renderer errors across the local HTTP boundary, no speakers."""
import runpy
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

from qobuz.upstream_backend import CompanionClient, RendererRequestError


class RendererRequestErrors(unittest.IsolatedAsyncioTestCase):
    async def test_local_renderer_and_network_failures_survive_without_retry(self):
        for origin, phase, detail in (
            ('local_check', 'preflight', {'error': 'topology_unavailable', 'requestAttempted': False}),
            ('local_check', 'uncertainty_block', {'error': 'creation_pending_or_uncertain', 'requestAttempted': False}),
            ('raumfeld_response', 'response', {'rendererHttpStatus': 503, 'requestAttempted': True}),
            ('network', 'network', {'networkCode': 'ECONNREFUSED', 'requestAttempted': True}),
        ):
            with self.subTest(origin=origin, phase=phase):
                calls = []
                async def fail(request):
                    calls.append(await request.json())
                    return web.json_response({'origin': origin, 'phase': phase,
                        'privateMetadata': 'synthetic-private', **detail}, status=409)
                app = web.Application()
                app.router.add_post('/v1/zone-for-play', fail)
                async with TestServer(app) as server:
                    client = CompanionClient('synthetic-token', str(server.make_url('/')).rstrip('/'))
                    try:
                        with self.assertRaises(RendererRequestError) as caught:
                            await client.create_for_play('synthetic-room')
                        error = caught.exception
                        self.assertEqual(error.status, 409)
                        self.assertEqual(error.origin, origin)
                        self.assertEqual(error.phase, phase)
                        self.assertEqual(error.request_attempted, detail['requestAttempted'])
                        self.assertEqual(error.renderer_http_status, detail.get('rendererHttpStatus'))
                        self.assertEqual(error.network_code, detail.get('networkCode', 'unknown'))
                        self.assertNotIn('synthetic-private', str(error))
                        self.assertEqual(calls, [{'roomId': 'synthetic-room'}])
                    finally:
                        await client.close()

    async def test_invalid_or_oversized_errors_stay_unknown_not_raw_or_retried(self):
        for body in ('not-json-synthetic-private', 'x'*8192,
                     '{"origin":[],"phase":{},"error":{},"networkCode":[],"rendererHttpStatus":true}'):
            calls = []
            async def fail(request):
                calls.append(True)
                return web.Response(text=body, status=503)
            app = web.Application()
            app.router.add_post('/v1/zone-for-play', fail)
            async with TestServer(app) as server:
                client = CompanionClient('synthetic-token', str(server.make_url('/')).rstrip('/'))
                try:
                    with self.assertRaises(RendererRequestError) as caught:
                        await client.create_for_play('synthetic-room')
                    self.assertEqual(caught.exception.origin, 'unknown')
                    self.assertEqual(caught.exception.request_attempted, 'unknown')
                    self.assertNotIn('synthetic-private', str(caught.exception))
                    self.assertIsNone(caught.exception.renderer_http_status)
                    self.assertEqual(len(calls), 1)
                finally:
                    await client.close()


class RendererPatchTests(unittest.TestCase):
    def test_unreviewed_sdk_source_is_rejected(self):
        root = Path(__file__).resolve().parents[1]
        apply = runpy.run_path(str(root / 'docker/apply-raumkernel-creation-errors.py'))['apply']
        with self.assertRaisesRegex(ValueError, 'Unreviewed node-raumkernel'):
            apply(b'synthetic changed dependency')

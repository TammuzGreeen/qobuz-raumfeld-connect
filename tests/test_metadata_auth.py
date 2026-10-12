"""Exercise the pinned upstream HTTP client, not mocked metadata methods."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import TestServer
from qobuz_proxy.auth.api_client import QobuzAPIClient
from qobuz_proxy.playback import MetadataService
from qobuz.service import Service, save_json, load_json


class MetadataAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.requests = []
        self.current_token = 'cached-test-token'
        self.login_count = 0
        self.metadata_auth_checks = []
        self.login_status = 200

        async def login(request):
            if self.login_status != 200:
                return web.json_response({'error': 'test login failure'}, status=self.login_status)
            self.assertTrue(request.headers.get('X-User-Auth-Token') == self.current_token,
                            'login did not send the expected test token')
            self.login_count += 1
            self.current_token = f'refreshed-test-token-{self.login_count}'
            return web.json_response({'user_auth_token': self.current_token})

        async def authenticated(request):
            self.requests.append(request.path)
            authenticated = request.headers.get('X-User-Auth-Token') == self.current_token
            if request.path == '/track/get':
                self.metadata_auth_checks.append(authenticated)
            if not authenticated:
                return web.json_response({'error': 'missing authentication'}, status=401)
            if request.path == '/track/get':
                return web.json_response({'title': 'Test track', 'duration': 180,
                    'performer': {'name': 'Test artist'}, 'album': {'title': 'Test album'}})
            if request.path == '/session/start':
                return web.json_response({'session_id': 'test-stream-session', 'expires_at': int(time.time()) + 3600})
            self.assertEqual(request.query['format_id'], '6')
            return web.json_response({'url': 'https://audio.example.test/opaque-test-stream',
                                     'format_id': 6, 'bit_depth': 16, 'sampling_rate': 44.1})

        app = web.Application()
        app.router.add_post('/user/login', login)
        app.router.add_get('/track/get', authenticated)
        app.router.add_post('/session/start', authenticated)
        app.router.add_get('/track/getFileUrl', authenticated)
        self.server = TestServer(app)
        await self.server.start_server()
        self.api_base = str(self.server.make_url('/')).rstrip('/')
        self.service = Service(MagicMock(), 'x' * 32, self.tmp.name)

    async def asyncTearDown(self):
        if self.service.api:
            await self.service.api.__aexit__(None, None, None)
        await self.server.close()
        self.tmp.cleanup()

    async def test_unmodified_client_metadata_session_does_not_inherit_login_token(self):
        async with QobuzAPIClient('test-app', 'test-secret') as api:
            api.API_BASE = self.api_base
            self.assertTrue(await api.login_with_token('1', 'cached-test-token'))
            self.assertNotIn('X-User-Auth-Token', api._session.headers)
            with self.assertLogs('qobuz_proxy.auth.api_client', level='WARNING'):
                self.assertIsNone(await api.get_track_metadata('synthetic-track'))
            self.assertEqual(self.metadata_auth_checks, [False])

    async def test_setup_uses_refreshed_token_for_metadata_and_cd_stream_resolution(self):
        class LocalAPI(QobuzAPIClient):
            API_BASE = self.api_base

        save_json(Path(self.tmp.name) / 'credentials.json',
                  {'user_id': '1', 'user_auth_token': 'cached-test-token'})
        with patch('qobuz.service.QobuzAPIClient', LocalAPI):
            await self.service.authenticate()
        self.assertEqual(self.service.auth_status, 'connected')
        api = self.service.api
        self.assertIsNone(api._session)
        metadata = await MetadataService(api, max_quality=6).get_metadata('synthetic-track', fetch_url=True)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.title, 'Test track')
        self.assertEqual(metadata.duration_ms, 180000)
        self.assertEqual(metadata.actual_quality, 6)
        self.assertEqual(metadata.bit_depth, 16)
        self.assertEqual(metadata.sample_rate, 44100)
        self.assertTrue(metadata.streaming_url)
        self.assertEqual(self.requests, ['/track/get', '/session/start', '/track/getFileUrl'])
        self.assertTrue(load_json(Path(self.tmp.name) / 'credentials.json', {})['user_auth_token'] == self.current_token,
                        'validated token was not persisted')
        self.assertEqual(self.metadata_auth_checks, [True])
        # A later login refresh changes the token on the same API client. The
        # next real HTTP metadata request must use it, not a header cached when
        # the client was constructed or first authenticated.
        self.assertTrue(await api.login_with_token('1', api.user_auth_token))
        self.assertIsNotNone(await api.get_track_metadata('another-synthetic-track'))
        self.assertEqual(self.metadata_auth_checks, [True, True])
        self.assertEqual(self.login_count, 2)
        self.assertIsNone(api._session)
        self.service.client.control.assert_not_called()

    async def test_rejection_and_transient_retry_preserve_credentials_without_entering_session(self):
        class LocalAPI(QobuzAPIClient):
            API_BASE = self.api_base

        credentials = {'user_id': '1', 'user_auth_token': 'cached-test-token'}
        path = Path(self.tmp.name) / 'credentials.json'
        save_json(path, credentials)
        with patch('qobuz.service.QobuzAPIClient', LocalAPI):
            self.login_status = 401
            with self.assertLogs('qobuz_proxy.auth.api_client', level='WARNING'):
                await self.service.authenticate()
            self.assertEqual(self.service.auth_status, 'login_required')
            self.assertIsNone(self.service.api)
            self.assertTrue(self.service.retry_at > time.monotonic())
            self.assertTrue(load_json(path, {}) == credentials, 'rejected token should be retained')
            self.login_status = 503
            await self.service.authenticate()
            self.assertEqual(self.service.auth_status, 'retrying_login')
            self.assertIsNone(self.service.api)
            self.assertTrue(self.service.retry_at > time.monotonic())
            self.assertTrue(load_json(path, {}) == credentials, 'transient failure must preserve credentials')
        self.service.client.control.assert_not_called()

    async def test_logout_cleanup_is_safe_for_upstream_temporary_session_lifecycle(self):
        class LocalAPI(QobuzAPIClient):
            API_BASE = self.api_base

        path = Path(self.tmp.name) / 'credentials.json'
        save_json(path, {'user_id': '1', 'user_auth_token': 'cached-test-token'})
        with patch('qobuz.service.QobuzAPIClient', LocalAPI):
            await self.service.authenticate()
        self.assertIsNone(self.service.api._session)
        response = await self.service.logout(None)
        self.assertEqual(response.status, 200)
        self.assertIsNone(self.service.api)
        self.assertEqual(self.service.auth_status, 'login_required')
        self.assertEqual(load_json(path, {}), {})
        self.service.client.control.assert_not_called()

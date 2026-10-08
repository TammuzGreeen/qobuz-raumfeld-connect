"""Two-process contract test against a simulated speaker; no Qobuz account needed."""
import asyncio
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import TestServer
from qobuz_proxy.auth.api_client import QobuzAPIClient
from qobuz_proxy.playback import MetadataService
from qobuz.client import RaumfeldClient, OwnershipLost
from qobuz.backend import RaumfeldBackend, SeekUnsupported
from qobuz.service import Service, save_json
from qobuz_proxy.backends.types import BackendTrackMetadata


async def run():
    child = subprocess.Popen(['node','tests/fake-node.js'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    temp = tempfile.TemporaryDirectory()
    service = server = None
    try:
        port = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=10)
        client=RaumfeldClient('http://127.0.0.1:'+port.strip(),'integration-test-token-12345678901234567890')
        relay=MagicMock()
        relay.register.return_value='http://127.0.0.1:8790/audio/'+'a'*32
        backend=RaumfeldBackend(client,'room','Room',relay)
        await backend.select('integration-selection')
        await backend.stop()  # Initial cloud snapshot must not stop Spotify.
        assert (await client.state())['rooms'][0]['source']=='spotify'

        @web.middleware
        async def endpoint_status(request, handler):
            response = await handler(request)
            print('Simulated Qobuz endpoint', request.path, 'HTTP', response.status)
            return response

        async def login(request):
            return web.json_response({'user_auth_token': 'validated-test-token'})

        async def authenticated(request):
            if request.headers.get('X-User-Auth-Token') != 'validated-test-token':
                return web.json_response({'error': 'missing auth'}, status=401)
            if request.path == '/track/get':
                return web.json_response({'title': 'Test', 'duration': 180})
            if request.path == '/session/start':
                return web.json_response({'session_id': 'test-session', 'expires_at': int(time.time()) + 3600})
            assert request.query['format_id'] == '6'
            return web.json_response({'url': 'https://example.test/audio', 'format_id': 6,
                                     'bit_depth': 16, 'sampling_rate': 44.1})

        app = web.Application(middlewares=[endpoint_status])
        app.router.add_post('/user/login', login)
        app.router.add_get('/track/get', authenticated)
        app.router.add_post('/session/start', authenticated)
        app.router.add_get('/track/getFileUrl', authenticated)
        server = TestServer(app)
        await server.start_server()

        class LocalAPI(QobuzAPIClient):
            API_BASE = str(server.make_url('/')).rstrip('/')

        save_json(Path(temp.name) / 'credentials.json',
                  {'user_id': '1', 'user_auth_token': 'cached-test-token'})
        service = Service(client, 'integration-test-token-12345678901234567890', temp.name)
        with patch('qobuz.service.QobuzAPIClient', LocalAPI):
            await service.authenticate()
        assert service.auth_status == 'connected'
        track = await MetadataService(service.api, max_quality=6).get_metadata('1', fetch_url=True)
        assert track and track.streaming_url and track.actual_quality == 6
        await backend.play(track.streaming_url, BackendTrackMetadata(track.track_id,
            title=track.title, bit_depth=track.bit_depth, sample_rate=track.sample_rate))
        assert backend.started
        print('CD-quality metadata → stream URL → guarded simulated backend Play passed')
        await backend.pause()
        await backend.resume()
        await backend.seek(12000)
        await backend.set_volume(40)
        assert (await client.state())['rooms'][0]['owned'] is True
        child.stdin.write('forwarding\n');child.stdin.flush()
        await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=5)
        assert (await client.state())['rooms'][0]['owned'] is True
        await backend.pause()
        await backend.resume()
        print('Topology-linked physical forwarding preserves virtual ownership and controls')
        child.stdin.write('seek-unsupported\n');child.stdin.flush()
        await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=5)
        try:
            await backend.seek(65000)
            raise AssertionError('Unsupported seek was falsely accepted')
        except SeekUnsupported:
            pass
        assert backend.started and backend.token
        assert (await client.state())['rooms'][0]['owned'] is True
        await backend.pause()
        await backend.resume()
        print('Rejected Seek 710 preserves ownership and subsequent controls')
        child.stdin.write('native\n');child.stdin.flush()
        await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=5)
        try:
            await backend.pause()
            raise AssertionError('Native takeover was not protected')
        except OwnershipLost:
            pass
        assert backend.token is None
        assert (await client.state())['rooms'][0]['source']=='spotify'
        print('Python → Node → simulated renderer integration passed')
    finally:
        if service and service.api:
            await service.api.__aexit__(None, None, None)
        if server:
            await server.close()
        temp.cleanup()
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill();child.wait()


asyncio.run(run())

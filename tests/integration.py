"""Two-process contract test against a simulated speaker; no Qobuz account needed."""
import asyncio
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from aiohttp import web
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from qobuz_proxy.auth.api_client import QobuzAPIClient
from qobuz.client import RaumfeldClient, OwnershipLost
from qobuz.backend import AudioRelay, RaumfeldBackend, SeekUnsupported, VolumeUncertain
from qobuz.diagnostics import PlaybackTimeline, ObservedMetadata
from qobuz.service import Service, save_json
from qobuz_proxy.backends.types import BackendTrackMetadata


async def run():
    child = subprocess.Popen(['node','tests/fake-node.js'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    temp = tempfile.TemporaryDirectory()
    service = server = relay_server = None
    try:
        port = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=10)
        client=RaumfeldClient('http://127.0.0.1:'+port.strip(),'integration-test-token-12345678901234567890')
        timeline = PlaybackTimeline()
        relay_app = web.Application()
        relay = AudioRelay(relay_app, '127.0.0.1', 8790, timeline=timeline)
        relay_server = TestServer(relay_app, port=8790)
        await relay_server.start_server()
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
            return web.json_response({'url': str(server.make_url('/synthetic-audio')), 'format_id': 6,
                                     'bit_depth': 16, 'sampling_rate': 44.1})

        app = web.Application(middlewares=[endpoint_status])
        app.router.add_post('/user/login', login)
        app.router.add_get('/track/get', authenticated)
        app.router.add_post('/session/start', authenticated)
        app.router.add_get('/track/getFileUrl', authenticated)
        async def synthetic_audio(request):
            return web.Response(body=b'fLaC-synthetic-byte-test', content_type='audio/flac')
        app.router.add_get('/synthetic-audio', synthetic_audio)
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
        attempt_token = timeline.begin()
        metadata = ObservedMetadata(service.api, max_quality=6, timeline=timeline)
        track = await metadata.get_metadata('1', fetch_url=True)
        assert await metadata.get_streaming_url('1')
        assert track and track.streaming_url and track.actual_quality == 6
        await backend.play(track.streaming_url, BackendTrackMetadata(track.track_id,
            title=track.title, bit_depth=track.bit_depth, sample_rate=track.sample_rate))
        assert backend.started
        timeline.attempt.reset(attempt_token)
        async with ClientSession() as simulated_speaker:
            async with simulated_speaker.get(relay_server.make_url('/audio/' + next(reversed(relay.tracks)))) as response:
                assert response.status == 200
                assert await response.read() == b'fLaC-synthetic-byte-test'
        events = timeline.snapshot()
        assert all(e['attempt'] == 1 for e in events)
        assert next(e for e in events if e['event'] == 'uri_load')['success']
        assert next(e for e in events if e['event'] == 'play_soap')['success']
        assert next(e for e in events if e['event'] == 'upstream_response')['status'] == 200
        assert events[-1]['bytesWritten'] == len(b'fLaC-synthetic-byte-test')
        print('Correlated simulated metadata → SOAP → relay HTTP request → bytes written passed; not audible evidence')
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
        child.stdin.write('volume-reset\n');child.stdin.flush()
        await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=5)
        try:
            await backend.set_volume(41)
            raise AssertionError('Reset volume was falsely reported successful')
        except VolumeUncertain:
            pass
        assert backend.token and backend.started
        assert backend.last_volume_result['controlAPI']['httpStatus'] == 200
        diagnostic = (await client.state())['rooms'][0]['lastVolumeCommand']
        assert diagnostic['connection'] == 'node_to_renderer'
        assert diagnostic['code'] == 'ECONNRESET'
        assert diagnostic['ownership'] == 'confirmed'
        await backend.pause()
        await backend.resume()
        print('SOAP volume reset keeps only freshly confirmed ownership; Python control API remains HTTP 200')
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
        if relay_server:
            await relay_server.close()
        temp.cleanup()
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill();child.wait()


asyncio.run(run())

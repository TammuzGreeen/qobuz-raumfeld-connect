"""Setup UI, OAuth and receiver lifecycle. Credentials stay in /data."""
import asyncio
import contextlib
import errno
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import time
from aiohttp import web
import ifaddr
from qobuz_proxy.auth.api_client import QobuzAPIClient
from qobuz_proxy.auth.oauth import build_oauth_url, exchange_code, OAUTH_APP_ID, OAUTH_APP_SECRET
from .client import RaumfeldClient
from .receiver import Receiver
from .diagnostics import ConnectDiagnostics


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf8') as out:
        json.dump(data, out)
    os.replace(temp, path)
    os.chmod(path, 0o600)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def local_address(address):
    """Check interfaces: kernels may permit non-local binds (ip_nonlocal_bind)."""
    if not address:
        return False
    ip = ipaddress.IPv4Address(address)
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast:
        return False
    try:
        return any(item.ip == address for adapter in ifaddr.get_adapters() for item in adapter.ips)
    except OSError:
        return False


class Service:
    def __init__(self, client, token, data_dir='/data', address=None):
        self.client, self.token = client, token
        self.data_dir = Path(data_dir)
        self.address = address
        self.playback_path = os.environ.get('PLAYBACK_PATH', 'guarded')
        if self.playback_path not in ('guarded', 'direct_dlna'):
            raise ValueError('Invalid PLAYBACK_PATH')
        if address:
            ipaddress.IPv4Address(address)
        self.settings = load_json(self.data_dir / 'config.json', {'rooms': [], 'quality': 6})
        self.api = None
        self.receivers = {}
        self.receiver_errors = {}
        self.state_error = None
        self.connect_diagnostics = ConnectDiagnostics()
        self.nonces = {}
        self.lock = asyncio.Lock()
        self.state = {'apiVersion': '1', 'topologyFresh': False, 'rooms': [], 'renderers': []}
        self.auth_status = 'login_required'
        self.retry_at = 0
        self.auth_generation = 0
        self.closed = asyncio.Event()
        self.app = web.Application(middlewares=[self.auth], client_max_size=16384)
        for method, route, handler in [('GET','/',self.page), ('GET','/healthz',self.health),
                ('GET','/api/status',self.status), ('POST','/api/config',self.configure),
                ('POST','/api/login',self.login), ('GET','/oauth/callback',self.callback),
                ('POST','/api/logout',self.logout)]:
            self.app.router.add_route(method, route, handler)

    @web.middleware
    async def auth(self, request, handler):
        if request.path.startswith('/api/') and not hmac.compare_digest(
                request.headers.get('Authorization', ''), 'Bearer ' + self.token):
            return web.json_response({'error': 'Enter your setup token'}, status=401)
        try:
            response = await handler(request)
        except web.HTTPException:
            raise
        except Exception:
            return web.json_response({'error': 'Request failed; check configuration and retry'}, status=400)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    async def page(self, request):
        return web.Response(text=Path(__file__).with_name('index.html').read_text(encoding='utf8'), content_type='text/html')

    async def health(self, request):
        return web.json_response({'status': 'ok', 'mode': 'connect', 'configured': bool(self.settings['rooms'])})

    async def status(self, request):
        return web.json_response({'auth': self.auth_status, 'lanAddress': self.address,
            'lanAddressLocal': local_address(self.address), 'stateError': self.state_error,
            'quality': self.settings['quality'], 'playbackPath': self.playback_path,
            'selectedRooms': [r['id'] for r in self.settings['rooms']],
            'rooms': self.state['rooms'], 'raumfeldReady': self.state['topologyFresh'],
            'topologyAt': self.state.get('topologyAt'), 'observedAt': self.state.get('observedAt'),
            'renderers': self.state.get('renderers', []),
            'observationErrors': self.state.get('observationErrors', []),
            'receiverErrors': dict(self.receiver_errors),
            'receiverConnections': {id: receiver.diagnostics() for id, receiver in self.receivers.items()},
            'connectEvents': self.connect_diagnostics.snapshot(),
            'advertised': list(self.receivers), 'version': '0.2.0'})

    async def configure(self, request):
        data = await request.json()
        ids, quality = data.get('rooms'), data.get('quality', 6)
        if not isinstance(ids, list) or any(not isinstance(r, str) for r in ids) or len(ids) > 50 or len(set(ids)) != len(ids):
            raise web.HTTPBadRequest(text='Invalid rooms')
        if quality not in (5, 6, 7, 27):
            raise web.HTTPBadRequest(text='Invalid quality')
        if self.playback_path == 'direct_dlna' and (len(ids) != 1 or quality != 6):
            raise web.HTTPBadRequest(text='Direct experiment requires one room and CD quality')
        state = await self.client.state()
        known = {r['id']: r for r in state['rooms']}
        if any(r not in known for r in ids):
            raise web.HTTPBadRequest(text='Unknown room')
        async with self.lock:
            previous = {r['id']: r for r in self.settings['rooms']}
            used = {previous[r]['port'] for r in ids if r in previous}
            rooms = []
            for id in ids:
                port = previous[id]['port'] if id in previous else next(p for p in range(8790, 8840) if p not in used)
                used.add(port)
                rooms.append({'id': id, 'name': known[id]['name'] + ' Qobuz ' + id[-6:], 'port': port})
            self.settings = {'rooms': rooms, 'quality': quality}
            save_json(self.data_dir / 'config.json', self.settings)
            self.receiver_errors.clear()
            await self.stop_receivers()
        return web.json_response({'saved': True})

    async def login(self, request):
        self.nonces = {k: exp for k, exp in self.nonces.items() if exp > time.monotonic()}
        if len(self.nonces) >= 8:
            raise web.HTTPTooManyRequests()
        nonce = secrets.token_urlsafe(32)
        self.nonces[nonce] = time.monotonic() + 600
        base = os.environ.get('PUBLIC_URL', f'{request.scheme}://{request.host}').rstrip('/')
        return web.json_response({'url': build_oauth_url(base + '/oauth/callback?state=' + nonce)})

    async def callback(self, request):
        expires = self.nonces.pop(request.query.get('state', ''), 0)
        if expires < time.monotonic():
            raise web.HTTPBadRequest(text='Login expired. Return to setup and try again.')
        code = request.query.get('code_autorisation', '')
        if not code:
            raise web.HTTPBadRequest(text='Missing authorization code')
        generation = self.auth_generation
        credentials = await exchange_code(code)
        async with self.lock:
            if generation != self.auth_generation:
                raise web.HTTPBadRequest(text='Login superseded. Start again from setup.')
            self.auth_generation += 1
            self.nonces.clear()
            await self.stop_receivers()
            if self.api:
                await self.api.__aexit__(None, None, None)
                self.api = None
            save_json(self.data_dir / 'credentials.json', credentials)
            self.auth_status = 'validating'
            self.retry_at = 0
        raise web.HTTPFound('/')

    async def logout(self, request):
        async with self.lock:
            self.auth_generation += 1
            await self.stop_receivers()
            if self.api:
                await self.api.__aexit__(None, None, None)
            self.api = None
            self.nonces.clear()
            save_json(self.data_dir / 'credentials.json', {})
            self.auth_status = 'login_required'
        return web.json_response({'loggedOut': True})

    async def stop_receivers(self):
        receivers, self.receivers = self.receivers, {}
        await asyncio.gather(*(r.stop() for r in receivers.values()), return_exceptions=True)

    async def authenticate(self):
        credentials = load_json(self.data_dir / 'credentials.json', {})
        if not credentials.get('user_auth_token') or not credentials.get('user_id'):
            return
        api = QobuzAPIClient(OAUTH_APP_ID, OAUTH_APP_SECRET)
        # Match upstream app.py: do not enter this client's context manager.
        # Its persistent session omits auth and is not updated by login. With
        # no persistent session, metadata requests use the current user token
        # in an authenticated temporary session, including after token refresh.
        try:
            if not await api.login_with_token(credentials['user_id'], credentials['user_auth_token']):
                self.auth_status = 'login_required'
                self.retry_at = time.monotonic() + 300
                await api.__aexit__(None, None, None)
                return
            self.api = api
            self.auth_status = 'connected'
            credentials['user_auth_token'] = api.user_auth_token
            save_json(self.data_dir / 'credentials.json', credentials)
        except Exception:
            await api.__aexit__(None, None, None)
            self.auth_status = 'retrying_login'
            self.retry_at = time.monotonic() + 30

    async def reconcile_once(self):
        state = await self.client.state()
        self.state = state
        self.state_error = None
        async with self.lock:
            if self.api is None and time.monotonic() >= self.retry_at:
                await self.authenticate()
            available = {r['id'] for r in state['rooms'] if (r['fresh'] or r.get('transitioning')) and
                         (not r['zoneId'] or next((len(z['roomIds']) for z in state['zones'] if z['id'] == r['zoneId']), 0) == 1)}
            desired = {r['id']: r for r in self.settings['rooms'] if r['id'] in available}
            if self.playback_path == 'direct_dlna':
                desired = ({r['id']: r for r in self.settings['rooms']}
                           if len(self.settings['rooms']) == 1 and self.settings['quality'] == 6 else {})
            address_ready = local_address(self.address)
            for id in list(self.receivers):
                if id not in desired or not address_ready:
                    await self.receivers.pop(id).stop()
            self.receiver_errors = {id: error for id, error in self.receiver_errors.items() if id in desired}
            if self.api and self.address:
                for id, room in desired.items():
                    if not address_ready:
                        self.receiver_errors[id] = 'lan_address_not_local'
                        continue
                    if id not in self.receivers:
                        receiver = Receiver(room, self.client, self.api, self.address, self.settings['quality'],
                                            playback_path=self.playback_path)
                        try:
                            await receiver.start()
                        except Exception as error:
                            # Receiver.start cleans up on failure. Never expose
                            # exception text, account data or upstream tracebacks.
                            self.receiver_errors[id] = ('receiver_port_in_use' if isinstance(error, OSError)
                                and error.errno == errno.EADDRINUSE else 'receiver_start_failed')
                            continue
                        self.receivers[id] = receiver
                        self.receiver_errors.pop(id, None)

    async def reconcile(self):
        while not self.closed.is_set():
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state_error = 'control_state_unavailable'
                self.state = {**self.state, 'topologyFresh': False, 'rooms': [
                    {**r, 'fresh': False} for r in self.state['rooms']]}
                async with self.lock:
                    if self.playback_path != 'direct_dlna':
                        await self.stop_receivers()
            try:
                await asyncio.wait_for(self.closed.wait(), timeout=5)
            except TimeoutError:
                pass

    async def run(self):
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, os.environ.get('SETUP_BIND', '0.0.0.0'), 8788).start()
        task = asyncio.create_task(self.reconcile())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.closed.set)
        print('Qobuz setup ready on port 8788; select rooms and sign in', flush=True)
        await self.closed.wait()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await self.stop_receivers()
        if self.api:
            await self.api.__aexit__(None, None, None)
        await runner.cleanup()


def main():
    logging.getLogger('qobuz_proxy').setLevel(logging.CRITICAL)
    token = os.environ.get('API_TOKEN', '')
    if len(token) < 32:
        raise ValueError('API_TOKEN must contain at least 32 characters')
    client = RaumfeldClient(os.environ.get('RAUMFELD_API', 'http://127.0.0.1:8787'), token)
    service = Service(client, token, os.environ.get('DATA_DIR', '/data'), os.environ.get('LAN_ADDRESS'))
    service.connect_diagnostics.install()
    asyncio.run(service.run())


if __name__ == '__main__':
    main()

"""Opt-in isolated supervisor, loopback diagnostic GUI and optional mDNS target."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import uuid

from control import Blocked, DiagnosticControl

ROOT = Path(__file__).resolve().parent


def observer_snapshot():
    request = urllib.request.Request('http://127.0.0.1:8817/snapshot',
        headers={'Authorization': 'Bearer ' + os.environ['CAST_API_TOKEN']})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    except Exception:
        return {'topologyFresh': False, 'rooms': [], 'zones': [], 'renderers': []}


def handler(control, data_dir):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No household names, tokens, URLs or request bodies in logs.

        def send(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization', '').encode(),
                ('Bearer ' + os.environ['CAST_API_TOKEN']).encode())

        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write((ROOT / 'index.html').read_bytes())
                return
            if self.path == '/ui.js':
                self.send_response(200)
                self.send_header('Content-Type', 'text/javascript')
                self.end_headers()
                self.wfile.write((ROOT / 'ui.js').read_bytes())
                return
            if self.path == '/healthz':
                return self.send(200, {'status': 'ok', 'mode': 'diagnostic_only'})
            if not self.authorized():
                return self.send(401, {'error': 'unauthorized'})
            if self.path != '/api/status':
                return self.send(404, {'error': 'not_found'})
            result = control.status(observer_snapshot())
            try:
                result['wire'] = json.loads((data_dir / 'wire-status.json').read_text())
            except Exception:
                result['wire'] = {'mode': 'not_ready'}
            return self.send(200, result)

        def do_POST(self):
            if not self.authorized():
                return self.send(401, {'error': 'unauthorized'})
            # No cookies or CORS; authorization is a non-simple bearer header.
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > 4096:
                    return self.send(400, {'error': 'invalid_body_size'})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError()
                if self.path == '/api/select' and set(body) == {'roomId'} and isinstance(body['roomId'], str):
                    control.select(body['roomId'])
                    return self.send(200, {'selected': True, 'playbackStarted': False})
                if self.path == '/api/group' and set(body) == {'enabled'}:
                    control.group(body['enabled'], observer_snapshot())
                if self.path == '/api/snapshot' and not body:
                    return self.send(200, control.save_snapshot(observer_snapshot()))
                return self.send(400, {'error': 'invalid_request'})
            except Blocked as error:
                return self.send(error.status, {'error': error.code, 'mutationSent': False})
            except (ValueError, TypeError, KeyError):
                return self.send(400, {'error': 'invalid_request'})

    return Handler


def advertise(config, address, port, data_dir):
    from zeroconf import ServiceInfo, Zeroconf
    path = data_dir / 'device-id'
    if not path.exists():
        path.write_text(uuid.uuid4().hex)
    identity = uuid.UUID(path.read_text().strip()).hex
    name = config.get('targetName', 'Raumfeld Cast Experiment')
    # Stable experimental identity, no production UUID/name impersonation.
    info = ServiceInfo('_googlecast._tcp.local.', name + '._googlecast._tcp.local.',
        addresses=[socket.inet_aton(address)], port=port,
        properties={'id': identity, 'fn': name, 'md': 'Experimental diagnostic receiver',
                    've': '05', 'ca': '4', 'st': '0', 'nf': '1', 'rs': ''},
        server='cast-' + identity + '.local.')
    zc = Zeroconf(interfaces=[address])
    zc.register_service(info)
    return zc, info


def main():
    if os.environ.get('CAST_EXPERIMENT') != '1':
        print('cast_experiment_disabled', flush=True)
        return
    os.umask(0o077)
    data = Path(os.environ.get('CAST_DATA_DIR', '/cast-data'))
    if data.resolve().is_relative_to(Path('/data')):
        raise ValueError('isolated_storage_required')
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    # No production API token reuse; token never goes into URLs/HTML/logs.
    token = os.environ.get('CAST_API_TOKEN') or secrets.token_urlsafe(32)
    if len(token) < 32:
        raise ValueError('experimental_token_required')
    os.environ['CAST_API_TOKEN'] = token
    (data / 'gui-token').write_text(token)
    config = json.loads((data / 'config.json').read_text())
    control = DiagnosticControl(config, data)
    address = str(ipaddress.IPv4Address(os.environ.get('CAST_BIND_ADDRESS', '127.0.0.1')))
    if ipaddress.IPv4Address(address).is_unspecified or ipaddress.IPv4Address(address).is_multicast:
        raise ValueError('explicit_ipv4_required')
    port = int(os.environ.get('CAST_PORT', '8809'))
    if not 1024 <= port <= 65535 or port in {8799, 8817, 8787, 8788}:
        raise ValueError('experimental_port_collision')
    if os.environ.get('CAST_ADVERTISE') == '1' and ipaddress.IPv4Address(address).is_loopback:
        raise ValueError('lan_address_required_for_advertisement')
    os.environ['CAST_BIND_ADDRESS'] = address
    children = []
    zc = info = None
    http = ThreadingHTTPServer(('127.0.0.1', 8799), handler(control, data))
    http.timeout = 0.5
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    try:
        (data / 'wire-status.json').unlink(missing_ok=True)
        (data / 'diagnostic-tls.der').unlink(missing_ok=True)
        children.append(subprocess.Popen(['/usr/local/bin/cast-diagnostic']))
        children.append(subprocess.Popen(['node', str(ROOT / 'observer.js')]))
        deadline = time.monotonic() + 30
        while not (data / 'wire-status.json').exists():
            if time.monotonic() >= deadline or any(child.poll() is not None for child in children):
                raise RuntimeError('diagnostic_startup_failed')
            time.sleep(0.1)
        if os.environ.get('CAST_ADVERTISE') == '1':
            zc, info = advertise(config, address, port, data)
        print('cast_diagnostic_gui_ready_media_and_grouping_blocked', flush=True)
        while not stopping.is_set() and all(child.poll() is None for child in children):
            http.handle_request()
    finally:
        if zc:
            zc.unregister_service(info)
            zc.close()
        http.server_close()
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    if not stopping.is_set():
        raise SystemExit(1)


if __name__ == '__main__':
    main()

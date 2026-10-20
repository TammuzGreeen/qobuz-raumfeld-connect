"""Synthetic image smoke: default upstream-first, no accounts or real speakers.

Run only in a disposable container with a private /data tmpfs and discovery off.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib.request import urlopen


assert os.environ.get('PLAYBACK_PATH') == 'upstream_first'
assert os.environ.get('DISCOVERY_MODE') == 'off'
data = Path('/data')
assert not list(data.iterdir()), 'requires empty disposable /data'
original = json.dumps({'quality': 6, 'rooms': [
    {'id': 'synthetic-room', 'name': 'Synthetic test room', 'port': 8790}
]}).encode()
(data / 'config.json').write_bytes(original)
environment = dict(os.environ, LAN_ADDRESS=socket.gethostbyname(socket.gethostname()))
child = subprocess.Popen(
    ['/app/qobuz/.venv/bin/python', 'docker/supervise.py'], env=environment,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    deadline = time.monotonic() + 20
    while True:
        assert child.poll() is None, 'supervisor exited before healthy'
        result = subprocess.run(
            ['/app/qobuz/.venv/bin/python', 'docker/healthcheck.py'],
            env=environment, capture_output=True)
        if result.returncode == 0:
            break
        assert time.monotonic() < deadline, 'healthcheck timed out'
        time.sleep(.2)
    commands = [path.read_bytes().split(b'\0')
                for path in Path('/proc').glob('[0-9]*/cmdline')]
    assert any(b'raumkernel/companion-main.js' in command for command in commands)
    assert any(b'qobuz.upstream_app' in command for command in commands)
    assert not any(b'raumkernel/main.js' in command for command in commands)
    with urlopen('http://127.0.0.1:8788/api/status', timeout=2) as response:
        assert response.status == 200
        json.load(response)
    assert (data / 'config.json').read_bytes() == original
    assert (data / 'upstream-first').is_dir()
finally:
    if child.poll() is None:
        child.send_signal(signal.SIGTERM)
    output = child.communicate(timeout=15)[0]
    if child.returncode:
        print(output)
assert child.returncode == 0
print('Default upstream-first launcher, health, UI, preserved config and shutdown passed.')

"""Non-root packaged startup/shutdown using disposable state, discovery off."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from experiments.dlna.runtime import udn


with tempfile.TemporaryDirectory() as directory:
    config = {'bridge_id': '482e698c-e616-4f87-83c7-8448a2c0622c', 'rooms': [
        {'id': 'synthetic-room-0', 'name': 'Synthetic 0', 'dlna_port': 18930},
        {'id': 'synthetic-room-1', 'name': 'Synthetic 1', 'dlna_port': 18931},
    ]}
    path = Path(directory) / 'config.json'
    original = json.dumps(config).encode()
    path.write_bytes(original)
    environment = dict(os.environ, CONFIG_PATH=str(path), API_PORT='18877',
        API_TOKEN='synthetic-token-only-for-offline-smoke-12345', DISCOVERY_MODE='off',
        LAN_ADDRESS=socket.gethostbyname(socket.gethostname()))
    child = subprocess.Popen([sys.executable, '-m', 'experiments.dlna.main'], env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 20
        while True:
            assert child.poll() is None, 'experimental runtime exited'
            result = subprocess.run([sys.executable, '-m', 'experiments.dlna.healthcheck'],
                env=environment, capture_output=True)
            if result.returncode == 0:
                break
            assert time.monotonic() < deadline, 'runtime health timed out'
            time.sleep(.1)
        for room in config['rooms']:
            base = f"http://{environment['LAN_ADDRESS']}:{room['dlna_port']}"
            with urlopen(base + '/device.xml', timeout=2) as response:
                assert udn(config['bridge_id'], room['id']).encode() in response.read()
            with urlopen(base + '/bridge-status', timeout=2) as response:
                status = json.load(response)
                assert status['transport'] == 'NO_MEDIA_PRESENT'
                assert status['limitation'] is not None
        assert path.read_bytes() == original
        assert list(Path(directory).iterdir()) == [path]
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
        output = child.communicate(timeout=20)[0]
    assert child.returncode == 0, output
    assert 'dlna_bridge_started targets=2' in output and 'dlna_bridge_stopped' in output
    assert 'synthetic-room-' not in output and environment['API_TOKEN'] not in output
    print('Packaged non-root: two persistent targets, missing endpoints, health, unchanged private config, redacted milestones and shutdown passed.')

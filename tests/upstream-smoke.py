"""Offline image/supervisor smoke; deliberately no discovery or credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qobuz.upstream_app

legacy = {'qobuz.receiver', 'qobuz.service', 'qobuz.backend', 'qobuz.dlna_backend', 'qobuz.player', 'qobuz.client'}
assert not legacy.intersection(sys.modules), 'Legacy receiver imported by upstream-first launcher'

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    config = {'rooms': [{'id': 'synthetic-room-'+str(i), 'name': 'Example Qobuz '+str(i), 'port': 8790+i}
                        for i in range(6)], 'quality': 6}
    config['rooms'][0]['name'] = 'Example Qobuz ' + config['rooms'][0]['id'][-6:]
    expected_names = ['Example Qobuz'] + [room['name'] for room in config['rooms'][1:]]
    path = root / 'config.json'
    path.write_text(json.dumps(config))
    before = path.read_bytes()
    env = dict(os.environ, PLAYBACK_PATH='upstream_first', DISCOVERY_MODE='off',
        DATA_DIR=directory, CONFIG_PATH=str(path), API_TOKEN='s' * 32, LAN_ADDRESS='192.0.2.10')
    process = subprocess.Popen([sys.executable, 'docker/supervise.py'], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8788/api/status', timeout=1) as response:
                    assert response.status == 200
                    speakers = json.load(response)['speakers']
                    assert len(speakers) == 6
                    assert [speaker['name'] for speaker in speakers] == expected_names
                result = subprocess.run([sys.executable, 'docker/healthcheck.py'], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if result.returncode == 0:
                    break
            except OSError:
                pass
            assert process.poll() is None and time.monotonic() < deadline, 'Supervisor startup failed'
            time.sleep(.1)
        assert path.read_bytes() == before and not (root / 'credentials.json').exists()
        assert (root / 'upstream-first').is_dir()
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, 'Supervisor did not exit cleanly'
        assert b'Waiting for Qobuz' not in stderr  # No legacy setup/receiver startup.
        assert b'phase=soap_sending' not in stderr
        assert b'phase=library_call' not in stdout
        print('Upstream-first supervisor, stock application UI, health, storage preservation and clean shutdown passed offline.')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

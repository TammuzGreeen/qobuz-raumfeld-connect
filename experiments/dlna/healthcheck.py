import json
import os
from pathlib import Path
from urllib.request import urlopen

config = json.loads(Path(os.environ.get('CONFIG_PATH', '/data/config.json')).read_text())
with urlopen(f"http://127.0.0.1:{os.environ.get('API_PORT', '8877')}/healthz", timeout=2) as response:
    assert response.status == 200
for room in config['rooms']:
    with urlopen(f"http://{os.environ['LAN_ADDRESS']}:{room['dlna_port']}/device.xml", timeout=2) as response:
        assert response.status == 200

import json
import os
from urllib.request import urlopen

for port in (8787, 8788):
    path = '/api/status' if port == 8788 and os.environ.get('PLAYBACK_PATH') == 'upstream_first' else '/healthz'
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
        if response.status != 200:
            raise SystemExit(1)
        json.load(response)

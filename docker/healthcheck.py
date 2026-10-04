import json
from urllib.request import urlopen

for port in (8787, 8788):
    with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
        if response.status != 200:
            raise SystemExit(1)
        json.load(response)

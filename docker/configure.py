"""Create .env for a new installation without overwriting an existing file."""
import ipaddress
import os
from pathlib import Path
import secrets

if Path('.env').exists():
    raise SystemExit('.env already exists. Edit it to keep your current setup token.')
address = str(ipaddress.IPv4Address(input('Docker host LAN IPv4 address: ').strip()))
token = secrets.token_hex(32)
fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as out:
    out.write(f'API_TOKEN={token}\nLAN_ADDRESS={address}\nRAUMFELD_HOST=\n')
print(f'Created .env. After starting Docker, open http://{address}:8788')
print('Use the API_TOKEN value from .env to unlock setup.')

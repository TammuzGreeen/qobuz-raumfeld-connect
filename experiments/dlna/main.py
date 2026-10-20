"""Experimental process pair. No Qobuz cloud receiver or audio relay starts here."""
import asyncio
import contextlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import secrets
import sys
import uuid

from qobuz.upstream_backend import CompanionClient, RendererRequestError
from .runtime import Room, DirectBackend, udn
from .services import device_class
from .server import RendererServer


class DlnaCompanion(CompanionClient):
    async def lookup(self, room_id):
        result = await self.request('GET', '/v1/endpoints')
        row = next((r for r in result.get('rooms', []) if r.get('roomId') == room_id), None)
        if row and row.get('unavailable'):
            raise RendererRequestError(409, {'error': row['unavailable']})
        return await super().lookup(room_id)


def load_config(path):
    value = json.loads(Path(path).read_text())
    value['bridge_id'] = str(uuid.UUID(value['bridge_id']))  # Persistent, never generated on boot.
    rooms = value['rooms']
    if not isinstance(rooms, list) or not rooms or len(rooms) > 32:
        raise ValueError('invalid_rooms')
    ids, ports = set(), set()
    companion_port = int(os.environ.get('API_PORT', '8877'))
    if companion_port in {8787, 8788, 1900} or not 1024 <= companion_port <= 65535:
        raise ValueError('invalid_companion_port')
    for room in rooms:
        if (not isinstance(room.get('id'), str) or not 1 <= len(room['id']) <= 256
                or not isinstance(room.get('name'), str) or not 1 <= len(room['name']) <= 128
                or type(room.get('dlna_port')) is not int
                or not 8900 <= room['dlna_port'] <= 65000
                or room['dlna_port'] == companion_port or room['id'] in ids or room['dlna_port'] in ports):
            raise ValueError('invalid_room_configuration')
        ids.add(room['id']); ports.add(room['dlna_port'])
    address = ipaddress.IPv4Address(os.environ['LAN_ADDRESS'])
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError('invalid_lan_address')
    token = os.environ['API_TOKEN']
    if len(token) < 32:
        raise ValueError('invalid_api_token')
    return value, str(address), companion_port


async def main():
    os.umask(0o077)
    # Upstream libraries can log media URLs or SOAP bodies. Do not emit them in
    # the experimental runtime; retain only our fixed categorical milestones.
    logging.disable(logging.CRITICAL)
    config, address, port = load_config(os.environ.get('CONFIG_PATH', '/data/config.json'))
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    env = dict(os.environ, API_PORT=str(port), EXCLUDED_RENDERER_UDNS=','.join(
        udn(config['bridge_id'], room['id']) for room in config['rooms']))
    node = await asyncio.create_subprocess_exec('node', 'raumkernel/companion-main.js', env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    rooms, servers = [], []
    async def poll(room):
        while not stopped.is_set():
            try:
                async with room.lock:
                    await room.refresh()
                    room.changed()
            except Exception:
                room.limitation = 'observation_failed'
                room.changed()
            await asyncio.sleep(2)
    tasks = []
    try:
        for _ in range(50):
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(f'http://127.0.0.1:{port}/healthz') as response:
                        if response.status == 200:
                            break
            except Exception:
                await asyncio.sleep(.1)
        else:
            raise RuntimeError('companion_start_failed')
        for item in config['rooms']:
            companion = DlnaCompanion(os.environ['API_TOKEN'], base=f'http://127.0.0.1:{port}')
            backend = DirectBackend(companion, item['id'], '', poll_seconds=2)
            room = Room(item, backend)
            backend.on_external_playback(lambda room=room: retired(room))
            await backend.connect()
            rooms.append(room)
            server = RendererServer(device_class(room, config['bridge_id']),
                source=(address, 0), http_port=item['dlna_port'], boot_id=secrets.randbelow(2147483646) + 1)
            servers.append(server)
            await server.start()
        print(f'dlna_bridge_started targets={len(servers)}', flush=True)
        tasks = [asyncio.create_task(poll(room)) for room in rooms]
        wait_stop = asyncio.create_task(stopped.wait())
        wait_node = asyncio.create_task(node.wait())
        done, _ = await asyncio.wait([wait_stop, wait_node], return_when=asyncio.FIRST_COMPLETED)
        if wait_node in done:
            raise RuntimeError('companion_exited')
        wait_stop.cancel(); wait_node.cancel()
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for server in reversed(servers):
            await server.stop()
        for room in rooms:
            await room.backend.disconnect()  # Closes HTTP; never sends Stop.
        if node.returncode is None:
            node.terminate()
            await node.wait()
        print('dlna_bridge_stopped', flush=True)


async def retired(room):
    room.retire()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception:
        print('dlna_bridge_failed', file=sys.stderr)
        sys.exit(1)

"""Real UDP M-SEARCH/alive/byebye smoke, only on an isolated Docker bridge.

No companion, real device discovery, Qobuz or speaker transport starts here.
"""
import asyncio
import socket
from unittest.mock import MagicMock

from async_upnp_client.server import SsdpAdvertisementAnnouncer
from experiments.dlna.runtime import Room
from experiments.dlna.services import device_class
from experiments.dlna.server import RendererServer


async def collect(sock, seconds=1.5):
    packets = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        try:
            packet = await asyncio.wait_for(loop.sock_recv(sock, 65535), deadline - loop.time())
            packets.append(packet.decode())
        except TimeoutError:
            break
    return packets


async def main():
    address = socket.gethostbyname(socket.gethostname())
    assert not address.startswith('127.'), 'run in a private Docker bridge, not host networking'
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('', 1900))
    listener.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
        socket.inet_aton('239.255.255.250') + socket.inet_aton(address))
    listener.setblocking(False)
    search = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    search.bind((address, 0))
    search.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
    search.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    search.setblocking(False)
    servers = []
    try:
        for index in range(2):
            room = Room({'id': f'synthetic-room-{index}', 'name': f'Synthetic {index}'}, MagicMock())
            server = RendererServer(device_class(room, '482e698c-e616-4f87-83c7-8448a2c0622c'),
                source=(address, 0), http_port=18920 + index)
            servers.append(server)
            await server.start()
            # Exercise stack announcements for all advertised device/service types
            # immediately instead of waiting for the default 30-second rotation.
            for _ in range(6):
                server._advertisement_announcer._cancel_announce.cancel()
                server._advertisement_announcer._announce_next()
        alive = await collect(listener, .3)
        assert all(any('ssdp:alive' in p and server._device.udn in p for p in alive) for server in servers)
        await asyncio.get_running_loop().sock_sendto(search,
            b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
            b'MX: 1\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n',
            ('239.255.255.250', 1900))
        packets = await collect(search)
        for server in servers:
            matches = [p for p in packets if server._device.udn in p]
            assert len(matches) == 1, 'expected one response for each persistent logical target'
            assert server.base_uri + '/device.xml' in matches[0]
        for server in reversed(servers):
            await server.stop()
        byebye = await collect(listener, .3)
        assert all(any('ssdp:byebye' in p and server._device.udn in p for p in byebye) for server in servers)
        servers.clear()
        print('Real isolated UDP: two logical M-SEARCH targets, alive and byebye passed.')
    finally:
        for server in reversed(servers):
            await server.stop()
        listener.close(); search.close()


if __name__ == '__main__':
    asyncio.run(main())

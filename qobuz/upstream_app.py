"""One-room launcher for upstream QobuzProxy, Speaker and protocol components.

Extension points are the backend factory, endpoint lifecycle, interface choice
and carrying upstream player generations across awaits. No handshake override.
"""
import asyncio
import ipaddress
import json
import logging
import os
from pathlib import Path
import uuid

from qobuz_proxy.app import QobuzProxy
from qobuz_proxy.config import Config, QobuzConfig, ServerConfig, SpeakerConfig
from qobuz_proxy.speaker import Speaker
from qobuz_proxy.speaker import VolumeCommandHandler as UpstreamVolumeCommandHandler
from qobuz_proxy.connect.discovery import DiscoveryService
from qobuz_proxy.connect.ws_manager import WsManager
from qobuz_proxy.backends.factory import BackendFactory
from qobuz_proxy.auth import credentials as token_storage
import qobuz_proxy.app as app_module
import qobuz_proxy.speaker as speaker_module

from .upstream_backend import CompanionClient, DeferredRoomBackend, operation


def load_runtime(data_dir, lan_address):
    data_dir = Path(data_dir)
    address = ipaddress.IPv4Address(lan_address)
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError('invalid_lan_address')
    settings = json.loads((data_dir / 'config.json').read_text())
    rooms = settings.get('rooms')
    if not isinstance(rooms, list) or len(rooms) != 1 or settings.get('quality') != 6:
        raise ValueError('upstream_first_requires_one_room_cd')
    room = rooms[0]
    if (not isinstance(room, dict) or not isinstance(room.get('id'), str) or not room['id'] or
            not isinstance(room.get('name'), str) or not room['name'] or
            type(room.get('port')) is not int or not 1024 <= room['port'] <= 65000):
        raise ValueError('invalid_room_configuration')
    runtime_dir = data_dir / 'upstream-first'
    # Prefer a token refreshed by this candidate; never replace original files.
    credential_path = runtime_dir / 'credentials.json'
    if not credential_path.exists():
        credential_path = data_dir / 'credentials.json'
    credentials = json.loads(credential_path.read_text()) if credential_path.exists() else {}
    config = Config(
        qobuz=QobuzConfig(user_id=credentials.get('user_id', ''),
            auth_token=credentials.get('user_auth_token', ''), max_quality=6),
        server=ServerConfig(http_port=8788, bind_address='127.0.0.1'),
        speakers=[SpeakerConfig(name=room['name'],
            uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, 'raumfeld:' + room['id'])),
            backend_type='dlna', max_quality=6, http_port=room['port'],
            proxy_port=room['port'] + 100, bind_address=str(address),
            dlna_ip='raumfeld-room', dlna_description_url='raumfeld-room:' + room['id'])],
    )
    return room, config, runtime_dir


def install_runtime(room, token, companion_base='http://127.0.0.1:8787'):
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('invalid_companion_token')
    active = {}

    class RoomBackendFactory(BackendFactory):
        @classmethod
        async def create_from_config(cls, config):
            if config.backend.dlna.description_url != 'raumfeld-room:' + room['id']:
                raise ValueError('only_configured_room_supported')
            backend = DeferredRoomBackend(CompanionClient(token, companion_base), room['id'], room['name'])
            active['backend'] = backend
            await backend.connect()
            return backend

    class InterfaceDiscovery(DiscoveryService):
        def _get_local_ip(self):
            return self.config.server.bind_address

    class GenerationWsManager(WsManager):
        def register_handler(self, msg_type, handler):
            def dispatch(mt, message):
                backend = active['backend']
                context = operation.set(backend.capture_operation())
                try:
                    # Stock handler creates its own task. ContextVars preserve
                    # the generation captured here across that task's awaits.
                    return handler(mt, message)
                finally:
                    operation.reset(context)
            return super().register_handler(msg_type, dispatch)

    class VolumeFeedbackHandler(UpstreamVolumeCommandHandler):
        async def _handle_volume_changed(self, message):
            # A cloud broadcast is feedback, not a second setter command. Real
            # readback reconciles the cache; explicit SET_VOLUME stays upstream.
            return

    class LifecycleSpeaker(Speaker):
        async def start(self):
            self._room_stopped = False
            started = await super().start()
            if started:
                self._backend.bind_player(self._player)
            return started

        async def stop(self):
            if getattr(self, '_room_stopped', False):
                return
            self._room_stopped = True
            if isinstance(self._backend, DeferredRoomBackend):
                # Stock player.stop during teardown must not stop another source.
                self._backend.closed = True
            await super().stop()

    # Scoped to this separate launcher process. Stock Speaker.start, discovery
    # connect handler, websocket setup and all protocol handlers remain upstream.
    speaker_module.BackendFactory = RoomBackendFactory
    speaker_module.DiscoveryService = InterfaceDiscovery
    speaker_module.WsManager = GenerationWsManager
    speaker_module.VolumeCommandHandler = VolumeFeedbackHandler
    app_module.Speaker = LifecycleSpeaker


class OneRoomApplication(QobuzProxy):
    # Configured room identity belongs to the compatibility loader. Upstream UI
    # edit/add/remove must not invoke restart-on-edit or silently change it.
    async def _on_add_speaker(self, body):
        raise ValueError('room_configuration_is_read_only')

    async def _on_edit_speaker(self, speaker_id, body):
        raise ValueError('room_configuration_is_read_only')

    async def _on_remove_speaker(self, speaker_id):
        raise ValueError('room_configuration_is_read_only')


async def main():
    os.umask(0o077)
    room, config, runtime_dir = load_runtime(os.environ.get('DATA_DIR', '/data'), os.environ['LAN_ADDRESS'])
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_storage.CACHE_DIR = runtime_dir
    token_storage.CACHE_FILE = runtime_dir / 'credentials.json'
    install_runtime(room, os.environ['API_TOKEN'])
    logging.basicConfig(level=logging.INFO)
    # Web UI is loopback-only; per-room discovery/audio use the preserved LAN
    # interface. Upstream owns auth, Android connection/session, queue and reports.
    await OneRoomApplication(config).run()


if __name__ == '__main__':
    asyncio.run(main())

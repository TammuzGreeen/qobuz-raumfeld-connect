"""Per-room launcher for upstream QobuzProxy, Speaker and protocol components.

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
from .upstream_diagnostics import StateDiagnostics


def load_runtime(data_dir, lan_address):
    data_dir = Path(data_dir)
    address = ipaddress.IPv4Address(lan_address)
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError('invalid_lan_address')
    settings = json.loads((data_dir / 'config.json').read_text())
    rooms = settings.get('rooms')
    if not isinstance(rooms, list) or not rooms or settings.get('quality') != 6:
        raise ValueError('upstream_first_requires_rooms_cd')
    ids, ports = set(), {8787, 8788}
    for room in rooms:
        if (not isinstance(room, dict) or not isinstance(room.get('id'), str) or not room['id'] or
             not isinstance(room.get('name'), str) or not room['name'] or
             type(room.get('port')) is not int or not 1024 <= room['port'] <= 65000):
            raise ValueError('invalid_room_configuration')
        audio_port = room.get('proxy_port', room['port'] + 100)
        if (room['id'] in ids or type(audio_port) is not int or not 1024 <= audio_port <= 65535
                or room['port'] == audio_port or room['port'] in ports or audio_port in ports):
            raise ValueError('duplicate_room_or_port_collision')
        ids.add(room['id'])
        ports.update((room['port'], audio_port))
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
             proxy_port=room.get('proxy_port', room['port'] + 100), bind_address=str(address),
             dlna_ip='raumfeld-room', dlna_description_url='raumfeld-room:' + room['id']) for room in rooms],
    )
    return rooms, config, runtime_dir


def install_runtime(rooms, token, companion_base='http://127.0.0.1:8787', diagnostics=False):
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('invalid_companion_token')
    rooms = [rooms] if isinstance(rooms, dict) else rooms
    configured = {'raumfeld-room:' + room['id']: (room, i) for i, room in enumerate(rooms, 1)}
    active = {}  # Keyed by exact component room configuration, never a shared current backend.

    class RoomBackendFactory(BackendFactory):
        @classmethod
        async def create_from_config(cls, config):
            key = config.backend.dlna.description_url
            if key not in configured:
                raise ValueError('only_configured_rooms_supported')
            room, ordinal = configured[key]
            backend = DeferredRoomBackend(CompanionClient(token, companion_base), room['id'], room['name'])
            backend.diagnostic_alias = 'room_' + str(ordinal).zfill(2)
            backend.diagnostics = StateDiagnostics(backend) if diagnostics else None
            active[key] = backend
            await backend.connect()
            return backend

    class InterfaceDiscovery(DiscoveryService):
        def _get_local_ip(self):
            return self.config.server.bind_address

    class GenerationWsManager(WsManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._room_backend = active[self.config.backend.dlna.description_url]
            if self._room_backend.diagnostics:
                self._room_backend.diagnostics.bind_ws(self)

        def register_handler(self, msg_type, handler):
            owner = getattr(handler, '__self__', None)
            if self._room_backend.diagnostics and hasattr(owner, '_note_active') and not getattr(owner, '_room_traced', False):
                owner._room_traced = True
                note_active = owner._note_active
                observer = self._room_backend.diagnostics
                def activation(active):
                    previous = owner._active
                    result = note_active(active)
                    observer.emit('upstream_activation', requestedActive=bool(active), changed=previous != active)
                    return result
                owner._note_active = activation
            def dispatch(mt, message):
                backend = self._room_backend
                context = operation.set(backend.capture_operation())
                try:
                    # Stock handler creates its own task. ContextVars preserve
                    # the generation captured here across that task's awaits.
                    observer = backend.diagnostics
                    return observer.dispatch(mt, message, handler) if observer else handler(mt, message)
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
                # Stock command-handler generations distinguish actual source
                # authority changes from ordinary queued SET_STATE commands.
                self._backend.bind_authority(lambda: (
                    self._playback_handler._generation, self._playback_handler._active
                ) if self._playback_handler else None)
                if self._backend.diagnostics:
                    self._backend.diagnostics.bind_player(self._player)
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


class RoomApplication(QobuzProxy):
    # Configured room identity belongs to the compatibility loader. Upstream UI
    # edit/add/remove must not invoke restart-on-edit or silently change it.
    async def _on_add_speaker(self, body):
        raise ValueError('room_configuration_is_read_only')

    async def _on_edit_speaker(self, speaker_id, body):
        raise ValueError('room_configuration_is_read_only')

    async def _on_remove_speaker(self, speaker_id):
        raise ValueError('room_configuration_is_read_only')


OneRoomApplication = RoomApplication  # Historical import compatibility; no single-room restriction.


async def main():
    os.umask(0o077)
    rooms, config, runtime_dir = load_runtime(os.environ.get('DATA_DIR', '/data'), os.environ['LAN_ADDRESS'])
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_storage.CACHE_DIR = runtime_dir
    token_storage.CACHE_FILE = runtime_dir / 'credentials.json'
    install_runtime(rooms, os.environ['API_TOKEN'], diagnostics=Path('/app/.upstream-state-diagnostics').is_file())
    logging.basicConfig(level=logging.INFO)
    # Web UI is loopback-only; per-room discovery/audio use the preserved LAN
    # interface. Upstream owns auth, Android connection/session, queue and reports.
    await RoomApplication(config).run()


if __name__ == '__main__':
    asyncio.run(main())

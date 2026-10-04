"""Compose upstream protocol components around our guarded backend."""
import asyncio
import contextlib
import hashlib
import time
import uuid
from aiohttp import web
from qobuz_proxy.config import Config, DeviceConfig, QobuzConfig, ServerConfig
from qobuz_proxy.connect import DiscoveryService, WsManager
from qobuz_proxy.playback import QobuzPlayer, QobuzQueue, MetadataService, StateReporter
from qobuz_proxy.playback.play_reporter import PlayReporter
from qobuz_proxy.playback.state_reporter import wire_playing_state
from qobuz_proxy.playback import QueueHandler, PlaybackCommandHandler, VolumeCommandHandler
from .backend import AudioRelay, RaumfeldBackend


class LanDiscovery(DiscoveryService):
    """Pin upstream advertisement to the configured speaker-facing interface."""
    def _get_local_ip(self):
        return self.config.server.bind_address


class Receiver:
    def __init__(self, room, client, api, address, quality):
        self.room, self.client, self.api = room, client, api
        self.quality = quality
        self.config = Config(
            device=DeviceConfig(name=room['name'], uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, 'raumfeld:' + room['id']))),
            qobuz=QobuzConfig(max_quality=quality),
            server=ServerConfig(http_port=room['port'], bind_address=address))
        self.app = web.Application(client_max_size=16384)
        self.relay = AudioRelay(self.app, address, room['port'])
        self.discovery = LanDiscovery(self.config, api.app_id, self.connected,
                                      quality_getter=lambda: self.quality, web_app=self.app)
        self.runner = None
        self.backend = self.player = self.ws = self.reporter = self.handler = None
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.seen = {}
        self.closed = False

    async def start(self):
        try:
            # Upstream registers routes and mDNS; our runner owns the shared app.
            await self.discovery.start()
            self.runner = web.AppRunner(self.app, access_log=None)
            await self.runner.setup()
            await web.TCPSite(self.runner, self.config.server.bind_address, self.room['port']).start()
        except Exception:
            await self.stop()
            raise

    def connected(self, tokens):
        if self.closed:
            return
        task = asyncio.create_task(self.select(tokens))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def select(self, tokens):
        async with self.lock:
            if self.closed:
                return
            # Retried handshakes cannot reclaim a revoked session. A different
            # session ID from a fresh app selection is required.
            key = hashlib.sha256(tokens.session_id.encode()).hexdigest()
            self.seen = {k: exp for k, exp in self.seen.items() if exp > time.monotonic()}
            if key in self.seen or len(self.seen) >= 4096:
                return
            self.seen[key] = time.monotonic() + 86400
            await self.close_session()
            try:
                backend = RaumfeldBackend(self.client, self.room['id'], self.room['name'], self.relay)
                self.backend = backend
                await backend.select(key)
                queue = QobuzQueue()
                metadata = MetadataService(api_client=self.api, max_quality=self.quality)
                player = QobuzPlayer(queue=queue, metadata_service=metadata, backend=backend,
                                     play_reporter=PlayReporter(self.api))
                ws = WsManager(config=self.config)
                self.player, self.ws = player, ws
                async def external():
                    self.discovery.clear_session()
                    ws.release_external_playback()
                    await player.release_external_playback()
                backend.on_external = external
                player.set_playback_permission_check(backend.can_play)
                ws.set_tokens(tokens, activate=True)
                ws.set_token_refresher(self.api.get_ws_token)
                ws.set_max_audio_quality(self.quality)
                queue_handler = QueueHandler(queue)
                async def quality_changed(requested):
                    # Do not exceed the user-configured quality ceiling.
                    effective = min(requested, self.quality) if requested in (5, 6, 7, 27) else self.quality
                    metadata.set_max_quality(effective)
                    await player.reload_current_track()
                handler = PlaybackCommandHandler(player, on_quality_change=quality_changed,
                                                 speaker_name=self.room['name'])
                self.handler = handler
                ws.on_connected(handler.note_connected)
                ws.on_disconnected(handler.note_disconnected)
                volume = VolumeCommandHandler(player)
                player.set_next_track_callbacks(handler.get_next_track_info, handler.clear_next_track_info)
                player.set_next_track_request_callback(ws.request_next_track)
                handler.set_on_next_track_changed(player.on_next_track_info_changed)
                for component in (queue_handler, volume):
                    for msg_type in component.get_message_types():
                        ws.register_handler(msg_type, lambda mt, msg, h=component: asyncio.create_task(h.handle_message(mt, msg)))
                for msg_type in handler.get_message_types():
                    ws.register_handler(msg_type, handler.dispatch_message)
                async def report(state):
                    await ws.send_state_update(playing_state=int(wire_playing_state(state.playing_state)),
                        buffer_state=int(state.buffer_state), position_ms=state.position_value_ms,
                        position_timestamp_ms=state.position_timestamp_ms, duration_ms=state.duration_ms,
                        queue_item_id=state.current_queue_item_id, queue_version_major=state.queue_version_major,
                        queue_version_minor=state.queue_version_minor)
                self.reporter = StateReporter(player=player, queue=queue, send_callback=report)
                player.set_state_reporter(self.reporter)
                player.set_volume_report_callback(ws.send_volume_changed)
                player.set_file_quality_report_callback(ws.send_file_audio_quality_changed)
                self.discovery.set_session(tokens)
                await player.start()
                await ws.start()
                await self.reporter.start()
            except Exception:
                await self.close_session()
                self.discovery.clear_session()

    async def close_session(self):
        # Release permission first so old callbacks can never use a new lease.
        if self.backend:
            await self.backend.release()
        if self.handler:
            self.handler.note_disconnected()
        for component in (self.reporter, self.ws, self.player):
            if component:
                with contextlib.suppress(Exception):
                    await component.stop()
        if self.backend:
            await self.backend.disconnect()
        self.backend = self.player = self.ws = self.reporter = self.handler = None

    async def stop(self):
        self.closed = True
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.close_session()
        with contextlib.suppress(Exception):
            await self.discovery.stop()
        if self.runner:
            await self.runner.cleanup()

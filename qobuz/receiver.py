"""Compose upstream protocol components around our guarded backend."""
import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from aiohttp import web
from qobuz_proxy.config import Config, DeviceConfig, QobuzConfig, ServerConfig
from qobuz_proxy.connect import DiscoveryService, WsManager
from qobuz_proxy.playback import QobuzQueue, StateReporter
from qobuz_proxy.playback.play_reporter import PlayReporter
from qobuz_proxy.playback.state_reporter import wire_playing_state
from qobuz_proxy.playback import QueueHandler, PlaybackCommandHandler, VolumeCommandHandler
from .backend import AudioRelay, RaumfeldBackend
from .player import RaumfeldPlayer
from .diagnostics import PlaybackTimeline, ObservedMetadata


class LanDiscovery(DiscoveryService):
    """Pin upstream advertisement to the configured speaker-facing interface."""
    def _get_local_ip(self):
        return self.config.server.bind_address

    async def _handle_connect(self, request):
        # Upstream acknowledges and stores tokens before our asynchronous
        # callback validates selection. Await admission instead, and let the
        # receiver install tokens only after successful composition.
        logger = logging.getLogger(DiscoveryService.__module__)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                return web.json_response({'error': 'invalid_connect_request'}, status=400)
            tokens = self._parse_connect_request(data)
            if not tokens.is_valid() or not isinstance(tokens.session_id, str) or not tokens.session_id:
                logger.warning('Invalid tokens in connect request')
                return web.json_response({'error': 'invalid_connect_request'}, status=400)
            if not self.on_connect:
                return web.json_response({'error': 'selection_unavailable'}, status=503)
            pending = self.on_connect(tokens)
            if pending is None or not await pending:
                logger.info('Connection selection rejected')
                return web.json_response({'error': 'selection_rejected'}, status=409)
            logger.info('Received connection from app')
            return web.json_response({})
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            return web.json_response({'error': 'invalid_connect_request'}, status=400)
        except Exception:
            logger.warning('Error handling connect request')
            return web.json_response({'error': 'connect_request_failed'}, status=503)


class Receiver:
    def __init__(self, room, client, api, address, quality):
        self.room, self.client, self.api = room, client, api
        self.quality = quality
        self.config = Config(
            device=DeviceConfig(name=room['name'], uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, 'raumfeld:' + room['id']))),
            qobuz=QobuzConfig(max_quality=quality),
            server=ServerConfig(http_port=room['port'], bind_address=address))
        self.app = web.Application(client_max_size=16384)
        self.timeline = PlaybackTimeline()
        self.relay = AudioRelay(self.app, address, room['port'], timeline=self.timeline)
        self.discovery = LanDiscovery(self.config, api.app_id, self.connected,
                                      quality_getter=lambda: self.quality, web_app=self.app)
        self.runner = None
        self.backend = self.player = self.ws = self.reporter = self.handler = None
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.seen = {}
        self.closed = False
        self.selection_stage = 'waiting_for_app'
        self.selection_error = None
        self.message_counts = {}
        self.protocol_error = None

    def diagnostics(self):
        token = self.ws._ws_token if self.ws else None
        return {'stage': self.selection_stage, 'error': self.selection_error,
                'sessionPresent': bool(self.discovery.get_received_tokens()),
                'cloudConnected': bool(self.ws and self.ws.is_connected),
                'cloudActive': bool(self.ws and self.ws.is_renderer_active),
                'cloudTokenValid': bool(token and token.is_valid()),
                'backendSelected': bool(self.backend and self.backend.token),
                'playbackStarted': bool(self.backend and self.backend.started),
                'messageCounts': dict(self.message_counts),
                'protocolError': dict(self.protocol_error) if self.protocol_error else None,
                'lastPlaybackCommand': dict(self.backend.last_playback_command)
                    if self.backend and self.backend.last_playback_command else None,
                'lastControlError': self.backend.last_control_error if self.backend else None,
                'lastVolumeResult': dict(self.backend.last_volume_result)
                    if self.backend and self.backend.last_volume_result else None,
                'lastReleaseReason': self.backend.last_release_reason if self.backend else None,
                'playbackTimeline': self.timeline.snapshot()}

    def dispatch(self, handler, msg_type, message):
        self.message_counts[str(msg_type)] = self.message_counts.get(str(msg_type), 0) + 1
        if msg_type == 41:
            self.timeline.note('playback_event', messageType=msg_type)
        return handler(msg_type, message)

    def note_protocol_error(self, msg_type, message):
        # This handler is part of upstream Speaker wiring but was missing from
        # our composition. Preserve only a numeric code, never its text payload.
        code = int(message.error.code) if message.HasField('error') else None
        self.protocol_error = {'code': code}

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
        return task

    async def select(self, tokens):
        async with self.lock:
            if self.closed:
                return False
            # Retried handshakes cannot reclaim a revoked session. A different
            # session ID from a fresh app selection is required.
            key = hashlib.sha256(tokens.session_id.encode()).hexdigest()
            self.seen = {k: exp for k, exp in self.seen.items() if exp > time.monotonic()}
            if key in self.seen or len(self.seen) >= 4096:
                self.selection_error = 'selection_replayed' if key in self.seen else 'selection_limit'
                # Upstream discovery installs tokens before invoking our
                # callback. A rejected retry must not re-advertise a released
                # session as current. Preserve an existing valid selection.
                if not self.backend or not self.backend.token:
                    self.discovery.clear_session()
                return False
            self.seen[key] = time.monotonic() + 86400
            await self.close_session()
            self.selection_stage = 'selecting_backend'
            self.selection_error = None
            self.message_counts = {}
            self.protocol_error = None
            try:
                backend = RaumfeldBackend(self.client, self.room['id'], self.room['name'], self.relay)
                self.backend = backend
                await backend.select(key)
                self.selection_stage = 'wiring_player'
                queue = QobuzQueue()
                metadata = ObservedMetadata(api_client=self.api, max_quality=self.quality, timeline=self.timeline)
                player = RaumfeldPlayer(queue=queue, metadata_service=metadata, backend=backend,
                                     play_reporter=PlayReporter(self.api))
                ws = WsManager(config=self.config)
                self.player, self.ws = player, ws
                async def external():
                    self.selection_stage = 'ownership_released'
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
                        ws.register_handler(msg_type, lambda mt, msg, h=component: self.dispatch(
                            lambda kind, payload: asyncio.create_task(h.handle_message(kind, payload)), mt, msg))
                for msg_type in handler.get_message_types():
                    ws.register_handler(msg_type, lambda mt, msg: self.dispatch(handler.dispatch_message, mt, msg))
                ws.register_handler(1, lambda mt, msg: self.dispatch(self.note_protocol_error, mt, msg))
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
                self.selection_stage = 'starting_player'
                await player.start()
                self.selection_stage = 'starting_cloud'
                await ws.start()
                await self.reporter.start()
                self.selection_stage = 'session_started'
                return True
            except Exception as error:
                known = {'room_not_enabled', 'room_not_found', 'grouped_room_not_supported',
                         'state_unavailable', 'selection_already_used', 'selection_limit',
                         'invalid_selection', 'unsupported_api', 'node_unavailable'}
                self.selection_error = str(error) if str(error) in known else 'session_start_failed'
                await self.close_session()
                self.discovery.clear_session()
                return False

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

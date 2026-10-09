"""Deferred endpoints around pinned upstream DLNA, not a second receiver.

The only playback authority is upstream's explicit selection, external-playback
release and player command generations. Node never grants transport ownership.
"""
import asyncio
import contextlib
from contextvars import ContextVar
from functools import wraps
from urllib.parse import urlsplit, urljoin
import xml.etree.ElementTree as ET

import aiohttp
from qobuz_proxy.backends.dlna.backend import DLNABackend
from qobuz_proxy.backends.dlna.client import DLNAClient, SoapResult
from qobuz_proxy.backends.types import PlaybackState

from .endpoint_catalog import Endpoint

operation = ContextVar('upstream_room_operation', default=None)


def source_operation(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        token = None
        if operation.get() is None:
            token = operation.set(self.capture_operation())
        try:
            self.check_operation()
            result = await method(self, *args, **kwargs)
            self.check_operation()
            return result
        finally:
            if token is not None:
                operation.reset(token)
    return wrapped


class EndpointUnavailable(RuntimeError):
    pass


class CompanionClient:
    def __init__(self, token, base='http://127.0.0.1:8787'):
        parsed = urlsplit(base)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
            raise ValueError('companion_must_be_loopback')
        self.base, self.token, self.session = base, token, None

    async def request(self, method, path, body=None):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        async with self.session.request(method, self.base + path, json=body,
                headers={'Authorization': 'Bearer ' + self.token}, allow_redirects=False) as response:
            if response.status != 200:
                raise EndpointUnavailable('companion_unavailable')
            return await response.json()

    async def lookup(self, room_id):
        result = await self.request('GET', '/v1/endpoints')
        rows = result.get('rooms', [])
        matches = [r for r in rows if r.get('roomId') == room_id]
        if len(matches) != 1 or matches[0].get('unavailable'):
            raise EndpointUnavailable('room_unavailable')
        row = matches[0]
        if type(row.get('spotifyVersion')) is not int:
            raise EndpointUnavailable('invalid_catalog')
        ids = row.get('rendererIds')
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids):
            raise EndpointUnavailable('invalid_catalog')
        if row.get('endpoint') is not None:
            Endpoint.parse(room_id, row['endpoint'])
        return row

    async def create_for_play(self, room_id):
        return await self.request('POST', '/v1/zone-for-play', {'roomId': room_id})

    async def close(self):
        if self.session:
            await self.session.close()


class RoomDLNAClient(DLNAClient):
    def __init__(self, backend, endpoint, epoch):
        parsed = urlsplit(endpoint.description_url)
        super().__init__(parsed.hostname, parsed.port or 80, endpoint.description_url)
        self.backend, self.endpoint, self.epoch = backend, endpoint, epoch
        self.retired = False

    async def _fetch_device_description(self):
        async with self._session.get(self.description_url, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=5)) as response:
            if response.status != 200:
                raise EndpointUnavailable('renderer_unavailable')
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(16384):
                size += len(chunk)
                if size > 262144:
                    raise EndpointUnavailable('description_too_large')
                chunks.append(chunk)
        body = b''.join(chunks)
        root = ET.fromstring(body)
        ns = {'d': 'urn:schemas-upnp-org:device-1-0'}
        device = root.find('d:device', ns)
        if (device is None or device.findtext('d:UDN', '', ns) != self.endpoint.renderer_id or
                device.findtext('d:deviceType', '', ns) != 'urn:schemas-upnp-org:device:MediaRenderer:1'):
            raise EndpointUnavailable('renderer_identity_mismatch')
        source = urlsplit(self.description_url)
        for service in device.findall('d:serviceList/d:service', ns):
            for tag in ('controlURL', 'SCPDURL'):
                target = urlsplit(urljoin(self.description_url, service.findtext('d:' + tag, '', ns)))
                if target.scheme != 'http' or target.netloc != source.netloc or target.query or target.fragment:
                    raise EndpointUnavailable('invalid_service_origin')
        self.volume_scpd = next((urljoin(self.description_url, s.findtext('d:SCPDURL', '', ns))
            for s in device.findall('d:serviceList/d:service', ns)
            if s.findtext('d:serviceType', '', ns) == 'urn:schemas-upnp-org:service:RenderingControl:1'), None)
        return self._parse_device_description(body.decode('utf8'), 'http://' + source.netloc)

    async def _soap_action_detailed(self, url, service, action, args, max_retries=None):
        mutating = not action.startswith('Get')
        if mutating:
            if action not in {'SetAVTransportURI', 'Play', 'Pause', 'Stop', 'Seek', 'SetVolume'}:
                raise EndpointUnavailable('unsupported_mutation')
            await self.backend.before_write(self, action)
        result = await super()._soap_action_detailed(url, service, action, args, max_retries=1)
        if self.retired or self.backend._client is not self or self.epoch != self.backend.epoch:
            if mutating:
                raise EndpointUnavailable('retired_endpoint')
            return SoapResult(success=False)
        if mutating:
            self.backend.check_operation()
            if not result.success:
                if action == 'Seek' and result.error_code == 710:
                    # Definite unsupported-mode rejection is not an uncertain
                    # transport/source mutation. Preserve upstream's false Seek
                    # result; never retry or substitute a transport command.
                    return result
                await self.backend.release_source()
                raise EndpointUnavailable('mutation_failed_or_uncertain')
        return result

    async def set_volume(self, volume):
        # Upstream's delayed debounce could escape the source/command generation.
        async with self._volume_lock:
            await self.verify_volume_action()
            return await self._do_set_volume(volume)

    async def verify_volume_action(self):
        if getattr(self, 'volume_verified', False):
            return
        if not self.volume_scpd:
            raise EndpointUnavailable('volume_action_not_declared')
        async with self._session.get(self.volume_scpd, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=5)) as response:
            if response.status != 200:
                raise EndpointUnavailable('volume_action_not_declared')
            body = await response.content.read(262145)
            # SCPD also must be accumulated to EOF, not treated as one read.
            chunks = [body]
            size = len(body)
            async for chunk in response.content.iter_chunked(16384):
                size += len(chunk)
                if size > 262144:
                    raise EndpointUnavailable('scpd_too_large')
                chunks.append(chunk)
            if size > 262144:
                raise EndpointUnavailable('scpd_too_large')
        root = ET.fromstring(b''.join(chunks))
        ns = {'s': 'urn:schemas-upnp-org:service-1-0'}
        actions = [a for a in root.findall('s:actionList/s:action', ns)
                   if a.findtext('s:name', '', ns) == 'SetVolume']
        inputs = [a.findtext('s:name', '', ns) for a in actions[0].findall('s:argumentList/s:argument', ns)
                  if a.findtext('s:direction', '', ns) == 'in'] if len(actions) == 1 else []
        if inputs != ['InstanceID', 'Channel', 'DesiredVolume']:
            raise EndpointUnavailable('volume_action_not_declared')
        self.volume_verified = True

    async def reset_session(self):
        if not self.retired:
            await super().reset_session()

    async def retire(self):
        self.retired = True
        await self.disconnect()  # HTTP only, never DLNABackend.disconnect().


class DeferredRoomBackend(DLNABackend):
    def __init__(self, companion, room_id, name, poll_seconds=2):
        super().__init__('127.0.0.1', name=name)
        self.companion, self.room_id = companion, room_id
        self.endpoint = None
        self.epoch = 0
        self.selection_generation = 0
        self.selection_pending = False
        self.spotify_version = None
        self.player = None
        self.closed = False
        self.poll_seconds = poll_seconds
        self._gapless_supported = False
        self._binding_lock = asyncio.Lock()
        self.refresh_sequence = 0
        self.applied_sequence = 0
        self.last_row = None
        self.membership = None
        self.last_renderer_id = None

    async def connect(self):
        # Speaker may advertise before any virtual renderer exists. No zone
        # mutation or transport access is required to establish the app session.
        self._is_connected = True
        try:
            await self.refresh()
        except Exception:
            pass  # Discovery can remain available without a renderer.
        self._poll_task = asyncio.create_task(self._poll_state_loop())
        self.monitor = asyncio.create_task(self._monitor_endpoints())
        return True

    def prepare_for_selection(self):
        super().prepare_for_selection()
        self.selection_generation += 1
        self.selection_pending = True

    def bind_player(self, player):
        self.player = player
        original_generation = player._next_generation
        def next_generation():
            captured = operation.get()
            generation = original_generation()
            # Retain the selection captured at dispatch, while using upstream's
            # newly registered player generation. A newer selection cannot revive
            # an old task simply because it registers a command late.
            operation.set((captured[0] if captured else self.selection_generation, generation))
            return generation
        player._next_generation = next_generation
        for name in ('_play_locked', '_start_playback'):
            original = getattr(player, name)
            def wrap(original):
                @wraps(original)
                async def with_generation(*args, **kwargs):
                    # Carry upstream's generation across metadata/URL/reporting awaits.
                    token = operation.set(operation.get() or self.capture_operation())
                    try:
                        self.check_operation()
                        return await original(*args, **kwargs)
                    finally:
                        operation.reset(token)
                return with_generation
            setattr(player, name, wrap(original))

    def capture_operation(self):
        return (self.selection_generation, self.player._command_generation if self.player else None)

    def check_operation(self):
        captured = operation.get()
        if self.closed or self._external_playback or (captured is not None and captured != self.capture_operation()):
            raise EndpointUnavailable('stale_upstream_command')

    async def release_source(self):
        if self._external_playback:
            return
        self._external_playback = True
        self.selection_pending = False
        self.selection_generation += 1
        self._next_track_proxy_url = None
        if self.player:
            self.player.invalidate_pending_commands()
        if self._on_external_playback:
            await self._on_external_playback()  # Stock Speaker clears cloud session.

    async def refresh(self):
        self.refresh_sequence += 1
        sequence = self.refresh_sequence
        try:
            row = await self.companion.lookup(self.room_id)
        except Exception:
            if sequence < self.applied_sequence:
                return self.last_row
            self.applied_sequence = sequence
            await self.rebind(None)
            raise
        if sequence < self.applied_sequence:
            return self.last_row
        self.applied_sequence = sequence
        self.last_row = row
        version = row['spotifyVersion']
        previous = self.spotify_version
        self.spotify_version = version
        membership = tuple(row['rendererIds'])
        changed = self.membership is not None and membership != self.membership
        self.membership = membership
        if changed or (previous is not None and version != previous):
            await self.release_source()
        if sequence < self.applied_sequence:
            return self.last_row
        endpoint = Endpoint.parse(self.room_id, row['endpoint']) if row.get('endpoint') else None
        replaced = (endpoint and self.last_renderer_id and endpoint.renderer_id != self.last_renderer_id
                    and self._current_proxy_url and not self.selection_pending)
        if endpoint:
            self.last_renderer_id = endpoint.renderer_id
        if replaced:
            await self.release_source()
        if sequence < self.applied_sequence:
            return self.last_row
        await self.rebind(endpoint)
        return row

    async def rebind(self, endpoint):
        async with self._binding_lock:
            if self.endpoint == endpoint:
                return
            old = self._client
            self.epoch += 1
            epoch = self.epoch
            self._client = None
            self.endpoint = endpoint
            if old:
                await old.retire()
            if endpoint is None:
                return
            # Never restart Speaker, clear its session, or send transport commands.
            client = RoomDLNAClient(self, endpoint, epoch)
            try:
                info = await client.connect()
                if self.closed or epoch != self.epoch:
                    raise EndpointUnavailable('retired_endpoint')
                self._client = client
                self._ip, self._port = client.ip, client.port
                self._is_sonos = False
                await self._discover_capabilities(info)
            except BaseException:
                await client.retire()
                self.endpoint = None
                raise

    async def _monitor_endpoints(self):
        while not self.closed:
            try:
                await self.refresh()
                await self.reconcile_volume()
            except asyncio.CancelledError:
                break
            except Exception:
                pass  # refresh invalidates only the current endpoint observation.
            await asyncio.sleep(self.poll_seconds)

    async def disconnect(self):
        self.closed = True
        self.selection_generation += 1
        self._is_connected = False
        tasks = [getattr(self, 'monitor', None), self._poll_task]
        for task in tasks:
            if task:
                task.cancel()
        for task in tasks:
            if task:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.rebind(None)
        await self.companion.close()

    async def can_apply_remote_state(self):
        if self.closed or self._external_playback:
            return False
        if self.selection_pending:
            return True
        return await super().can_apply_remote_state()

    async def play(self, url, metadata):
        token = None
        if operation.get() is None:
            token = operation.set(self.capture_operation())
        try:
            self.check_operation()
            row = await self.refresh()
            self.check_operation()
            if not row['assigned']:
                if not self.selection_pending:
                    raise EndpointUnavailable('explicit_play_required')
                await self.companion.create_for_play(self.room_id)
                deadline = asyncio.get_running_loop().time() + 8
                while not self._client and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(.2)
                    await self.refresh()
                    self.check_operation()
            if not self._client:
                raise EndpointUnavailable('renderer_unavailable')
            if not self.selection_pending and not await self._owns_transport():
                raise EndpointUnavailable('external_source')
            self.check_operation()
            await super().play(url, metadata)
            self.check_operation()
            self.selection_pending = False
        finally:
            if token is not None:
                operation.reset(token)

    async def _play_via_transport(self, url, didl):
        # Preserve upstream DIDL/URI/Play sequence, remove recovery Stop/retry.
        stage = await self._try_transport_sequence(url, didl)
        if stage:
            raise EndpointUnavailable('transport_failed')
        return True

    async def before_write(self, client, action):
        self.check_operation()
        # Refresh only discovery/native Spotify evidence, never physical forwarding
        # ownership. Exact virtual source checks remain upstream's responsibility.
        await self.refresh()
        self.check_operation()
        if client is not self._client or client.retired or client.epoch != self.epoch:
            raise EndpointUnavailable('retired_endpoint')
        if action == 'SetAVTransportURI':
            # Upstream suspends ownership polling during its load sequence. For
            # ordinary next-track loads verify the previous virtual URI directly.
            if not self.selection_pending and not await self.virtual_matches(self._current_proxy_url):
                raise EndpointUnavailable('external_source')
        elif self._starting_playback and action == 'Play':
            # URI loading has a single bounded read-only confirmation window.
            deadline = asyncio.get_running_loop().time() + 5
            while await client.get_media_info() != self.loading_uri:
                self.check_operation()
                if asyncio.get_running_loop().time() >= deadline:
                    raise EndpointUnavailable('loaded_uri_not_confirmed')
                await asyncio.sleep(.1)
                await self.refresh()
        elif not await self._owns_transport():
            raise EndpointUnavailable('external_source')
        self.check_operation()
        if client is not self._client or client.retired:
            raise EndpointUnavailable('retired_endpoint')

    async def _try_transport_sequence(self, url, didl):
        self.loading_uri = url
        return await super()._try_transport_sequence(url, didl)

    async def _owns_transport(self):
        if self.closed or self._external_playback or self._starting_playback or not self._current_proxy_url:
            return False
        return await self.virtual_matches(self._current_proxy_url)

    async def virtual_matches(self, expected):
        client, epoch, generation = self._client, self.epoch, self.selection_generation
        if not client or not expected:
            return False
        uri = await client.get_media_info()
        if (client is not self._client or epoch != self.epoch or generation != self.selection_generation
                or self.closed or self._external_playback):
            return False
        if uri == expected:
            return True
        if uri:
            await self.release_source()
        return False

    async def get_state(self):
        client, epoch = self._client, self.epoch
        if not client or self._external_playback:
            return self._state
        state = await client.get_transport_info()
        result = {'PLAYING': PlaybackState.PLAYING, 'PAUSED_PLAYBACK': PlaybackState.PAUSED,
                  'TRANSITIONING': PlaybackState.LOADING, 'STOPPED': PlaybackState.STOPPED,
                  'NO_MEDIA_PRESENT': PlaybackState.STOPPED}.get(state, self._state)
        if client is not self._client or epoch != self.epoch or self._external_playback:
            return self._state
        return result

    def _notify_track_ended(self):
        if (not self._external_playback and not self.closed and self._client
                and self._current_proxy_url and not self.selection_pending):
            token = operation.set(self.capture_operation())
            try:
                # Stock player schedules its automatic advancement task here.
                # Capture authority at scheduling time, not when it wakes later.
                super()._notify_track_ended()
            finally:
                operation.reset(token)

    @source_operation
    async def set_volume(self, level):
        if not await self._owns_transport():
            raise EndpointUnavailable('volume_source_unconfirmed')
        await super().set_volume(level)
        observed = await self._client.get_volume() if self._client else None
        self.check_operation()
        if observed != max(0, min(100, level)):
            await self.release_source()
            raise EndpointUnavailable('volume_not_confirmed')

    async def reconcile_volume(self):
        if not self.player or not self._client or not await self._owns_transport():
            return
        client, epoch, generation = self._client, self.epoch, self.selection_generation
        volume = await client.get_volume()
        if (volume is not None and client is self._client and epoch == self.epoch
                and generation == self.selection_generation and not self._external_playback
                and not self.closed and volume != self.player._volume):
            self._volume = self.player._volume = volume
            await self.player._report_volume_change()

    @source_operation
    async def pause(self):
        await super().pause()

    @source_operation
    async def resume(self):
        return await super().resume()

    @source_operation
    async def seek(self, position_ms):
        await super().seek(position_ms)

    async def stop(self, **kwargs):
        if not self.closed:
            return await self._stop_with_generation(**kwargs)

    @source_operation
    async def _stop_with_generation(self, **kwargs):
        await super().stop(**kwargs)

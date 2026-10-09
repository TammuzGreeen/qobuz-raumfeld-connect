"""Opt-in upstream DLNA transport with narrow Raumfeld binding/safety overrides.

Keep upstream DIDL, state/position polling and player lifecycle. Never use its
transport recovery, mutation retries, volume debounce or shutdown Stop.
"""
import asyncio
import contextlib
from contextvars import ContextVar
import time
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET
import aiohttp
from qobuz_proxy.backends.dlna.backend import DLNABackend
from qobuz_proxy.backends.dlna.client import DLNAClient, SoapResult
from qobuz_proxy.backends.types import PlaybackState
from .backend import SeekUnsupported, VolumeUncertain
from .client import OwnershipLost


class FencedDLNAClient(DLNAClient):
    def __init__(self, binding, backend, epoch):
        parsed = urlparse(binding['descriptionUrl'])
        if (parsed.scheme != 'http' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment):
            raise OwnershipLost('invalid_binding')
        super().__init__(parsed.hostname, parsed.port or 80, binding['descriptionUrl'])
        self.backend, self.epoch = backend, epoch
        self.retired = False
        self.volume_scpd = None
        self.volume_declared = False

    @staticmethod
    async def read_xml(response):
        # StreamReader.read(n) may return only the bytes currently available,
        # not the complete HTTP entity. Accumulate to EOF with a strict bound.
        chunks, size = [], 0
        async for chunk in response.content.iter_chunked(16384):
            size += len(chunk)
            if size > 262144:
                raise OwnershipLost('invalid_binding')
            chunks.append(chunk)
        return b''.join(chunks)

    async def _fetch_device_description(self):
        # No fallback path guessing/redirects. Only the identity-verified binding
        # from discovery can nominate a renderer.
        async with self._session.get(self.description_url, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=5)) as response:
            if response.status != 200:
                raise OwnershipLost('renderer_unavailable')
            body = await self.read_xml(response)
        root = ET.fromstring(body)
        ns = {'d': 'urn:schemas-upnp-org:device-1-0'}
        devices = root.findall('d:device', ns)
        if len(devices) != 1 or devices[0].find('d:deviceList', ns) is not None:
            raise OwnershipLost('invalid_binding')
        device = devices[0]
        if device.findtext('d:deviceType', '', ns) != 'urn:schemas-upnp-org:device:MediaRenderer:1':
            raise OwnershipLost('invalid_binding')
        services = device.findall('d:serviceList/d:service', ns)
        for service in services:
            if service.findtext('d:serviceType', '', ns) == 'urn:schemas-upnp-org:service:RenderingControl:1':
                self.volume_scpd = urljoin(self.description_url, service.findtext('d:SCPDURL', '', ns))
        parsed = urlparse(self.description_url)
        return self._parse_device_description(body.decode('utf8'), f'http://{parsed.netloc}')

    async def validate_volume(self):
        if self.volume_declared:
            return
        source, target = urlparse(self.description_url), urlparse(self.volume_scpd or '')
        if (not self.volume_scpd or target.scheme != 'http' or target.netloc != source.netloc or
                target.query or target.fragment):
            raise OwnershipLost('volume_action_not_declared')
        async with self._session.get(self.volume_scpd, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=5)) as response:
            if response.status != 200:
                raise OwnershipLost('volume_action_not_declared')
            body = await self.read_xml(response)
        root = ET.fromstring(body)
        ns = {'s': 'urn:schemas-upnp-org:service-1-0'}
        setters = [a for a in root.findall('s:actionList/s:action', ns)
                   if a.findtext('s:name', '', ns) == 'SetVolume']
        inputs = [a.findtext('s:name', '', ns) for a in setters[0].findall('s:argumentList/s:argument', ns)
                  if a.findtext('s:direction', '', ns) == 'in'] if len(setters) == 1 else []
        if inputs != ['InstanceID', 'Channel', 'DesiredVolume']:
            raise OwnershipLost('volume_action_not_declared')
        self.volume_declared = True

    async def _soap_action_detailed(self, url, service, action, args, max_retries=None):
        mutating = not action.startswith('Get')
        if mutating:
            if action not in {'SetAVTransportURI', 'Play', 'Pause', 'Stop', 'Seek', 'SetVolume'}:
                raise OwnershipLost('unsupported_mutation')
            await self.backend.fence(self, action)
        # Even read retries are bounded to one: a stale poll is not new evidence.
        started = time.monotonic()
        result = await super()._soap_action_detailed(url, service, action, args, max_retries=1)
        if mutating and self.backend.relay.timeline:
            self.backend.relay.timeline.note('renderer_soap', action=action, success=result.success,
                elapsedMs=round((time.monotonic() - started) * 1000), upnpErrorCode=result.error_code)
        if not mutating and (self.retired or self.epoch != self.backend.epoch or
                             self is not self.backend._client):
            return SoapResult(success=False)
        if mutating:
            self.backend.check_context(self)
            if not result.success:
                if action == 'Seek' and result.error_code == 710:
                    raise SeekUnsupported('seek_mode_not_supported')
                if action == 'SetVolume':
                    raise VolumeUncertain('volume_command_uncertain')
                raise OwnershipLost('command_failed_or_timed_out')
            if action == 'Pause':
                self.backend._pause_acknowledged = True
            elif action in {'Play', 'Stop', 'SetAVTransportURI'}:
                self.backend._pause_acknowledged = False
        return result

    async def set_volume(self, volume):
        # No delayed setter that can outlive its selected session/client epoch.
        async with self._volume_lock:
            await self.validate_volume()
            return await self._do_set_volume(volume)

    async def reset_session(self):
        # Pinned SOAP error handling calls this even on its final attempt. A
        # retired read must not reinstall an HTTP session after rebind/close.
        # force_close already opens a fresh TCP connection for each request.
        return

    async def retire(self):
        self.retired = True
        if self._volume_debounce_task:
            self._volume_debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._volume_debounce_task
        await super().disconnect()  # HTTP close only; not backend.disconnect().


class RaumfeldDLNABackend(DLNABackend):
    def __init__(self, client, room_id, name, relay):
        super().__init__('127.0.0.1', name=name)
        self.node, self.room_id, self.relay = client, room_id, relay
        self.token = None
        self.on_external = None
        self.on_volume = None
        self.started = False
        self.last_playback_command = self.last_volume_result = None
        self.last_control_error = self.last_release_reason = None
        self.generation = 0
        self.epoch = 0
        self.binding = None
        self.context = ContextVar('direct_dlna_operation', default=None)
        self.operation_lock = asyncio.Lock()
        self.binding_lock = asyncio.Lock()
        self._gapless_supported = False
        self._initial_load = True
        self._loading_uri = None
        self._volume_uncertain = False
        self._pause_acknowledged = False
        self._pause_reads = None
        self.volume_reconcile_seconds = 6
        self._monitor_task = None
        self._on_external_playback = self.external

    async def select(self, selection_id):
        result = await self.node.binding(self.room_id, 'select', selectionId=selection_id)
        self.token = result['token']

    async def release(self):
        token, self.token = self.token, None
        self.generation += 1
        self.started = False
        self._pause_acknowledged = False
        self._pause_reads = None
        if token:
            with contextlib.suppress(Exception):
                await self.node.binding(self.room_id, 'release', token=token)

    async def external(self, reason='external_source'):
        if not self.token:
            return
        self.last_release_reason = reason
        self._external_playback = True
        await self.release()
        if self.on_external:
            await self.on_external()

    async def authority(self, action='lookup'):
        generation, token = self.generation, self.token
        if not token:
            raise OwnershipLost('ownership_lost')
        try:
            result = await self.node.binding(self.room_id, action, token=token)
        except Exception:
            if generation == self.generation and token == self.token:
                await self.external('binding_or_source_lost')
            raise OwnershipLost('ownership_lost') from None
        if generation != self.generation or token != self.token:
            raise OwnershipLost('ownership_lost')
        return result

    async def resolve(self, prepare=False):
        async with self.binding_lock:
            result = await self.authority('prepare' if prepare else 'lookup')
            binding = result.get('binding')
            if binding == self.binding and self._client:
                return result
            # Retire before fetching a new description; old commands cannot run
            # while reconnect is suspended. The receiver and cloud session stay.
            self.epoch += 1
            old, self._client = self._client, None
            self.binding = None
            if old:
                await old.retire()
            if not binding:
                return result
            if (binding.get('roomId') != self.room_id or not binding.get('rendererId') or
                    not binding.get('rendererIds')):
                await self.external('invalid_binding')
                raise OwnershipLost('invalid_binding')
            generation = self.generation
            candidate = FencedDLNAClient(binding, self, self.epoch)
            try:
                info = await candidate.connect()
                if info.udn != binding['rendererId']:
                    raise OwnershipLost('invalid_binding')
                origin = urlparse(binding['descriptionUrl'])
                for url in (info.av_transport_url, info.rendering_control_url, info.connection_manager_url):
                    if not url:
                        continue
                    parsed = urlparse(url)
                    if (parsed.scheme != 'http' or parsed.hostname != origin.hostname or
                            parsed.port != origin.port or parsed.username or parsed.password or
                            parsed.query or parsed.fragment):
                        raise OwnershipLost('invalid_binding')
                current = await self.authority()
                if (generation != self.generation or current.get('binding') != binding):
                    raise OwnershipLost('binding_changed')
                self._client, self.binding = candidate, binding
                self._ip, self._port = candidate.ip, candidate.port
                # Use the user's CD ceiling; don't change capabilities/quality
                # during this experiment or inspect Sonos queues.
                return current
            except BaseException as error:
                await candidate.retire()
                if isinstance(error, OwnershipLost) and generation == self.generation:
                    await self.external('invalid_binding')
                raise

    def check_context(self, client):
        context = self.context.get()
        if (not context or context[:2] != (self.generation, self.epoch) or
                not self.token or client is not self._client or client.retired):
            raise OwnershipLost('ownership_lost')

    async def fence(self, client, action):
        self.check_context(client)
        loading = self.context.get()[2]
        if action == 'SetAVTransportURI' and loading and self._initial_load:
            result = await self.authority('load')
        elif action == 'Play' and loading and self._initial_load:
            result = await self.authority('loaded_play')
        else:
            result = await self.authority('guard')
        self.check_context(client)
        if result.get('binding') != self.binding:
            raise OwnershipLost('binding_changed')
        physical_confirmed = await self.physical_confirmed(client, result)
        self.check_context(client)
        if not physical_confirmed and not (loading and self._initial_load):
            raise OwnershipLost('source_not_confirmed')
        if not (action == 'SetAVTransportURI' and loading and self._initial_load):
            expected = self._loading_uri if loading and action == 'Play' else self._current_proxy_url
            uri = await client.get_media_info()
            self.check_context(client)
            if not expected or uri != expected:
                await self.external('source_not_confirmed')
                raise OwnershipLost('source_not_confirmed')
        # Recheck after the awaited virtual-source read: a native event arriving
        # during that read must defeat this queued send.
        final = await self.authority('lookup' if loading and self._initial_load else 'guard')
        self.check_context(client)
        if final.get('binding') != self.binding:
            raise OwnershipLost('binding_changed')
        if (not final.get('physicalReady') and
                not (physical_confirmed and self.paused_snapshot(final)) and
                not (loading and self._initial_load)):
            raise OwnershipLost('source_not_confirmed')
        if action == 'Pause':
            self._pause_reads = tuple(final.get('physicalReads', []))

    def paused_snapshot(self, result):
        reads = result.get('physicalReads', [])
        return bool(self._pause_acknowledged and self._pause_reads and result.get('physicalIdle') is True
                and len(reads) == len(self._pause_reads)
                and all(type(after) is int and after > before for before, after in zip(self._pause_reads, reads)))

    async def physical_confirmed(self, client, result):
        if result.get('physicalReady'):
            return True
        if not self.paused_snapshot(result) or not self._current_proxy_url:
            return False
        generation, epoch, expected = self.generation, self.epoch, self._current_proxy_url
        uri = await client.get_media_info()
        state = await client.get_transport_info()
        if (self.token and generation == self.generation and epoch == self.epoch
                and client is self._client and expected == self._current_proxy_url
                and uri and uri != expected):
            await self.external('external_source')
            return False
        return (bool(self.token) and generation == self.generation and epoch == self.epoch
                and client is self._client and expected == self._current_proxy_url
                and uri == expected and state == 'PAUSED_PLAYBACK')

    async def _owns_transport(self):
        if self._starting_playback or not self._current_proxy_url or not self.token:
            return False
        try:
            result = await self.resolve()
            if not self._client or not await self.physical_confirmed(self._client, result):
                return False
            generation, epoch = self.generation, self.epoch
            owned = await super()._owns_transport()
            if not owned and self.token and self._client:
                # The load path already obtained exact virtual-source evidence
                # before Play. No subsequent contradictory URI is a loading
                # intermediate or a new selection, even inside upstream grace.
                uri = await self._client.get_media_info()
                if (generation == self.generation and epoch == self.epoch and uri and
                        uri != self._current_proxy_url):
                    await self.external('external_source')
            return owned and generation == self.generation and epoch == self.epoch and bool(self.token)
        except Exception:
            return False

    async def check_external_playback(self):
        # Stock polling treats unavailable URI as "not external" and might
        # advance on STOPPED. Suppress polling/advancement without claiming it.
        return not await self._owns_transport() if self._current_proxy_url else self._external_playback

    async def can_play(self):
        try:
            if self._current_proxy_url:
                return await self._owns_transport()
            await self.authority()
            return bool(self.token)
        except Exception:
            return False

    async def connect(self):
        # Receiver advertisement and player startup don't require a renderer.
        self._is_connected = True
        self._poll_task = asyncio.create_task(self._poll_state_loop())
        self._monitor_task = asyncio.create_task(self.observe())
        return True

    async def disconnect(self):
        await self.release()
        self._is_connected = False
        for task in (self._poll_task, self._monitor_task):
            if task and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._poll_task = self._monitor_task = None
        async with self.binding_lock:
            self.epoch += 1
            old, self._client = self._client, None
            self.binding = None
            if old:
                await old.retire()

    async def observe(self):
        while self._is_connected:
            try:
                if self.token and not self._starting_playback:
                    if self._current_proxy_url:
                        if await self._owns_transport() and self.on_volume:
                            client, epoch, generation = self._client, self.epoch, self.generation
                            volume = await client.get_volume()
                            if (client is self._client and epoch == self.epoch and generation == self.generation
                                    and self.token and type(volume) is int and 0 <= volume <= 100
                                    and volume != self._volume):
                                self._volume = volume
                                await self.on_volume(volume)
                    else:
                        await self.authority()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # authority() already releases on lost source/control API.
            await asyncio.sleep(2)

    async def operation(self, action, callback, *, loading=False):
        generation = self.generation
        async with self.operation_lock:
            if generation != self.generation or not self.token:
                raise OwnershipLost('ownership_lost')
            record = {'action': action, 'result': 'pending'}
            self.last_playback_command = record
            context = None
            try:
                if not loading and not await self._owns_transport():
                    raise OwnershipLost('source_not_confirmed')
                if generation != self.generation or not self.token:
                    raise OwnershipLost('ownership_lost')
                context = self.context.set((generation, self.epoch, loading))
                result = await callback()
                self.check_context(self._client)
                record['result'] = 'completed'
                self.last_control_error = None
                return result
            except SeekUnsupported:
                record['result'] = 'unsupported'
                self.last_control_error = 'seek_mode_not_supported'
                raise
            except VolumeUncertain:
                record['result'] = 'uncertain'
                self.last_control_error = 'volume_command_uncertain'
                raise
            except Exception:
                record['result'] = 'failed'
                self.last_control_error = 'control_request_failed'
                if generation == self.generation:
                    await self.external('command_failed')
                raise
            finally:
                if context is not None:
                    self.context.reset(context)

    async def play(self, url, metadata):
        generation = self.generation
        # The only path that may create a truly unassigned zone is actual Play.
        deadline = time.monotonic() + 10
        while True:
            await self.resolve(prepare=True)
            if self._client:
                break
            if generation != self.generation or time.monotonic() >= deadline:
                raise OwnershipLost('renderer_unavailable')
            await asyncio.sleep(.2)
        if generation != self.generation:
            raise OwnershipLost('ownership_lost')
        if self._current_proxy_url and not await self._owns_transport():
            raise OwnershipLost('source_not_confirmed')
        proxy_url = self.relay.register(url)
        async def start():
            await super(RaumfeldDLNABackend, self).play(proxy_url, metadata)
            self.started = True
            self._initial_load = False
        await self.operation('play', start, loading=True)

    async def _play_via_transport(self, url, didl):
        self._loading_uri = url
        try:
            old_uri = await self._client.get_media_info()
            self.check_context(self._client)
            await self._client.set_av_transport_uri(url, didl)
            self.relay.timeline.note('uri_load', success=True)
            # Only a bounded read wait. Never resend an uncertain URI mutation.
            deadline = time.monotonic() + 5
            while True:
                current = await self._client.get_media_info()
                evidence = await self.authority('lookup' if self._initial_load else 'guard')
                if current == url and (self._initial_load or evidence.get('physicalReady')):
                    break
                self.check_context(self._client)
                if current and current not in (old_uri, url):
                    await self.external('external_source')
                    raise OwnershipLost('ownership_lost')
                if time.monotonic() >= deadline:
                    raise OwnershipLost('source_not_confirmed')
                await asyncio.sleep(.1)
            await self._client.play()
            self.relay.timeline.note('play_soap', success=True)
            return True
        finally:
            self._loading_uri = None

    async def pause(self):
        await self.operation('pause', lambda: super(RaumfeldDLNABackend, self).pause())

    async def resume(self):
        return await self.operation('resume', lambda: super(RaumfeldDLNABackend, self).resume())

    async def stop(self, **kwargs):
        # Upstream player.stop() during cleanup must not send a transport command
        # once release has fenced this selection.
        if self.token:
            await self.operation('stop', lambda: super(RaumfeldDLNABackend, self).stop(**kwargs))

    async def seek(self, position_ms):
        await self.operation('seek', lambda: super(RaumfeldDLNABackend, self).seek(position_ms))

    async def observed_position(self):
        return await self.get_position()

    async def get_state(self):
        client, epoch, generation = self._client, self.epoch, self.generation
        if not client or not self.token:
            return PlaybackState.LOADING
        state = await client.get_transport_info()
        if (client is not self._client or epoch != self.epoch or generation != self.generation or not self.token):
            return PlaybackState.LOADING
        return {'PLAYING': PlaybackState.PLAYING, 'PAUSED_PLAYBACK': PlaybackState.PAUSED,
                'STOPPED': PlaybackState.STOPPED, 'NO_MEDIA_PRESENT': PlaybackState.STOPPED}.get(state, PlaybackState.LOADING)

    async def set_volume(self, level):
        if self._volume_uncertain:
            raise VolumeUncertain('volume_command_uncertain')
        if not await self._owns_transport():
            raise OwnershipLost('play_required')
        before = await self.authority('guard')
        async def apply():
            await self._client.set_volume(max(0, min(100, level)))
            observed = await self._client.get_volume()
            self.check_context(self._client)
            self.last_volume_result = {'observedVolume': observed, 'attempts': 1}
            if observed != level:
                raise VolumeUncertain('volume_command_uncertain')
            self._volume = observed
        try:
            await self.operation('volume', apply)
        except VolumeUncertain:
            self._volume_uncertain = True
            # Cached pre-setter physical evidence cannot retain an uncertain
            # write. Wait only for complete new read-only observations.
            deadline = time.monotonic() + self.volume_reconcile_seconds
            counts = before.get('physicalReads', [])
            while True:
                fresh = await self.authority('guard')
                after = fresh.get('physicalReads', [])
                if counts and len(counts) == len(after) and all(b > a for a, b in zip(counts, after)):
                    break
                if time.monotonic() >= deadline:
                    await self.external('volume_source_not_confirmed')
                    raise OwnershipLost('ownership_lost') from None
                await asyncio.sleep(.1)
            if not await self._owns_transport():
                await self.external('volume_source_not_confirmed')
                raise OwnershipLost('ownership_lost') from None
            observed = await self._client.get_volume()
            if not await self._owns_transport():
                await self.external('volume_source_not_confirmed')
                raise OwnershipLost('ownership_lost') from None
            self.last_volume_result = {'observedVolume': observed, 'attempts': 1}
            raise

    async def set_next_track(self, *args, **kwargs):
        return False

    async def clear_next_track(self):
        self._next_track_proxy_url = self._next_track_metadata = None

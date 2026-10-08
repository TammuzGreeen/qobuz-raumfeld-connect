"""Unmodified qobuz-proxy AudioBackend contract, backed by guarded Node HTTP."""
import asyncio
import contextlib
import secrets
from urllib.parse import urlparse
import aiohttp
from aiohttp import web
from qobuz_proxy.backends.base import AudioBackend
from qobuz_proxy.backends.types import BackendInfo, PlaybackState
from .client import OwnershipLost


class SeekUnsupported(Exception):
    """A definite renderer rejection, not loss of playback ownership."""


class VolumeUncertain(Exception):
    """Volume outcome is unconfirmed, but fresh Qobuz ownership is retained."""


class AudioRelay:
    def __init__(self, app, address, port):
        self.address, self.port = address, port
        self.tracks = {}
        app.router.add_get('/audio/{key}', self.stream)

    def register(self, url):
        if urlparse(url).scheme not in ('https', 'http'):
            raise ValueError('Invalid upstream audio URL')
        key = secrets.token_hex(16)
        self.tracks[key] = url
        while len(self.tracks) > 4:
            del self.tracks[next(iter(self.tracks))]
        return f'http://{self.address}:{self.port}/audio/{key}'

    async def stream(self, request):
        url = self.tracks.get(request.match_info['key'])
        if not url:
            raise web.HTTPNotFound()
        headers = {name: request.headers[name] for name in ('Range', 'If-Range') if name in request.headers}
        headers['Accept-Encoding'] = 'identity'
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30),
                                         auto_decompress=False) as session:
            async with session.get(url, headers=headers) as source:
                if source.status not in (200, 206, 416):
                    raise web.HTTPBadGateway()
                copied = {k: v for k, v in source.headers.items() if k.lower() in
                          ('content-type', 'content-length', 'content-range', 'accept-ranges')}
                response = web.StreamResponse(status=source.status, headers=copied)
                await response.prepare(request)
                if request.method != 'HEAD':
                    async for chunk in source.content.iter_chunked(65536):
                        await response.write(chunk)
                await response.write_eof()
                return response


class RaumfeldBackend(AudioBackend):
    def __init__(self, client, room_id, name, relay, on_external=None):
        super().__init__(name)
        self.client, self.room_id, self.relay = client, room_id, relay
        self.token = None
        self.on_external = on_external
        self.monitor = None
        self.sample = {}
        self.started = False
        self.last_playing = False
        self.stopped_polls = 0
        self.last_playback_command = None
        self.last_control_error = None
        self.last_volume_result = None
        self.last_release_reason = None

    async def select(self, selection_id):
        await self.release()
        result = await self.client.control(self.room_id, 'select', selectionId=selection_id)
        self.token = result['token']
        self.last_release_reason = None

    async def release(self):
        token, self.token = self.token, None
        self.started = self.last_playing = False
        if token:
            with contextlib.suppress(Exception):
                await self.client.control(self.room_id, 'release', token=token)

    async def command(self, action, **kwargs):
        if not self.token:
            raise OwnershipLost('Fresh selection required')
        command_record = None
        try:
            if action != 'heartbeat':
                command_record = {'action': action, 'result': 'pending'}
                self.last_playback_command = command_record
            result = await self.client.control(self.room_id, action, token=self.token, **kwargs)
            if action == 'volume':
                self.last_volume_result = {'controlAPI': result.get('controlAPI'),
                    'observedVolume': result.get('observedVolume'), 'attempts': result.get('attempts', 1)}
                if (result.get('accepted') is False and result.get('applied') is None
                        and result.get('error') == 'volume_command_uncertain'
                        and result.get('ownershipRetained') is True):
                    command_record['result'] = 'uncertain'
                    self.last_control_error = 'volume_command_uncertain'
                    return result
                if (result.get('accepted') is not True or result.get('applied') is not True
                        or type(result.get('observedVolume')) is not int
                        or result['observedVolume'] != kwargs.get('value')):
                    raise OwnershipLost('control_request_failed')
            if (action == 'seek' and result.get('accepted') is False
                    and result.get('applied') is False and result.get('error') == 'seek_mode_not_supported'
                    and result.get('upnpErrorCode') == 710):
                command_record['result'] = 'unsupported'
                self.last_control_error = 'seek_mode_not_supported'
                return result
            if action != 'heartbeat':
                command_record['result'] = 'completed'
                self.last_control_error = None
            return result
        except Exception as error:
            if action == 'volume':
                self.last_volume_result = {'controlAPI': getattr(error, 'control_api_diagnostic', None)}
            known = {'ownership_lost', 'room_not_enabled', 'room_not_found', 'grouped_room_not_supported',
                     'state_unavailable', 'room_membership_changed', 'observation_busy', 'play_required',
                     'command_failed_or_timed_out', 'zone_unavailable', 'renderer_unavailable',
                     'source_not_confirmed', 'spotify_still_active', 'invalid_stream', 'unsupported_api'}
            self.last_control_error = str(error) if str(error) in known else 'control_request_failed'
            if action != 'heartbeat':
                command_record['result'] = 'failed'
            reason = ('control_api_unavailable' if getattr(error, 'control_api_diagnostic', {}).get('exception') == 'connection_error'
                else 'observations_unavailable' if self.last_control_error == 'state_unavailable'
                else 'command_failure' if self.last_control_error == 'command_failed_or_timed_out'
                else 'lease_or_source_lost' if self.last_control_error == 'ownership_lost' else 'control_failure')
            await self.external(reason)
            raise

    async def external(self, reason='lease_or_source_lost'):
        had_token = self.token is not None
        if had_token:
            self.last_release_reason = reason
        await self.release()
        if had_token and self.on_external:
            await self.on_external()

    async def can_play(self):
        if not self.token:
            return False
        try:
            await self.command('heartbeat')
            return True
        except Exception:
            return False

    async def connect(self):
        self._is_connected = True
        if self.monitor is None:
            self.monitor = asyncio.create_task(self.observe())
        return True

    async def disconnect(self):
        await self.release()  # Never Stop someone else's audio on shutdown.
        self._is_connected = False
        if self.monitor and self.monitor is not asyncio.current_task():
            self.monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor
        self.monitor = None

    async def observe(self):
        while True:
            try:
                state = await self.client.state()
                room = next((r for r in state['rooms'] if r['id'] == self.room_id), None)
                if not room or (not room['fresh'] and not room.get('transitioning')):
                    await self.external('observations_unavailable')
                else:
                    target = room['zoneId'] or room['rendererIds'][0]
                    self.sample = next((r for r in state['renderers'] if r['id'] == target), {})
                    if self.token:
                        await self.command('heartbeat')
                    if self.started and self.token:
                        mode = self.sample.get('transport')
                        if mode == 'PLAYING':
                            self.last_playing = True
                            self.stopped_polls = 0
                        elif mode == 'STOPPED' and self.last_playing:
                            self.stopped_polls += 1
                            if self.stopped_polls >= 2:
                                self.last_playing = False
                                self._notify_track_ended()
                        self._notify_position_update(self.sample.get('positionMs', 0))
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.external()
            await asyncio.sleep(2)

    async def play(self, url, metadata):
        proxy_url = self.relay.register(url)
        await self.command('play', url=proxy_url, metadata={
            'title': metadata.title[:2000], 'artist': metadata.artist[:2000], 'album': metadata.album[:2000],
            'mime': 'audio/mpeg' if metadata.bit_depth == 0 else 'audio/flac'})
        self.started = True
        self.last_playing = False
        self._notify_state_change(PlaybackState.PLAYING)

    async def pause(self):
        await self.command('pause')
        self.last_playing = False
        self._notify_state_change(PlaybackState.PAUSED)

    async def resume(self):
        await self.command('resume')
        self._notify_state_change(PlaybackState.PLAYING)
        return True

    async def stop(self, *, next_track_id=None):
        # Upstream may call stop before the first load; it is not permission to
        # stop native Spotify. No-op until this backend has actually played.
        if self.started and self.token:
            self.last_playing = False
            await self.command('stop')
        self._notify_state_change(PlaybackState.STOPPED)

    async def seek(self, position_ms):
        result = await self.command('seek', value=int(position_ms))
        if result.get('error') == 'seek_mode_not_supported':
            raise SeekUnsupported('Renderer rejected seek mode')

    async def observed_position(self):
        # Used after a rejected initial seek: report fresh renderer evidence,
        # never the requested phone position that was not actually applied.
        await self.command('heartbeat')
        state = await self.client.state()
        room = next((r for r in state['rooms'] if r['id'] == self.room_id), None)
        if not room or not room.get('fresh') or not room.get('owned'):
            await self.external()
            raise OwnershipLost('state_unavailable')
        target = room['zoneId'] or room['rendererIds'][0]
        self.sample = next((r for r in state['renderers'] if r['id'] == target), {})
        position = self.sample.get('positionMs')
        if not self.sample.get('fresh') or not isinstance(position, int) or position < 0:
            await self.external()
            raise OwnershipLost('state_unavailable')
        return position

    async def get_position(self):
        return self.sample.get('positionMs', 0)

    async def set_volume(self, level):
        if not self.started:
            return  # Initial cloud snapshots must not change native volume.
        result = await self.command('volume', value=int(level))
        if result.get('error') == 'volume_command_uncertain':
            raise VolumeUncertain('Room volume outcome unconfirmed')

    async def get_volume(self):
        if self.last_volume_result:
            observed = self.last_volume_result.get('observedVolume')
            if isinstance(observed, int) and 0 <= observed <= 100:
                return observed
        return self.sample.get('volume') or 0

    async def get_state(self):
        if not self.token or not self.started:
            return PlaybackState.STOPPED
        return {'PLAYING': PlaybackState.PLAYING, 'PAUSED_PLAYBACK': PlaybackState.PAUSED,
                'TRANSITIONING': PlaybackState.LOADING}.get(self.sample.get('transport'), PlaybackState.STOPPED)

    def get_info(self):
        return BackendInfo(backend_type='raumfeld', name=self.name, device_id=self.room_id)

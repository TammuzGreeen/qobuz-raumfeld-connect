"""Room-local prepared media and direct delivery using the stable renderer boundary."""
import asyncio
import re
import uuid
import mimetypes
from urllib.parse import urlsplit

from defusedxml.ElementTree import fromstring
from async_upnp_client.exceptions import UpnpActionError
from qobuz.upstream_backend import DeferredRoomBackend, RoomDLNAClient, operation
from qobuz_proxy.backends.types import BackendTrackMetadata

AVT = 'urn:schemas-upnp-org:service:AVTransport:1'
RCS = 'urn:schemas-upnp-org:service:RenderingControl:1'
MIMES = {'audio/mpeg', 'audio/flac', 'audio/x-flac', 'audio/wav', 'audio/x-wav', 'audio/L16'}


def fail(code=501):
    raise UpnpActionError(error_code=code)


def seconds(value):
    match = re.fullmatch(r'(\d{1,5}):([0-5]\d):([0-5]\d)(?:\.\d+)?', value)
    if not match:
        fail(711)
    return int(match[1]) * 3600 + int(match[2]) * 60 + int(match[3])


def udn(bridge_id, room_id):
    return 'uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, f'raumfeld-dlna:{bridge_id}:{room_id}'))


def protocols(sink):
    # Only exact backed audio types. No wildcard, video, transcoding or invented
    # DLNA profile/range flags. Report the backing renderer's actual attributes.
    return ','.join(entry for entry in sink.split(',')
                    if len(entry.split(':', 3)) == 4 and entry.split(':', 3)[0] == 'http-get'
                    and entry.split(':', 3)[2] in MIMES)


class DirectClient(RoomDLNAClient):
    write_actions = RoomDLNAClient.write_actions | {'SetMute'}


class DirectBackend(DeferredRoomBackend):
    client_type = DirectClient

    async def connect(self):
        # Room's serialized read-only poll replaces Qobuz's track-end/queue poll.
        # There is no bridge queue and no unattended advancement callback.
        self._is_connected = True
        try:
            await self.refresh()
        except Exception:
            pass
        return True

    async def before_write(self, client, action):
        if action == 'Seek' and getattr(self, 'initial_seek_in_progress', False):
            # Reuse the stable URI/source confirmation used before initial Play.
            # Explicit Play has authorized this load, but _current_proxy_url has
            # not yet been committed, so ordinary owned-transport checks cannot
            # authorize a seek here. No background caller enters this path.
            return await super().before_write(client, 'Play')
        return await super().before_write(client, action)

    async def _try_transport_sequence(self, url, didl):
        sink = protocols(await self._client.get_protocol_info() or '')
        if self.media_type not in [p.split(':', 3)[2] for p in sink.split(',') if p]:
            fail(714)
        # Preserve controller DIDL. A prepared nonzero start is acknowledged
        # before our Play, rather than intentionally starting at zero first.
        self.loading_uri = url
        if not await self._client.set_av_transport_uri(url, self.raw_metadata):
            return 'set_uri'
        if self.initial_seek is not None:
            self.initial_seek_in_progress = True
            try:
                if not await self._client.seek(self.initial_seek * 1000):
                    fail(710)
            finally:
                self.initial_seek_in_progress = False
        if not await self._client.play():
            return 'play'
        return None


class Room:
    def __init__(self, config, backend):
        self.config, self.backend = config, backend
        self.lock = asyncio.Lock()
        self.uri, self.metadata = '', ''
        self.media_type = None
        self.loaded_uri = None
        self.pending_seek = None
        self.transport, self.status = 'NO_MEDIA_PRESENT', 'OK'
        self.position, self.duration = '00:00:00', '00:00:00'
        self.volume, self.mute = None, None
        self.sink = ''
        self.connection_protocol = ''
        self.limitation = 'backing_renderer_unavailable'
        self.changed = lambda: None

    def retire(self):
        self.loaded_uri = None
        self.pending_seek = None
        self.transport = 'STOPPED' if self.uri else 'NO_MEDIA_PRESENT'
        self.status = 'ERROR_OCCURRED'
        self.changed()

    async def call(self, action, **args):
        # Serialize commands and observation commits per logical room only.
        async with self.lock:
            token = operation.set(self.backend.capture_operation())
            try:
                result = await self.dispatch(action, **args)
                self.changed()
                return result
            except UpnpActionError as error:
                if error.error_code == 501:
                    self.status = 'ERROR_OCCURRED'
                    self.changed()
                raise
            except Exception:
                # A failure is never represented as successful playback, and no
                # mutation is retried. Raw upstream exceptions are not logged.
                self.status = 'ERROR_OCCURRED'
                self.changed()
                fail()
            finally:
                operation.reset(token)

    async def refresh(self):
        try:
            row = await self.backend.refresh()
            self.limitation = None if row.get('endpoint') else 'backing_renderer_unavailable'
        except Exception as error:
            code = getattr(error, 'error_code', '')
            self.limitation = 'grouped_room_not_supported' if code == 'grouped_room_not_supported' else 'room_or_topology_unavailable'
        client = self.backend._client
        if not client:
            self.sink, self.volume, self.mute = '', None, None
            if self.loaded_uri:
                self.retire()
            return
        self.sink = protocols(await client.get_protocol_info() or '')
        self.volume = await client.get_volume()
        info = client.device_info
        if info.rendering_control_url:
            result = await client._soap_action_detailed(info.rendering_control_url, RCS,
                'GetMute', {'InstanceID': '0', 'Channel': 'Master'}, max_retries=1)
            raw = client._parse_xml_value(result.body, 'CurrentMute') if result.success else None
            self.mute = raw in {'1', 'true'} if raw in {'0', '1', 'true', 'false'} else None
        if not self.loaded_uri or self.loaded_uri != self.uri:
            return  # Idle observation never claims native playback.
        if not await self.backend._owns_transport():
            self.retire()
            return
        state = await client.get_transport_info()
        if state in {'PLAYING', 'PAUSED_PLAYBACK', 'STOPPED', 'TRANSITIONING', 'NO_MEDIA_PRESENT'}:
            self.transport = state
        else:
            self.status = 'ERROR_OCCURRED'
        result = await client._soap_action_detailed(info.av_transport_url, AVT,
            'GetPositionInfo', {'InstanceID': '0'}, max_retries=1)
        if result.success:
            if state in {'PLAYING', 'PAUSED_PLAYBACK', 'STOPPED', 'TRANSITIONING', 'NO_MEDIA_PRESENT'}:
                self.status = 'OK'
            for name, attr in [('RelTime', 'position'), ('TrackDuration', 'duration')]:
                raw = client._parse_xml_value(result.body, name)
                if raw and re.fullmatch(r'\d+:[0-5]\d:[0-5]\d(?:\.\d+)?', raw):
                    setattr(self, attr, raw)
        else:
            self.status = 'ERROR_OCCURRED'

    async def require_owned(self):
        await self.backend.refresh()
        self.backend.check_operation()
        if not self.loaded_uri or not await self.backend._owns_transport():
            fail(701)

    async def write(self, action, **args):
        await self.require_owned()
        client = self.backend._client
        if action == 'SetVolume':
            await client.verify_volume_action()
        service = RCS if action in {'SetVolume', 'SetMute'} else AVT
        url = client.device_info.rendering_control_url if service == RCS else client.device_info.av_transport_url
        result = await client._soap_action_detailed(url, service, action,
            {'InstanceID': '0', **args}, max_retries=1)
        if not result.success:
            fail(result.error_code if result.error_code in {401, 602, 701, 710, 711} else 501)

    async def dispatch(self, action, **args):
        if args.get('InstanceID', 0) != 0:
            fail(718)
        if args.get('Channel', 'Master') != 'Master':
            fail(402)
        if action == 'SetAVTransportURI':
            uri, metadata = args['CurrentURI'], args['CurrentURIMetaData']
            parsed = urlsplit(uri)
            if (len(uri) > 8192 or parsed.scheme != 'http' or not parsed.hostname
                    or parsed.username or parsed.password or parsed.fragment):
                fail(714)
            if len(metadata.encode()) > 65536:
                fail(402)
            media_type = mimetypes.guess_type(parsed.path)[0]
            if metadata:
                try:
                    root = fromstring(metadata)
                    if root.tag != '{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}DIDL-Lite':
                        fail(402)
                    for resource in root.iter('{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}res'):
                        info = resource.get('protocolInfo', '').split(':', 3)
                        if resource.text == uri and len(info) == 4 and info[0] == 'http-get':
                            media_type = info[2]
                except UpnpActionError:
                    raise
                except Exception:
                    fail(402)
            if media_type not in MIMES:
                fail(714)
            # No backend selection, source retirement or physical command here.
            self.uri, self.metadata = uri, metadata
            self.media_type = media_type
            self.pending_seek = None
            self.position, self.duration = '00:00:00', '00:00:00'
            self.transport, self.status = 'STOPPED', 'OK'
            return {}
        if action == 'Play':
            if args['Speed'] != '1':
                fail(717)
            if not self.uri:
                fail(701)
            new_load = self.loaded_uri != self.uri or self.backend._external_playback
            if new_load:
                # Only this explicit action authorizes on-demand renderer demand.
                self.backend.prepare_for_selection()
                operation.set(self.backend.capture_operation())
                self.backend.raw_metadata = self.metadata
                self.backend.media_type = self.media_type
                self.backend.initial_seek = self.pending_seek
                try:
                    await self.backend.play(self.uri, BackendTrackMetadata('dlna-item', title='', duration_ms=0))
                except Exception:
                    await self.backend.release_source('dlna_play_failed')
                    raise
                self.loaded_uri = self.uri
                self.pending_seek = None  # Initial seek, if any, was acknowledged.
                self.connection_protocol = next((p for p in protocols(
                    await self.backend._client.get_protocol_info() or '').split(',')
                    if p and p.split(':', 3)[2] == self.media_type), '')
            else:
                await self.require_owned()
            if self.pending_seek is not None:
                target = self.pending_seek
                if not await self.backend.seek(target * 1000):
                    fail(710)
                self.pending_seek = None
            # All seeks above are after explicit Play, never during paused Seek.
            if not new_load:
                await self.write('Play', Speed='1')
            self.status = 'OK'
            await self.refresh()
            return {}
        if action == 'Pause':
            await self.write('Pause')
            await self.refresh()
            return {}
        if action == 'Stop':
            if self.loaded_uri:
                await self.write('Stop')
                await self.refresh()
            else:
                self.transport = 'STOPPED' if self.uri else 'NO_MEDIA_PRESENT'
            self.pending_seek = None
            return {}
        if action == 'Seek':
            if args['Unit'] not in {'REL_TIME', 'ABS_TIME'}:
                fail(710)
            target = seconds(args['Target'])
            if not self.uri:
                fail(701)
            if self.loaded_uri != self.uri or self.transport in {'STOPPED', 'PAUSED_PLAYBACK'}:
                if self.loaded_uri == self.uri:
                    await self.require_owned()
                    await self.backend._client.verify_seek_action()
                self.pending_seek = target  # Accepted intent, NOT observed position.
            else:
                await self.require_owned()
                if not await self.backend.seek(target * 1000):
                    fail(710)
                await self.refresh()
            return {}
        if action in {'GetVolume', 'GetMute', 'SetVolume', 'SetMute'}:
            await self.refresh()
            attr = 'volume' if 'Volume' in action else 'mute'
            if getattr(self, attr) is None:
                fail(401)
            if action.startswith('Set'):
                desired = args['DesiredVolume'] if attr == 'volume' else args['DesiredMute']
                if attr == 'volume' and not 0 <= desired <= 100:
                    fail(402)
                await self.write(action, Channel='Master', **{
                    'DesiredVolume' if attr == 'volume' else 'DesiredMute': str(int(desired))})
                await self.refresh()
                if getattr(self, attr) != desired:
                    fail()
                return {}
            return {'CurrentVolume' if attr == 'volume' else 'CurrentMute': getattr(self, attr)}
        await self.refresh()
        if action == 'GetTransportInfo':
            return dict(CurrentTransportState=self.transport, CurrentTransportStatus=self.status, CurrentSpeed='1')
        if action == 'GetPositionInfo':
            return dict(Track=1 if self.uri else 0, TrackDuration=self.duration, TrackMetaData=self.metadata,
                        TrackURI=self.uri, RelTime=self.position, AbsTime=self.position, RelCount=-1, AbsCount=-1)
        if action == 'GetMediaInfo':
            return dict(NrTracks=1 if self.uri else 0, MediaDuration=self.duration, CurrentURI=self.uri,
                        CurrentURIMetaData=self.metadata, NextURI='', NextURIMetaData='',
                        PlayMedium='NETWORK' if self.uri else 'NONE', RecordMedium='NOT_IMPLEMENTED', WriteStatus='NOT_IMPLEMENTED')
        if action == 'GetDeviceCapabilities':
            return dict(PlayMedia='NETWORK', RecMedia='NOT_IMPLEMENTED', RecQualityModes='NOT_IMPLEMENTED')
        if action == 'GetTransportSettings':
            return dict(PlayMode='NORMAL', RecQualityMode='NOT_IMPLEMENTED')
        if action == 'SetPlayMode':
            if args['NewPlayMode'] != 'NORMAL':
                fail(712)
            return {}
        if action == 'GetCurrentTransportActions':
            actions = 'Play,Stop,Seek' if self.uri else ''
            if self.loaded_uri == self.uri and self.backend._client and not self.backend._external_playback:
                actions += ',Pause'
            return {'Actions': actions}
        if action == 'GetProtocolInfo':
            return {'Source': '', 'Sink': self.sink}
        if action == 'GetCurrentConnectionIDs':
            return {'ConnectionIDs': '0' if self.loaded_uri else ''}
        if action == 'GetCurrentConnectionInfo':
            if args['ConnectionID'] != 0 or not self.loaded_uri:
                fail(706)
            return dict(RcsID=0, AVTransportID=0, ProtocolInfo=self.connection_protocol, PeerConnectionManager='',
                        PeerConnectionID=-1, Direction='Input', Status='OK' if self.status == 'OK' else 'Unknown')
        if action == 'ListPresets':
            return {'CurrentPresetNameList': 'FactoryDefaults'}
        if action == 'SelectPreset':
            fail(701)  # No multi-setting/fallback mutations.
        fail(401)

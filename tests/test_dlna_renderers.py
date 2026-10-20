"""Real device-stack HTTP/SOAP/GENA and synthetic backing renderers, no hardware."""
import asyncio
from datetime import datetime, timedelta
import html
import json
import socket
import unittest
from unittest.mock import AsyncMock, MagicMock
import xml.etree.ElementTree as ET

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from async_upnp_client.server import SsdpSearchResponder, _build_advertisements
from async_upnp_client.utils import CaseInsensitiveDict

from experiments.dlna.runtime import Room, DirectBackend, udn, protocols, AVT, RCS
from experiments.dlna.services import device_class, AV_ACTIONS, RC_ACTIONS, CM_ACTIONS
from experiments.dlna.server import RendererServer
from experiments.dlna.main import retired
from qobuz.upstream_backend import operation, RendererRequestError

BRIDGE_ID = '482e698c-e616-4f87-83c7-8448a2c0622c'
DIDL = '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"><item id="1" parentID="0" restricted="1"><res protocolInfo="http-get:*:audio/flac:*">{}</res></item></DIDL-Lite>'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Backing:
    def __init__(self, identity):
        self.identity = identity
        self.uri = 'spotify:baseline'
        self.state = 'PLAYING'
        self.volume, self.mute, self.position = 12, False, '00:00:00'
        self.commands, self.metadata = [], ''
        self.failure, self.failure_code, self.hook = None, 501, None
        self.media_requests = []

    async def start(self):
        app = web.Application()
        app.router.add_get('/device.xml', self.description)
        app.router.add_get('/changed.xml', self.description)
        app.router.add_get('/scpd', self.scpd)
        app.router.add_get('/audio.flac', self.audio)
        app.router.add_post('/soap', self.soap)
        self.server = TestServer(app)
        await self.server.start_server()

    async def description(self, request):
        services = ''.join(f'<service><serviceType>urn:schemas-upnp-org:service:{name}:1</serviceType>'
            f'<serviceId>urn:upnp-org:serviceId:{name}</serviceId><controlURL>/soap</controlURL>'
            '<eventSubURL>/events</eventSubURL><SCPDURL>/scpd</SCPDURL></service>'
            for name in ('AVTransport', 'RenderingControl', 'ConnectionManager'))
        return web.Response(text='<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
            '<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>'
            f'<friendlyName>Synthetic</friendlyName><manufacturer>Raumfeld</manufacturer><UDN>{self.identity}</UDN>'
            f'<serviceList>{services}</serviceList></device></root>', content_type='text/xml')

    async def scpd(self, request):
        actions = ''
        for action, args in [('Seek', ['InstanceID', 'Unit', 'Target']),
                             ('SetVolume', ['InstanceID', 'Channel', 'DesiredVolume'])]:
            actions += f'<action><name>{action}</name><argumentList>' + ''.join(
                f'<argument><name>{arg}</name><direction>in</direction>'
                f'<relatedStateVariable>{"A_ARG_TYPE_SeekMode" if arg == "Unit" else arg}</relatedStateVariable></argument>'
                for arg in args) + '</argumentList></action>'
        return web.Response(text='<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
            f'<actionList>{actions}</actionList><serviceStateTable><stateVariable><name>A_ARG_TYPE_SeekMode</name>'
            '<allowedValueList><allowedValue>ABS_TIME</allowedValue></allowedValueList>'
            '</stateVariable></serviceStateTable></scpd>', content_type='text/xml')

    async def audio(self, request):
        self.media_requests.append(request.headers.get('Range'))
        return web.Response(status=206, body=b'synthetic-audio-bytes',
            headers={'Content-Type': 'audio/flac', 'Content-Range': 'bytes 0-20/21'})

    async def soap(self, request):
        service, action = request.headers['SOAPAction'].strip('"').split('#')
        body = ET.fromstring(await request.read())
        fields = {e.tag: e.text or '' for e in body.find('{http://schemas.xmlsoap.org/soap/envelope/}Body')[0]}
        self.commands.append(action)
        if self.hook:
            await self.hook(action)
        if action == self.failure:
            return web.Response(status=500, text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                '<s:Body><s:Fault><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
                f'<errorCode>{self.failure_code}</errorCode></UPnPError></detail></s:Fault></s:Body></s:Envelope>')
        if action == 'SetAVTransportURI':
            self.uri, self.metadata = fields['CurrentURI'], fields['CurrentURIMetaData']
        if action == 'Play':
            self.state = 'PLAYING'
            # Simulated Raumfeld fetches the HTTP media URL, never the controller.
            async with aiohttp.ClientSession() as session:
                async with session.get(self.uri, headers={'Range': 'bytes=0-'}) as response:
                    await response.read()
        if action == 'Pause':
            self.state = 'PAUSED_PLAYBACK'
        if action == 'Stop':
            self.state = 'STOPPED'
        if action == 'Seek':
            self.position = fields['Target']
            self.state = 'PLAYING'  # Model Raumfeld's paused Seek auto-resume.
        if action == 'SetVolume':
            self.volume = int(fields['DesiredVolume'])
        if action == 'SetMute':
            self.mute = fields['DesiredMute'] == '1'
        output = {
            'GetMediaInfo': {'CurrentURI': self.uri},
            'GetTransportInfo': {'CurrentTransportState': self.state},
            'GetPositionInfo': {'RelTime': self.position, 'TrackDuration': '00:05:00'},
            'GetVolume': {'CurrentVolume': self.volume}, 'GetMute': {'CurrentMute': int(self.mute)},
            'GetProtocolInfo': {'Source': '', 'Sink': 'http-get:*:audio/flac:*,http-get:*:audio/mpeg:*,http-get:*:video/mp4:*'},
        }.get(action, {})
        result = ''.join(f'<{key}>{html.escape(str(value))}</{key}>' for key, value in output.items())
        return web.Response(text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            f'<u:{action}Response xmlns:u="{service}">{result}</u:{action}Response></s:Body></s:Envelope>', content_type='text/xml')

    def mutations(self):
        return [c for c in self.commands if not c.startswith('Get')]


class DlnaRendererTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        operation.set(None)
        self.session = aiohttp.ClientSession()
        self.rooms, self.servers, self.backings, self.nodes, self.rows = [], [], [], [], []
        for index in range(2):
            backing = Backing(f'uuid:synthetic-zone-{index}')
            await backing.start()
            config = {'id': f'synthetic-room-{index}', 'name': f'Synthetic {index}', 'dlna_port': free_port()}
            row = {'roomId': config['id'], 'assigned': False, 'endpoint': None,
                   'rendererIds': [f'physical-left-{index}', f'physical-right-{index}'], 'spotifyVersion': 0}
            endpoint = {'roomId': config['id'], 'rendererId': backing.identity,
                        'descriptionUrl': str(backing.server.make_url('/device.xml'))}
            node = MagicMock()
            node.lookup = AsyncMock(side_effect=lambda _, row=row: dict(row))
            node.confirm_sources, node.close = AsyncMock(), AsyncMock()
            async def create(_, row=row, endpoint=endpoint):
                row.update(assigned=True, endpoint=endpoint)
            node.create_for_play = AsyncMock(side_effect=create)
            backend = DirectBackend(node, config['id'], '', poll_seconds=1000)
            room = Room(config, backend)
            backend.on_external_playback(lambda room=room: retired(room))
            await backend.connect()
            server = RendererServer(device_class(room, BRIDGE_ID), source=('127.0.0.1', 0), http_port=config['dlna_port'])
            await server.start(ssdp=False)
            self.rooms.append(room); self.servers.append(server); self.backings.append(backing)
            self.nodes.append(node); self.rows.append(row)

    async def asyncTearDown(self):
        for server in self.servers:
            await server.stop()
        for room in self.rooms:
            await room.backend.disconnect()
        for backing in self.backings:
            await backing.server.close()
        await self.session.close()
        operation.set(None)

    async def soap(self, action, index=0, service='AVTransport', expected=200, **args):
        if service != 'ConnectionManager':
            args = {'InstanceID': 0, **args}
        ns = f'urn:schemas-upnp-org:service:{service}:1'
        arguments = ''.join(f'<{key}>{html.escape(str(value))}</{key}>' for key, value in args.items())
        body = '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        body += f'<u:{action} xmlns:u="{ns}">{arguments}</u:{action}></s:Body></s:Envelope>'
        async with self.session.post(self.servers[index].base_uri + f'/{service}/control',
                data=body, headers={'SOAPAction': f'"{ns}#{action}"'}) as response:
            text = await response.text()
            self.assertEqual(response.status, expected, text)
            root = ET.fromstring(text)
            return {e.tag.split('}')[-1]: e.text or '' for e in root.iter() if len(e) == 0}

    async def prepare(self, index=0):
        uri = str(self.backings[index].server.make_url('/audio.flac'))
        metadata = DIDL.format(html.escape(uri))
        await self.soap('SetAVTransportURI', index, CurrentURI=uri, CurrentURIMetaData=metadata)
        return uri, metadata

    async def start(self, index=0):
        await self.prepare(index)
        await self.soap('Play', index, Speed='1')

    async def test_descriptions_discovery_stable_room_identity_and_stereo_pairs(self):
        identities = []
        for index, server in enumerate(self.servers):
            async with self.session.get(server.base_uri + '/device.xml') as response:
                root = ET.fromstring(await response.read())
                ns = {'d': 'urn:schemas-upnp-org:device-1-0'}
                identities.append(root.findtext('d:device/d:UDN', namespaces=ns))
                self.assertEqual(root.findtext('d:device/d:friendlyName', namespaces=ns), f'Synthetic {index} DLNA')
                self.assertEqual(len(root.findall('d:device/d:deviceList/d:device', ns)), 0)
            device = server._device
            search = SsdpSearchResponder(device, source=('127.0.0.1', 0))
            for target in ['ssdp:all', device.udn, 'upnp:rootdevice', device.device_type, AVT]:
                packets = search._build_responses(CaseInsensitiveDict({'ST': target}))
                self.assertTrue(packets)
                self.assertTrue(all(device.udn.encode() in packet and b'LOCATION:' in packet for packet in packets))
            self.assertEqual(search._build_responses(CaseInsensitiveDict({'ST': 'unknown'})), [])
            alive = _build_advertisements(('239.255.255.250', 1900), device)
            self.assertEqual(len(alive), 6)
            self.assertEqual({p['USN'].split('::')[0] for p in alive}, {device.udn})
        self.assertEqual(len(set(identities)), 2)
        self.assertEqual(identities[0], udn(BRIDGE_ID, self.rooms[0].config['id']))
        self.assertEqual(len(self.rows[0]['rendererIds']), 2)  # One logical stereo pair, one bridge.

    async def test_scpds_all_declared_actions_have_implementations(self):
        for name, actions in [('AVTransport', AV_ACTIONS), ('RenderingControl', RC_ACTIONS), ('ConnectionManager', CM_ACTIONS)]:
            async with self.session.get(self.servers[0].base_uri + f'/{name}/scpd.xml') as response:
                root = ET.fromstring(await response.read())
            ns = {'s': 'urn:schemas-upnp-org:service-1-0'}
            self.assertEqual({a.text for a in root.findall('s:actionList/s:action/s:name', ns)}, set(actions))
            variables = {a.text for a in root.findall('s:serviceStateTable/s:stateVariable/s:name', ns)}
            self.assertTrue(all(a.text in variables for a in root.findall('.//s:relatedStateVariable', ns)))

    async def test_prepare_status_and_events_never_create_zones_or_take_over(self):
        await self.prepare()
        for action in ['GetTransportInfo', 'GetPositionInfo', 'GetMediaInfo', 'GetDeviceCapabilities', 'GetTransportSettings', 'GetCurrentTransportActions']:
            await self.soap(action)
        for action in ['GetProtocolInfo', 'GetCurrentConnectionIDs']:
            await self.soap(action, service='ConnectionManager')
        for node in self.nodes:
            node.create_for_play.assert_not_awaited()
        self.assertEqual(self.backings[0].mutations(), [])
        self.assertEqual(self.backings[0].state, 'PLAYING')
        self.assertEqual(self.backings[0].uri, 'spotify:baseline')

    async def test_real_soap_play_pause_stop_resume_seek_direct_bytes_and_metadata(self):
        uri, metadata = await self.prepare()
        await self.soap('Play', Speed='1')
        self.nodes[0].create_for_play.assert_awaited_once()
        self.assertEqual(self.backings[0].metadata, metadata)
        self.assertEqual(self.backings[0].media_requests, ['bytes=0-'])
        await self.soap('Seek', Unit='REL_TIME', Target='00:01:17')
        info = await self.soap('GetPositionInfo')
        self.assertEqual(info['RelTime'], '00:01:17')
        self.assertEqual(info['TrackDuration'], '00:05:00')
        await self.soap('Pause')
        await self.soap('Play', Speed='1')
        await self.soap('Stop')
        await self.soap('Play', Speed='1')
        self.assertEqual(self.backings[0].state, 'PLAYING')
        self.assertEqual(self.backings[0].mutations(), ['SetAVTransportURI', 'Play', 'Seek', 'Pause', 'Play', 'Stop', 'Play'])

    async def test_paused_and_prepared_seek_are_silent_and_not_reported_as_applied(self):
        await self.prepare()
        await self.soap('Seek', Unit='ABS_TIME', Target='00:00:31')
        self.assertEqual(self.backings[0].mutations(), [])
        info = await self.soap('GetPositionInfo')
        self.assertEqual(info['RelTime'], '00:00:00')
        await self.soap('Play', Speed='1')
        self.assertEqual(self.backings[0].position, '00:00:31')
        self.assertEqual(self.backings[0].mutations(), ['SetAVTransportURI', 'Seek', 'Play'])
        await self.soap('Pause')
        before = self.backings[0].mutations()
        await self.soap('Seek', Unit='REL_TIME', Target='00:01:40')
        self.assertEqual(self.backings[0].mutations(), before)
        self.assertEqual(self.backings[0].state, 'PAUSED_PLAYBACK')
        self.assertEqual((await self.soap('GetPositionInfo'))['RelTime'], '00:00:31')
        await self.soap('Play', Speed='1')
        self.assertEqual((await self.soap('GetPositionInfo'))['RelTime'], '00:01:40')

    async def test_unsupported_and_invalid_actions_return_upnp_errors(self):
        self.assertEqual((await self.soap('Next', expected=500))['errorCode'], '401')
        self.assertEqual((await self.soap('GetPositionInfo', InstanceID=1, expected=500))['errorCode'], '718')
        self.assertEqual((await self.soap('SetAVTransportURI', CurrentURI='file:///etc/passwd', CurrentURIMetaData='', expected=500))['errorCode'], '714')
        self.assertEqual((await self.soap('Seek', Unit='TRACK_NR', Target='1', expected=500))['errorCode'], '710')
        self.assertEqual((await self.soap('SetPlayMode', NewPlayMode='REPEAT_ALL', expected=500))['errorCode'], '712')
        self.assertEqual((await self.soap('Play', expected=500))['errorCode'], '402')

    async def test_failed_position_observation_is_not_reported_as_ok(self):
        await self.start()
        self.backings[0].failure = 'GetPositionInfo'
        info = await self.soap('GetTransportInfo')
        self.assertEqual(info['CurrentTransportStatus'], 'ERROR_OCCURRED')

    async def test_prepared_item_does_not_report_previous_items_playing_state(self):
        await self.start()
        uri = str(self.backings[0].server.make_url('/audio.flac')) + '?next=1'
        await self.soap('SetAVTransportURI', CurrentURI=uri, CurrentURIMetaData=DIDL.format(html.escape(uri)))
        self.assertEqual((await self.soap('GetTransportInfo'))['CurrentTransportState'], 'STOPPED')
        self.assertEqual((await self.soap('GetPositionInfo'))['RelTime'], '00:00:00')
        self.assertEqual(self.backings[0].mutations(), ['SetAVTransportURI', 'Play'])

    async def test_invalid_didl_and_unsupported_media_do_not_mutate(self):
        uri = str(self.backings[0].server.make_url('/audio.flac'))
        await self.soap('SetAVTransportURI', CurrentURI=uri, CurrentURIMetaData='<!DOCTYPE x [<!ENTITY s SYSTEM "file:///etc/passwd">]><x>&s;</x>', expected=500)
        await self.soap('SetAVTransportURI', CurrentURI='http://audio.example.test/video.mp4', CurrentURIMetaData='', expected=500)
        self.assertEqual(self.backings[0].mutations(), [])
        self.nodes[0].create_for_play.assert_not_awaited()

    async def test_isolation_volume_mute_and_actual_readback(self):
        await self.start(0)
        before = list(self.backings[0].mutations())
        await self.start(1)
        await self.soap('SetVolume', 1, 'RenderingControl', Channel='Master', DesiredVolume=28)
        await self.soap('SetMute', 1, 'RenderingControl', Channel='Master', DesiredMute=1)
        await self.soap('Pause', 1)
        self.assertEqual(self.backings[0].mutations(), before)
        self.assertEqual(self.backings[0].state, 'PLAYING')
        self.assertEqual((await self.soap('GetVolume', 1, 'RenderingControl', Channel='Master'))['CurrentVolume'], '28')
        self.assertEqual((await self.soap('GetMute', 1, 'RenderingControl', Channel='Master'))['CurrentMute'], '1')

    async def test_missing_renderer_and_rediscovery_keep_target_without_background_repair(self):
        await self.start()
        identity = self.servers[0]._device.udn
        endpoint = self.rows[0]['endpoint']
        self.rows[0]['endpoint'] = None
        await self.rooms[0].refresh()
        await self.soap('GetTransportInfo')
        self.assertEqual(self.servers[0]._device.udn, identity)
        self.nodes[0].create_for_play.assert_awaited_once()
        before = self.backings[0].mutations()
        self.rows[0]['endpoint'] = dict(endpoint, descriptionUrl=str(self.backings[0].server.make_url('/changed.xml')))
        await self.rooms[0].refresh()
        self.assertEqual(self.backings[0].mutations(), before)
        await self.soap('Play', Speed='1')
        self.assertEqual(self.servers[0]._device.udn, identity)

    async def test_spotify_and_other_uri_retire_old_controls(self):
        await self.start()
        self.rows[0]['spotifyVersion'] += 1
        self.backings[0].uri = 'spotify:new-selection'
        self.backings[0].state = 'PLAYING'
        await self.rooms[0].refresh()
        before = self.backings[0].mutations()
        await self.soap('Pause', expected=500)
        await self.soap('SetVolume', service='RenderingControl', Channel='Master', DesiredVolume=10, expected=500)
        await self.soap('Stop')  # Logical no-op after terminal retirement.
        self.assertEqual(self.backings[0].mutations(), before)
        await self.soap('Play', Speed='1')  # Fresh explicit takeover, not a lease.
        self.backings[0].uri = 'http://other-controller.example.test/audio'
        await self.rooms[0].refresh()
        self.assertIsNone(self.rooms[0].loaded_uri)

    async def test_source_change_during_load_blocks_late_play(self):
        await self.prepare()
        async def hook(action):
            if action == 'SetAVTransportURI':
                self.rows[0]['spotifyVersion'] += 1
        self.backings[0].hook = hook
        await self.soap('Play', Speed='1', expected=500)
        self.assertEqual(self.backings[0].mutations(), ['SetAVTransportURI'])

    async def test_failed_mutation_not_retried_and_failed_seek_not_reported(self):
        await self.start()
        self.backings[0].failure, self.backings[0].failure_code = 'Seek', 710
        await self.soap('Seek', Unit='ABS_TIME', Target='00:01:11', expected=500)
        self.assertEqual(self.backings[0].mutations().count('Seek'), 1)
        self.assertEqual((await self.soap('GetPositionInfo'))['RelTime'], '00:00:00')
        self.backings[0].failure = 'Pause'
        await self.soap('Pause', expected=500)
        self.assertEqual(self.backings[0].mutations().count('Pause'), 1)
        before = self.backings[0].mutations()
        await self.rooms[0].backend.disconnect()
        self.assertEqual(self.backings[0].mutations(), before)

    async def test_grouped_limitation_and_precise_protocol_capabilities(self):
        self.nodes[0].lookup.side_effect = RendererRequestError(409, {'error': 'grouped_room_not_supported'})
        await self.rooms[0].refresh()
        self.assertEqual(self.rooms[0].limitation, 'grouped_room_not_supported')
        self.assertEqual((await self.soap('GetProtocolInfo', service='ConnectionManager'))['Sink'], '')
        self.nodes[0].create_for_play.assert_not_awaited()
        await self.start(1)
        sink = (await self.soap('GetProtocolInfo', 1, 'ConnectionManager'))['Sink']
        self.assertEqual(sink, 'http-get:*:audio/flac:*,http-get:*:audio/mpeg:*')
        self.assertEqual(protocols('http-get:*:*:*,http-get:*:video/mp4:*'), '')

    async def test_gena_initial_change_physical_volume_renew_expire_unsubscribe(self):
        await self.start()
        notifications = []
        async def notify(request):
            notifications.append((dict(request.headers), await request.text()))
            return web.Response()
        app = web.Application()
        app.router.add_route('NOTIFY', '/callback', notify)
        callback = TestServer(app)
        await callback.start_server()
        try:
            event_url = self.servers[0].base_uri + '/RenderingControl/event'
            async with self.session.request('SUBSCRIBE', event_url, headers={
                    'CALLBACK': '<' + str(callback.make_url('/callback')) + '>', 'NT': 'upnp:event', 'TIMEOUT': 'Second-60'}) as response:
                self.assertEqual(response.status, 200)
                sid = response.headers['SID']
                self.assertTrue(sid.startswith('uuid:'))
                self.assertEqual(response.headers['TIMEOUT'], 'Second-60')
            await asyncio.sleep(.05)
            self.assertTrue(notifications)
            self.assertEqual(notifications[0][0]['SEQ'], '0')
            self.backings[0].volume = 43  # Physical knob, no controller SetVolume.
            await self.rooms[0].refresh()
            self.rooms[0].changed()
            await asyncio.sleep(.05)
            self.assertTrue(any('val="43"' in html.unescape(body) for _, body in notifications))
            self.assertEqual(len(self.backings[0].mutations()), 2)
            async with self.session.request('SUBSCRIBE', event_url, headers={'SID': sid, 'TIMEOUT': 'Second-120'}) as response:
                self.assertEqual(response.status, 200)
            rcs = self.servers[0]._device.service(RCS)
            rcs.get_subscriber(sid)._expires = datetime.now() - timedelta(seconds=1)
            async with self.session.request('SUBSCRIBE', event_url, headers={'SID': sid}) as response:
                self.assertEqual(response.status, 412)
            async with self.session.request('SUBSCRIBE', event_url, headers={
                    'CALLBACK': '<' + str(callback.make_url('/callback')) + '>', 'NT': 'upnp:event'}) as response:
                self.assertEqual(response.status, 200)
                sid = response.headers['SID']
            async with self.session.request('UNSUBSCRIBE', event_url, headers={'SID': sid}) as response:
                self.assertEqual(response.status, 200)
            async with self.session.request('SUBSCRIBE', event_url, headers={'SID': sid}) as response:
                self.assertEqual(response.status, 412)
            async with self.session.request('SUBSCRIBE', event_url, headers={
                    'CALLBACK': '<http://192.0.2.99/callback>', 'NT': 'upnp:event'}) as response:
                self.assertEqual(response.status, 412)
        finally:
            await callback.close()


if __name__ == '__main__':
    unittest.main()

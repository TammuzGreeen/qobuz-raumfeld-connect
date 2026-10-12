"""Deterministic, identical fake DLNA transport beneath stock and room backends.

Only the SOAP network boundary is replaced. Command handler, player, queue,
DLNA/client parsing and (for room mode) source checks are real implementations.
No sleeps/timing windows pair messages: Event gates hold an exact await boundary.
"""
import asyncio
import html
import json
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from qobuz_proxy.backends.dlna.backend import DLNABackend
from qobuz_proxy.backends.dlna.client import DLNAClient, DLNADeviceInfo, SoapResult
from qobuz_proxy.playback import QobuzPlayer, QobuzQueue
from qobuz_proxy.playback.command_handler import PlaybackCommandHandler
from qobuz_proxy.playback.play_reporter import PlayReporter
from qobuz_proxy.connect.protocol import payload_pb2

from qobuz.endpoint_catalog import Endpoint
from qobuz.upstream_backend import DeferredRoomBackend, RoomDLNAClient, operation

entered_apply = ContextVar('replay_apply_entry', default=None)


class FakeRenderer:
    def __init__(self):
        self.uri = ''
        self.state = 'STOPPED'
        self.writes = []
        self.gate_action = None
        self.gate_n = 0
        self.entered, self.proceed = asyncio.Event(), asyncio.Event()

    def gate(self, action, occurrence=1):
        self.gate_action, self.gate_n = action, occurrence

    async def soap(self, action, args):
        if action == self.gate_action:
            self.gate_n -= 1
            if self.gate_n == 0:
                self.entered.set()
                await self.proceed.wait()
                self.gate_action = None
        if not action.startswith('Get'):
            self.writes.append((action, args.get('CurrentURI')))
        if action == 'SetAVTransportURI':
            self.uri = args['CurrentURI']
        elif action == 'Play':
            self.state = 'PLAYING'
        elif action == 'Pause':
            self.state = 'PAUSED_PLAYBACK'
        elif action == 'Stop':
            self.state = 'STOPPED'
        fields = {'GetMediaInfo': f'<CurrentURI>{html.escape(self.uri)}</CurrentURI>',
                  'GetTransportInfo': f'<CurrentTransportState>{self.state}</CurrentTransportState>',
                  'GetPositionInfo': '<RelTime>00:00:01</RelTime>',
                  'GetVolume': '<CurrentVolume>5</CurrentVolume>'}.get(action, '')
        return SoapResult(success=True, body='<root>' + fields + '</root>')


async def fake_soap(client, url, service, action, args, max_retries=None):
    return await client.test_renderer.soap(action, args)


def state_message(track=None, playing=None, position=None, queue=True):
    msg = payload_pb2.QConnectMessage()
    state = msg.srvrRndrSetState
    if track is not None:
        state.currentQueueItem.trackId = int(track)
        state.currentQueueItem.queueItemId = int(track)
    if playing is not None:
        state.playingState = playing
    if position is not None:
        state.currentPosition = position
    if queue:
        state.nextQueueItem.trackId = 103
        state.nextQueueItem.queueItemId = 103
        state.queueVersion.major = 1
        state.queueVersion.minor = 1
    return msg


class Replay:
    def __init__(self, room=False, player_class=QobuzPlayer):
        operation.set(None)
        self.renderer = FakeRenderer()
        endpoint_row = {'roomId': 'synthetic-room', 'rendererId': 'synthetic-zone',
                        'descriptionUrl': 'http://renderer.example.test/device.xml'}
        self.row = {'roomId': 'synthetic-room', 'assigned': True, 'endpoint': endpoint_row,
                    'rendererIds': ['synthetic-physical'], 'spotifyVersion': 0}
        if room:
            companion = SimpleNamespace(lookup=AsyncMock(side_effect=lambda _: dict(self.row)))
            self.backend = DeferredRoomBackend(companion, 'synthetic-room', 'Synthetic')
            self.backend.endpoint = Endpoint.parse('synthetic-room', endpoint_row)
            self.backend.epoch = 1
            self.backend.spotify_version = 0
            self.client = RoomDLNAClient(self.backend, self.backend.endpoint, 1)
            self.client.seek_declared = True  # Synthetic validated SCPD; no network.
        else:
            self.backend = DLNABackend('renderer.example.test', name='Synthetic')
            self.client = DLNAClient('renderer.example.test')
        self.client.test_renderer = self.renderer
        self.client.device_info = DLNADeviceInfo(av_transport_url='http://renderer.example.test/soap',
                                               rendering_control_url='http://renderer.example.test/soap')
        self.backend._client = self.client
        metadata = MagicMock()
        metadata.get_streaming_url = AsyncMock(side_effect=lambda t: self.url(t))
        metadata.get_metadata = AsyncMock(return_value=None)
        metadata.get_track_format.return_value = (6, 44100, 16)
        metadata.get_track_actual_quality.return_value = 6
        metadata.get_track_blob.return_value = 'synthetic'
        self.api = SimpleNamespace(report_streaming_start=AsyncMock(return_value=True),
                                   report_streaming_end=AsyncMock(return_value=True))
        self.player = player_class(queue=QobuzQueue(), metadata_service=metadata, backend=self.backend,
                                  play_reporter=PlayReporter(self.api))
        self.handler = PlaybackCommandHandler(self.player)
        self.room = room
        if room:
            self.backend.bind_player(self.player)
            self.backend.bind_authority(lambda: (self.handler._generation, self.handler._active))
            self.backend.prepare_for_selection()
        self.player.set_playback_permission_check(self.backend.can_apply_remote_state)
        self.backend.on_external_playback(self.player.release_external_playback)
        self.handler._note_active(True)
        original_apply = self.player.apply_remote_state
        async def observe_entry(**kwargs):
            marker = entered_apply.get()
            if marker is not None:
                marker.set()
            return await original_apply(**kwargs)
        self.player.apply_remote_state = observe_entry
        operation.set(None)  # Driver is not an old handler/invalidation callback.

    @staticmethod
    def url(track):
        return f'http://audio.example.test/{track}.flac'

    def dispatch(self, msg):
        entered = asyncio.Event()
        entry_token = entered_apply.set(entered)
        token = operation.set(self.backend.capture_operation()) if self.room else None
        try:
            task = self.handler.dispatch_message(41, msg)
            task.test_entered = entered
            return task
        finally:
            entered_apply.reset(entry_token)
            if token is not None:
                operation.reset(token)

    async def start(self, paused=False):
        await self.dispatch(state_message('101', 2))
        if paused:
            await self.dispatch(state_message(playing=3, queue=False))
        self.backend._playback_started_at = 0  # Initial-source grace is irrelevant to Next.
        self.renderer.writes.clear()

    def result(self):
        track = self.player.current_track
        return {'current': track.track_id if track else None, 'observed': self.player.state.name,
                'renderer': self.renderer.state,
                'writes': [a for a, _ in self.renderer.writes],
                'newTrackPlayed': any(a == 'SetAVTransportURI' and u == self.url('102')
                                      for a, u in self.renderer.writes)}


async def captured_sequence(room, reverse=False, player_class=QobuzPlayer):
    replay = Replay(room, player_class)
    with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
        await replay.start()
        if reverse:
            await replay.dispatch(state_message(playing=2, queue=False))
            await replay.dispatch(state_message('102'))
        else:
            replay.renderer.gate('GetMediaInfo', 2)  # permission read, then pre-Stop ownership read
            selected = replay.dispatch(state_message('102'))
            await asyncio.wait_for(replay.renderer.entered.wait(), 5)
            played = replay.dispatch(state_message(playing=2, queue=False))
            await asyncio.wait_for(played.test_entered.wait(), 5)
            replay.renderer.proceed.set()
            await asyncio.wait_for(asyncio.gather(selected, played), 5)
        return replay.result()


async def main():
    results = {}
    for room in (False, True):
        for reverse in (False, True):
            key = ('room' if room else 'stock') + ('_play_then_item' if reverse else '_item_then_play')
            results[key] = await captured_sequence(room, reverse)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    asyncio.run(main())

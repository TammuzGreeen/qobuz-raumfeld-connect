"""Standalone upstream contribution tests; requires only qobuz-proxy."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from qobuz_proxy.backends.base import AudioBackend
from qobuz_proxy.backends.types import PlaybackState
from qobuz_proxy.connect.protocol import payload_pb2
from qobuz_proxy.playback import QobuzPlayer, QobuzQueue
from qobuz_proxy.playback.command_handler import PlaybackCommandHandler


class PartialPlaybackStateTests(unittest.IsolatedAsyncioTestCase):
    def setup_player(self):
        self.backend = MagicMock(spec=AudioBackend)
        self.backend.needs_url = True
        self.backend.state = PlaybackState.STOPPED
        self.writes = []
        async def play(url, metadata):
            self.writes.append(('play', metadata.track_id))
            self.backend.state = PlaybackState.PLAYING
            return True
        async def stop(**kwargs):
            self.writes.append(('stop', None))
            self.backend.state = PlaybackState.STOPPED
            return True
        async def pause():
            self.writes.append(('pause', None))
            self.backend.state = PlaybackState.PAUSED
            return True
        self.backend.play = AsyncMock(side_effect=play)
        self.backend.stop = AsyncMock(side_effect=stop)
        self.backend.pause = AsyncMock(side_effect=pause)
        self.backend.resume = AsyncMock(return_value=True)
        self.backend.seek = AsyncMock(return_value=True)
        self.backend.get_position = AsyncMock(return_value=0)
        self.backend.can_apply_remote_state = AsyncMock(return_value=True)
        metadata = MagicMock()
        metadata.get_streaming_url = AsyncMock(side_effect=lambda t: f'http://audio.example.test/{t}')
        metadata.get_metadata = AsyncMock(return_value=None)
        metadata.get_track_format.return_value = (6, 44100, 16)
        self.player = QobuzPlayer(queue=QobuzQueue(), metadata_service=metadata, backend=self.backend)
        self.player.set_playback_permission_check(self.backend.can_apply_remote_state)
        self.handler = PlaybackCommandHandler(self.player)
        self.handler._note_active(True)

    def dispatch(self, track=None, mode=None, position=None):
        message = payload_pb2.QConnectMessage()
        state = message.srvrRndrSetState
        if track is not None:
            state.currentQueueItem.trackId = int(track)
            state.currentQueueItem.queueItemId = int(track)
        if mode is not None:
            state.playingState = mode
        if position is not None:
            state.currentPosition = position
        return self.handler.dispatch_message(41, message)

    async def test_both_orders_and_paused_selection(self):
        for reverse in (False, True):
            self.setup_player()
            await self.dispatch('101', 2)
            if reverse:
                await self.dispatch(mode=2)
            await self.dispatch('102')
            if not reverse:
                await self.dispatch(mode=2)
            self.assertEqual(self.writes.count(('play', '102')), 1)
            await self.dispatch(mode=3)
            await self.dispatch('103')
            self.assertNotIn(('play', '103'), self.writes)

    async def test_pending_target_survives_state_only_play_before_lock(self):
        self.setup_player()
        await self.dispatch('101', 2)
        await self.player._playback_lock.acquire()
        first = self.dispatch('102')
        # Entry gate rather than a wall-clock pairing window.
        entered = asyncio.Event()
        original = self.player._apply_remote_state
        async def observed(**kwargs):
            entered.set()
            return await original(**kwargs)
        self.player._apply_remote_state = observed
        second = self.dispatch(mode=2)
        await asyncio.wait_for(entered.wait(), 5)
        self.player._playback_lock.release()
        await asyncio.gather(first, second)
        self.assertEqual(self.player.current_track.track_id, '102')
        self.assertEqual(self.writes.count(('play', '102')), 1)

    async def test_pause_stop_and_rapid_selection_during_metadata(self):
        for mode, target in ((1, None), (3, None), (None, '103')):
            self.setup_player()
            await self.dispatch('101', 2)
            entered, proceed, superseded = asyncio.Event(), asyncio.Event(), asyncio.Event()
            async def stream(track):
                entered.set()
                await proceed.wait()
                return f'http://audio.example.test/{track}'
            self.player.metadata.get_streaming_url = AsyncMock(side_effect=stream)
            original = self.player._apply_remote_state
            async def observed(**kwargs):
                if entered.is_set():
                    superseded.set()
                return await original(**kwargs)
            self.player._apply_remote_state = observed
            first = self.dispatch('102')
            await asyncio.wait_for(entered.wait(), 5)
            second = self.dispatch(target, mode)
            await asyncio.wait_for(superseded.wait(), 5)
            proceed.set()
            await asyncio.gather(first, second)
            self.assertNotIn(('play', '102'), self.writes)
            if target:
                self.assertIn(('play', target), self.writes)

    async def test_invalidation_and_startup_do_not_invent_play(self):
        self.setup_player()
        await self.dispatch('101')
        self.assertEqual(self.writes, [])
        await self.dispatch(mode=2)
        self.player.invalidate_pending_commands()
        await self.dispatch('102')
        self.assertNotIn(('play', '102'), self.writes)
        self.assertIsNone(self.player._requested_playing_state)


if __name__ == '__main__':
    unittest.main()

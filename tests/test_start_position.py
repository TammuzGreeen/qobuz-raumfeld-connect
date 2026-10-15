"""Split handoff snapshots through actual pinned protobuf/handler/player/DLNA."""
import asyncio
import unittest
from unittest.mock import patch

from qobuz_proxy.backends.dlna.client import DLNAClient
from qobuz_proxy.backends.types import PlaybackState
from qobuz.upstream_backend import operation
from upstream_partial_replay import fake_soap, state_message
from test_upstream_seek import make_replay


async def handoff(replay):
    await replay.start()
    replay.renderer.uri = 'spotify:synthetic-takeover'
    replay.row['spotifyVersion'] += 1
    await replay.backend.refresh()
    replay.handler.note_disconnected()
    replay.backend.prepare_for_selection()
    replay.handler._note_active(True)
    operation.set(None)
    replay.renderer.writes.clear()
    replay.renderer.seek_requests.clear()


class StartPositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_handoff_snapshot_retains_offset_until_separate_play(self):
        replay = make_replay(True)
        with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
            await handoff(replay)
            await replay.dispatch(state_message('101', 3, 60000))
            self.assertEqual(replay.renderer.writes, [])
            self.assertLess(replay.player.current_position_ms, 10000)
            await replay.dispatch(state_message())  # queue feedback is not intent
            await replay.dispatch(state_message(playing=2, queue=False))
        self.assertEqual(replay.result()['writes'], ['SetAVTransportURI', 'Play', 'Seek'])
        self.assertEqual(replay.renderer.position_ms, 60000)
        self.assertEqual(len(replay.renderer.seek_requests), 1)
        self.assertEqual(replay.renderer.seek_requests[0]['Unit'], 'ABS_TIME')

    async def test_initial_load_position_before_play_and_play_before_position(self):
        for room in (False, True):
            for reverse in (False, True):
                replay = make_replay(room)
                with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                    if reverse:
                        await replay.dispatch(state_message('101', 2))
                        await replay.dispatch(state_message(position=60000, queue=False))
                    else:
                        await replay.dispatch(state_message('101', 3, 60000))
                        self.assertEqual(replay.renderer.writes, [])
                        await replay.dispatch(state_message(playing=2, queue=False))
                self.assertEqual(replay.result()['writes'], ['SetAVTransportURI', 'Play', 'Seek'])
                self.assertEqual(replay.renderer.position_ms, 60000)

    async def test_latest_start_position_including_zero_and_clamping(self):
        for target, expected in ((0, 0), (90000, 90000), (999999, 239000)):
            replay = make_replay(True)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.dispatch(state_message('101', 3, 60000))
                await replay.dispatch(state_message(position=target, queue=False))
                self.assertEqual(replay.renderer.writes, [])
                await replay.dispatch(state_message(playing=2, queue=False))
            self.assertEqual(replay.result()['writes'], ['SetAVTransportURI', 'Play'] + (['Seek'] if target else []))
            if target:
                self.assertEqual(replay.renderer.position_ms, expected)
            self.assertIsNone(replay.player._pending_start_position_ms)

    async def test_nonzero_start_rejection_is_not_success_or_retried_on_play(self):
        replay = make_replay(True, reject=True)
        with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
            await replay.dispatch(state_message('101', 3, 60000))
            await replay.dispatch(state_message(playing=2, queue=False))
            await replay.dispatch(state_message(playing=2, queue=False))
        self.assertEqual(replay.result()['writes'], ['SetAVTransportURI', 'Play', 'Seek'])
        self.assertEqual(replay.player.state, PlaybackState.PLAYING)
        self.assertLess(replay.player.current_position_ms, 10000)
        self.assertIsNone(replay.player._pending_start_position_ms)

    async def test_pending_start_does_not_survive_stop_selection_source_loss_or_occurrence(self):
        for invalidation in ('stop', 'selection', 'release', 'disconnect', 'endpoint', 'track', 'occurrence'):
            replay = make_replay(True)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.dispatch(state_message('101', 3, 60000))
                self.assertEqual(replay.player._pending_start_position_ms, 60000)
                if invalidation == 'stop':
                    await replay.dispatch(state_message(playing=1, queue=False))
                elif invalidation == 'selection':
                    replay.backend.prepare_for_selection()
                elif invalidation == 'release':
                    await replay.backend.release_source()
                elif invalidation == 'disconnect':
                    replay.handler.note_disconnected()
                elif invalidation == 'endpoint':
                    await replay.backend.rebind(None)
                elif invalidation == 'track':
                    await replay.dispatch(state_message('102', 3))
                else:
                    msg = state_message('101', 3)
                    msg.srvrRndrSetState.currentQueueItem.queueItemId = 201
                    await replay.dispatch(msg)
                self.assertIsNone(replay.player._pending_start_position_ms, invalidation)
                self.assertNotIn('Seek', replay.result()['writes'])

    async def test_late_position_from_old_selection_cannot_seed_new_start(self):
        replay = make_replay(True)
        with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
            await replay.dispatch(state_message('101', 3, 60000))
            await replay.player._playback_lock.acquire()
            task = replay.dispatch(state_message(position=90000, queue=False))
            await asyncio.wait_for(task.test_entered.wait(), 5)
            replay.handler.note_disconnected()
            replay.backend.prepare_for_selection()
            replay.handler._note_active(True)
            operation.set(None)
            replay.player._playback_lock.release()
            await asyncio.wait_for(task, 5)
            self.assertIsNone(replay.player._pending_start_position_ms)
            await replay.dispatch(state_message('101', 2))
        self.assertNotIn('Seek', replay.result()['writes'])

    async def test_pending_offset_in_room_a_cannot_seed_room_b(self):
        a, b = make_replay(True), make_replay(True)
        with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
            await a.dispatch(state_message('101', 3, 60000))
            await b.dispatch(state_message('101', 2))
            self.assertEqual(a.player._pending_start_position_ms, 60000)
            self.assertNotIn('Seek', b.result()['writes'])
            await a.dispatch(state_message(playing=2, queue=False))
        self.assertEqual(a.renderer.position_ms, 60000)
        self.assertEqual(len(a.renderer.seek_requests), 1)
        self.assertEqual(len(b.renderer.seek_requests), 0)

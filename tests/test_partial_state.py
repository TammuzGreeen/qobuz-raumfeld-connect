"""Same deterministic transport/input matrix for patched stock and room paths."""
import asyncio
import unittest
from unittest.mock import patch

from qobuz_proxy.backends.dlna.client import DLNAClient
from qobuz_proxy.backends.types import PlaybackState
from upstream_partial_replay import Replay, captured_sequence, fake_soap, state_message


class PartialStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_captured_orders_stock_and_room(self):
        for room in (False, True):
            for reverse in (False, True):
                with self.subTest(room=room, reverse=reverse):
                    result = await captured_sequence(room, reverse)
                    self.assertEqual(result['current'], '102')
                    self.assertEqual(result['renderer'], 'PLAYING')
                    self.assertEqual(result['writes'], ['Stop', 'SetAVTransportURI', 'Play'])

    async def test_item_selection_while_paused_never_invents_play(self):
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start(paused=True)
                await replay.dispatch(state_message('102'))
                self.assertEqual(replay.player.current_track.track_id, '102')
                self.assertEqual(replay.player._requested_playing_state, 3)
                self.assertNotIn('Play', replay.result()['writes'])
                self.assertNotIn('SetAVTransportURI', replay.result()['writes'])

    async def test_startup_selection_without_play_is_load_only(self):
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.dispatch(state_message('102'))
                self.assertEqual(replay.player.current_track.track_id, '102')
                self.assertIsNone(replay.player._requested_playing_state)
                self.assertEqual(replay.renderer.writes, [])

    async def test_internal_stop_does_not_erase_admitted_play_request(self):
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                entered, proceed = asyncio.Event(), asyncio.Event()
                original = replay.player.metadata.get_streaming_url
                async def metadata(track):
                    entered.set()
                    await proceed.wait()
                    return await original(track)
                replay.player.metadata.get_streaming_url = metadata
                task = replay.dispatch(state_message('102'))
                await asyncio.wait_for(entered.wait(), 5)
                try:
                    self.assertEqual(replay.renderer.state, 'STOPPED')
                    self.assertEqual(replay.player.state, PlaybackState.LOADING)
                    self.assertEqual(replay.player._requested_playing_state, 2)
                finally:
                    proceed.set()
                    await asyncio.wait_for(task, 5)
                self.assertEqual(replay.result()['writes'], ['Stop', 'SetAVTransportURI', 'Play'])

    async def test_explicit_pause_and_stop_during_load_override_play(self):
        for room in (False, True):
            for mode in (1, 3):
                replay = Replay(room)
                with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                    await replay.start()
                    replay.renderer.gate('GetMediaInfo', 2)
                    old = replay.dispatch(state_message('102'))
                    await asyncio.wait_for(replay.renderer.entered.wait(), 5)
                    new = replay.dispatch(state_message(playing=mode, queue=False))
                    await asyncio.wait_for(new.test_entered.wait(), 5)
                    replay.renderer.proceed.set()
                    await asyncio.wait_for(asyncio.gather(old, new), 5)
                    self.assertEqual(replay.player._requested_playing_state, mode)
                    self.assertNotIn('Play', replay.result()['writes'])
                    self.assertNotIn('SetAVTransportURI', replay.result()['writes'])

    async def test_rapid_selections_preserve_only_latest_target(self):
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                await replay.player._playback_lock.acquire()
                tasks = []
                try:
                    for msg in (state_message('102'), state_message(playing=2, queue=False), state_message('104')):
                        task = replay.dispatch(msg)
                        tasks.append(task)
                        await asyncio.wait_for(task.test_entered.wait(), 5)
                finally:
                    replay.player._playback_lock.release()
                await asyncio.wait_for(asyncio.gather(*tasks), 5)
                self.assertEqual(replay.player.current_track.track_id, '104')
                self.assertFalse(replay.result()['newTrackPlayed'])
                uris = [u for a, u in replay.renderer.writes if a == 'SetAVTransportURI']
                self.assertEqual(uris, [replay.url('104')])

    async def test_queue_only_feedback_does_not_supersede_load(self):
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                replay.renderer.gate('GetMediaInfo', 2)
                selected = replay.dispatch(state_message('102'))
                await asyncio.wait_for(replay.renderer.entered.wait(), 5)
                generation = replay.player._command_generation
                await replay.dispatch(state_message())
                self.assertEqual(replay.player._command_generation, generation)
                replay.renderer.proceed.set()
                await asyncio.wait_for(selected, 5)
                self.assertTrue(replay.result()['newTrackPlayed'])

    async def test_takeover_revokes_pending_target_and_play_intent(self):
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                replay.renderer.gate('GetMediaInfo', 2)
                selected = replay.dispatch(state_message('102'))
                await asyncio.wait_for(replay.renderer.entered.wait(), 5)
                replay.renderer.uri = 'spotify:synthetic-foreign-source'
                replay.renderer.state = 'PLAYING'
                if room:
                    replay.row['spotifyVersion'] += 1
                replay.renderer.proceed.set()
                await asyncio.wait_for(selected, 5)
                await replay.dispatch(state_message(playing=2, queue=False))
                self.assertEqual(replay.renderer.writes, [])
                self.assertEqual(replay.renderer.uri, 'spotify:synthetic-foreign-source')
                self.assertIsNone(replay.player._requested_playing_state)
                self.assertIsNone(replay.player._pending_remote_state)

    async def test_disconnect_clears_mode_before_new_selection(self):
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                replay.handler.note_disconnected()
                replay.handler._note_active(True)
                await replay.dispatch(state_message('102'))
                self.assertIsNone(replay.player._requested_playing_state)
                self.assertNotIn('Play', replay.result()['writes'])

    async def test_play_before_item_while_paused_and_before_lock(self):
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start(paused=True)
                await replay.dispatch(state_message(playing=2, queue=False))
                await replay.dispatch(state_message('102'))
                self.assertTrue(replay.result()['newTrackPlayed'])
                self.assertEqual(replay.renderer.state, 'PLAYING')
                await replay.player._playback_lock.acquire()
                tasks = []
                try:
                    for msg in (state_message('104'), state_message(playing=2, queue=False)):
                        task = replay.dispatch(msg)
                        tasks.append(task)
                        await asyncio.wait_for(task.test_entered.wait(), 5)
                finally:
                    replay.player._playback_lock.release()
                await asyncio.wait_for(asyncio.gather(*tasks), 5)
                self.assertEqual(replay.player.current_track.track_id, '104')
                uris = [u for a, u in replay.renderer.writes if a == 'SetAVTransportURI']
                self.assertEqual(uris, [replay.url('102'), replay.url('104')])

    async def test_pause_during_acknowledged_uri_transaction_finishes_paused(self):
        # Stock owns the serialized URI+Play transaction. A command arriving
        # after URI transmission is applied next, not a recovery retry.
        for room in (False, True):
            replay = Replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                replay.renderer.gate('SetAVTransportURI')
                first = replay.dispatch(state_message('102'))
                await asyncio.wait_for(replay.renderer.entered.wait(), 5)
                second = replay.dispatch(state_message(playing=3, queue=False))
                await asyncio.wait_for(second.test_entered.wait(), 5)
                replay.renderer.proceed.set()
                await asyncio.wait_for(asyncio.gather(first, second), 5)
                self.assertEqual(replay.player.state, PlaybackState.PAUSED)
                self.assertEqual(replay.player._requested_playing_state, 3)
                self.assertEqual(replay.renderer.state, 'PAUSED_PLAYBACK')
                self.assertEqual(replay.result()['writes'], ['Stop', 'SetAVTransportURI', 'Play', 'Pause'])

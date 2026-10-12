"""Real protobuf/handler/player/DLNA seek semantics on a synthetic transport."""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

from qobuz_proxy.backends.dlna.client import DLNAClient, SoapResult
from qobuz_proxy.backends.types import PlaybackState
from upstream_partial_replay import Replay, FakeRenderer, fake_soap, state_message


class SeekRenderer(FakeRenderer):
    def __init__(self, reject=False):
        super().__init__()
        self.reject = reject
        self.failure_code = 710
        self.position_ms = 1000
        self.seek_requests = []
        self.after_seek = None

    async def soap(self, action, args):
        result = await super().soap(action, args)
        if action == 'Seek':
            self.seek_requests.append(dict(args))
            if self.reject:
                return SoapResult(success=False, error_code=self.failure_code)
            parts = args['Target'].split(':')
            self.position_ms = (int(parts[0])*3600 + int(parts[1])*60 + int(parts[2]))*1000
            if self.after_seek:
                await self.after_seek()
        if action == 'GetPositionInfo':
            seconds = self.position_ms//1000
            return SoapResult(success=True,
                body=f'<root><RelTime>00:{seconds//60:02}:{seconds%60:02}</RelTime></root>')
        return result


def make_replay(room, reject=False):
    replay = Replay(room)
    replay.renderer = SeekRenderer(reject)
    replay.client.test_renderer = replay.renderer
    metadata = MagicMock()
    metadata.to_dict.return_value = {'title': 'Synthetic', 'duration_ms': 240000}
    replay.player.metadata.get_metadata.return_value = metadata
    return replay


class SeekTests(unittest.IsolatedAsyncioTestCase):
    async def test_playing_position_deltas_send_one_seek(self):
        for room in (False, True):
            for mode in (None, 2):
                replay = make_replay(room)
                with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                    await replay.start()
                    await replay.dispatch(state_message(playing=mode, position=60000, queue=False))
                self.assertEqual(replay.result()['writes'], ['Seek'])
                self.assertEqual(replay.renderer.seek_requests[0]['Unit'], 'ABS_TIME' if room else 'REL_TIME')
                self.assertEqual(replay.renderer.position_ms, 60000)
                self.assertGreaterEqual(replay.player.current_position_ms, 60000)

    async def test_rejected_seek_does_not_change_clock_or_retry(self):
        for room in (False, True):
            for paused in (False, True):
                replay = make_replay(room, reject=True)
                with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                    await replay.start(paused=paused)
                    self.assertFalse(await replay.player.seek(60000))
                    self.assertLess(replay.player.current_position_ms, 10000)
                    replay.renderer.writes.clear()
                    replay.renderer.seek_requests.clear()
                    await replay.dispatch(state_message(position=60000, queue=False))
                self.assertEqual(replay.result()['writes'], ['Seek'])
                self.assertLess(replay.player.current_position_ms, 10000)
                self.assertEqual(replay.renderer.position_ms, 1000)
                self.assertFalse(replay.backend._external_playback)

    async def test_paused_scrub_remains_paused_and_resume_does_not_reseek(self):
        for room in (False, True):
            replay = make_replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start(paused=True)
                await replay.dispatch(state_message(position=60000, queue=False))
                self.assertEqual(replay.player.state, PlaybackState.PAUSED)
                self.assertEqual(replay.result()['writes'], ['Seek'])
                await replay.dispatch(state_message(playing=2, queue=False))
            self.assertEqual(replay.result()['writes'], ['Seek', 'Play'])
            self.assertEqual(replay.renderer.position_ms, 60000)

    async def test_nonzero_start_is_reported_only_after_seek_ack(self):
        for room in (False, True):
            replay = make_replay(room)
            reported = []
            original = replay.player._send_state_update
            async def observe_report():
                reported.append((replay.player.current_position_ms, replay.renderer.position_ms))
                return await original()
            replay.player._send_state_update = observe_report
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.dispatch(state_message('101', 2, 60000))
            self.assertEqual(replay.result()['writes'], ['SetAVTransportURI', 'Play', 'Seek'])
            self.assertTrue(any(p >= 60000 for p, _ in reported))
            self.assertFalse(any(p >= 60000 and renderer != 60000 for p, renderer in reported))

    async def test_rejected_nonzero_start_returns_false_without_false_position(self):
        for room in (False, True):
            replay = make_replay(room, reject=True)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                self.assertFalse(await replay.player.play_track(queue_item_id=101, track_id='101', position_ms=60000))
            self.assertEqual(replay.result()['writes'], ['SetAVTransportURI', 'Play', 'Seek'])
            self.assertEqual(replay.player.state, PlaybackState.PLAYING)
            self.assertLess(replay.player.current_position_ms, 10000)
            self.assertEqual(replay.renderer.position_ms, 1000)
            # A rejected start offset must not suppress the real start as an
            # adopted mid-stream session. Reporting remains synthetic here.
            replay.api.report_streaming_start.assert_awaited_once()

    async def test_zero_position_seeks_to_beginning_once(self):
        for room in (False, True):
            replay = make_replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                await replay.dispatch(state_message(position=0, queue=False))
            self.assertEqual(replay.result()['writes'], ['Seek'])
            self.assertEqual(replay.renderer.position_ms, 0)

    async def test_newer_seek_supersedes_queued_seek_without_old_transmission(self):
        for room in (False, True):
            replay = make_replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                await replay.player._playback_lock.acquire()
                tasks = []
                try:
                    for target in (60000, 90000):
                        task = replay.dispatch(state_message(position=target, queue=False))
                        tasks.append(task)
                        await asyncio.wait_for(task.test_entered.wait(), 5)
                finally:
                    replay.player._playback_lock.release()
                await asyncio.wait_for(asyncio.gather(*tasks), 5)
            self.assertEqual(replay.result()['writes'], ['Seek'])
            self.assertEqual(replay.renderer.position_ms, 90000)

    async def test_takeover_before_seek_prevents_mutation(self):
        for room in (False, True):
            replay = make_replay(room)
            with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                await replay.start()
                replay.renderer.gate('GetMediaInfo')
                task = replay.dispatch(state_message(position=60000, queue=False))
                await asyncio.wait_for(replay.renderer.entered.wait(), 5)
                replay.renderer.uri = 'spotify:synthetic-source'
                if room:
                    replay.row['spotifyVersion'] += 1
                replay.renderer.proceed.set()
                await asyncio.wait_for(task, 5)
            self.assertEqual(replay.renderer.writes, [])
            self.assertIsNone(replay.player._requested_playing_state)

    async def test_source_retirement_after_seek_ack_prevents_position_commit(self):
        replay = make_replay(True)
        with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
            await replay.start()
            replay.renderer.after_seek = replay.backend.release_source
            await replay.dispatch(state_message(position=60000, queue=False))
        self.assertEqual(replay.result()['writes'], ['Seek'])
        self.assertLess(replay.player.current_position_ms, 10000)
        self.assertIsNone(replay.player._requested_playing_state)

    async def test_pause_or_stop_during_mid_track_load_prevents_start(self):
        for room in (False, True):
            for mode in (1, 3):
                replay = make_replay(room)
                with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
                    entered, proceed = asyncio.Event(), asyncio.Event()
                    original = replay.player.metadata.get_streaming_url
                    async def delayed(track):
                        entered.set()
                        await proceed.wait()
                        return await original(track)
                    replay.player.metadata.get_streaming_url = delayed
                    first = replay.dispatch(state_message('101', 2, 60000))
                    await asyncio.wait_for(entered.wait(), 5)
                    second = replay.dispatch(state_message(playing=mode, queue=False))
                    await asyncio.wait_for(second.test_entered.wait(), 5)
                    proceed.set()
                    await asyncio.wait_for(asyncio.gather(first, second), 5)
                self.assertEqual(replay.renderer.writes, [])
                self.assertEqual(replay.player._requested_playing_state, mode)

    async def test_newer_target_before_seek_transmission_cancels_old_seek(self):
        replay = make_replay(True)
        with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
            await replay.start()
            # Handler permission, backend ownership, final pre-write ownership.
            replay.renderer.gate('GetMediaInfo', 3)
            first = replay.dispatch(state_message(position=60000, queue=False))
            await asyncio.wait_for(replay.renderer.entered.wait(), 5)
            second = replay.dispatch(state_message(position=90000, queue=False))
            await asyncio.wait_for(second.test_entered.wait(), 5)
            replay.renderer.proceed.set()
            await asyncio.wait_for(asyncio.gather(first, second), 5)
        self.assertEqual(replay.result()['writes'], ['Seek'])
        self.assertEqual(replay.renderer.position_ms, 90000)

    async def test_uncertain_seek_failure_releases_without_retry_or_stop(self):
        replay = make_replay(True, reject=True)
        replay.renderer.failure_code = 501
        with patch.object(DLNAClient, '_soap_action_detailed', fake_soap):
            await replay.start()
            await replay.dispatch(state_message(position=60000, queue=False))
            await replay.dispatch(state_message(playing=2, queue=False))
        self.assertEqual(replay.result()['writes'], ['Seek'])
        self.assertTrue(replay.backend._external_playback)
        self.assertLess(replay.player.current_position_ms, 10000)

"""Standalone player regression tests using only upstream imports and fixtures."""
import unittest
from unittest.mock import AsyncMock, MagicMock

import test_partial_playback_state as fixtures


class SeekAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_stopped_snapshot_retains_nonzero_start_until_play(self):
        replay = self.fixture()
        await replay.dispatch('101', 3, 60000)
        replay.backend.play.assert_not_awaited()
        replay.backend.seek.assert_not_awaited()
        self.assertLess(replay.player.current_position_ms, 10000)
        await replay.dispatch(mode=2)
        replay.backend.seek.assert_awaited_once_with(60000)
        self.assertIsNone(replay.player._pending_start_position_ms)

    async def test_split_start_offset_is_revoked_by_new_authority(self):
        replay = self.fixture()
        await replay.dispatch('101', 3, 60000)
        replay.player.invalidate_pending_commands()
        await replay.dispatch(mode=2)
        replay.backend.seek.assert_not_awaited()
        self.assertIsNone(replay.player._pending_start_position_ms)

    def fixture(self):
        replay = fixtures.PartialPlaybackStateTests()
        replay.setup_player()
        metadata = MagicMock()
        metadata.to_dict.return_value = {'duration_ms': 240000}
        replay.player.metadata.get_metadata.return_value = metadata
        replay.backend.seek_before_report = True
        replay.backend.get_position = AsyncMock(return_value=1000)
        return replay

    async def test_position_and_play_seek_once(self):
        replay = self.fixture()
        await replay.dispatch('101', 2)
        await replay.dispatch(mode=2, position=60000)
        replay.backend.seek.assert_awaited_once_with(60000)

    async def test_explicit_rejection_does_not_advance_position(self):
        replay = self.fixture()
        await replay.dispatch('101', 2)
        replay.backend.seek.return_value = False
        self.assertFalse(await replay.player.seek(60000))
        self.assertLess(replay.player.current_position_ms, 10000)
        replay.backend.seek.assert_awaited_once_with(60000)

    async def test_start_position_is_not_reported_before_seek(self):
        replay = self.fixture()
        reports = []
        async def report():
            reports.append((replay.player.current_position_ms, replay.backend.seek.await_count))
        replay.player._send_state_update = report
        await replay.dispatch('101', 2, 60000)
        self.assertFalse(any(p >= 60000 and calls == 0 for p, calls in reports))
        replay.backend.seek.assert_awaited_once_with(60000)

    async def test_rejected_start_offset_reports_observed_position(self):
        replay = self.fixture()
        replay.backend.seek.return_value = False
        self.assertFalse(await replay.player.play_track(queue_item_id=101, track_id='101', position_ms=60000))
        self.assertGreaterEqual(replay.player.current_position_ms, 1000)
        self.assertLess(replay.player.current_position_ms, 10000)
        replay.backend.seek.assert_awaited_once_with(60000)

    async def test_paused_offset_is_desired_only_until_explicit_play(self):
        replay = self.fixture()
        replay.backend.defer_paused_seek = True
        replay.backend.validate_paused_seek = AsyncMock()
        await replay.dispatch('101', 2)
        await replay.dispatch(mode=3)
        for target in (60000, 0):
            await replay.dispatch(position=target)
            replay.backend.seek.assert_not_awaited()
            self.assertEqual(replay.player.current_position_ms, target)
        await replay.dispatch(mode=2)
        replay.backend.seek.assert_awaited_once_with(0)
        self.assertIsNone(replay.player._pending_paused_position_ms)

    async def test_authority_invalidation_discards_paused_offset(self):
        replay = self.fixture()
        replay.backend.defer_paused_seek = True
        replay.backend.validate_paused_seek = AsyncMock()
        await replay.dispatch('101', 2)
        await replay.dispatch(mode=3)
        await replay.dispatch(position=60000)
        replay.player.invalidate_pending_commands()
        self.assertIsNone(replay.player._pending_paused_position_ms)
        replay.backend.seek.assert_not_awaited()

"""Categorical diagnostic output around real upstream commands and synthetic SOAP."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

import test_upstream_first as fixtures
from qobuz.upstream_diagnostics import StateDiagnostics, event
from qobuz_proxy.connect.protocol import payload_pb2
from qobuz_proxy.playback.command_handler import PlaybackCommandHandler
from qobuz_proxy.backends.types import PlaybackState


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.UpstreamRoomTests.asyncSetUp
    asyncTearDown = fixtures.UpstreamRoomTests.asyncTearDown
    mutations = fixtures.UpstreamRoomTests.mutations
    stock_player = fixtures.UpstreamRoomTests.stock_player

    def prepare(self):
        player, api = self.stock_player()
        meta = MagicMock()
        meta.to_dict.return_value = {'title': 'DO_NOT_LOG_TITLE', 'duration_ms': 180000}
        player.metadata.get_metadata = AsyncMock(return_value=meta)
        player.metadata.get_streaming_url = AsyncMock(return_value='http://audio.example.test/DO_NOT_LOG_URL?token=DO_NOT_LOG_TOKEN')
        handler = PlaybackCommandHandler(player)
        self.backend.bind_authority(lambda: (handler._generation, handler._active))
        handler._note_active(True)
        self.backend.prepare_for_selection()
        fixtures.operation.set(None)
        diagnostics = StateDiagnostics(self.backend)
        self.backend.diagnostics = diagnostics
        diagnostics.bind_player(player)
        return player, api, handler, diagnostics

    def message(self, playing_state=None, track_id=987654321, item_id=876543210):
        msg = payload_pb2.QConnectMessage()
        msg.srvrRndrSetState.currentQueueItem.trackId = track_id
        msg.srvrRndrSetState.currentQueueItem.queueItemId = item_id
        msg.srvrRndrSetState.currentQueueItem.contextUuid = b'DO_NOT_LOG_UUID!!'
        if playing_state is not None:
            msg.srvrRndrSetState.playingState = playing_state
        return msg

    async def test_item_only_preserves_play_without_private_fields_or_extra_commands(self):
        player, api, handler, diagnostics = self.prepare()
        with self.assertLogs('qobuz.upstream_diagnostics', level='INFO') as logs:
            self.assertTrue(await player.play())
            await diagnostics.dispatch(41, self.message(), handler.dispatch_message)
            await asyncio.sleep(0)
            self.assertEqual(player.state, PlaybackState.PLAYING)
            await diagnostics.dispatch(41, self.message(2), handler.dispatch_message)
            await asyncio.sleep(0)
        self.assertEqual(player.state, PlaybackState.PLAYING)
        self.assertEqual(self.mutations(), ['SetAVTransportURI', 'Play', 'Stop', 'SetAVTransportURI', 'Play'])
        output = '\n'.join(logs.output)
        self.assertIn('phase=wire_received stage=playing sourceActive=True playing=absent', output)
        self.assertIn('phase=wire_received stage=playing sourceActive=True playing=playing', output)
        self.assertIn('phase=_load_track_locked_finished', output)
        self.assertIn('phase=soap_sending', output)
        self.assertIn('phase=soap_response', output)
        self.assertIn('acknowledged=True', output)
        for private in ('987654321', '876543210', 'DO_NOT_LOG', 'example.test', 'http://', 'contextUuid', 'trackId', 'queueItemId'):
            self.assertNotIn(private, output)
        self.assertEqual(event.get(), 0)
        self.assertEqual(api.report_streaming_start.await_count, 2)

    async def test_failed_soap_logs_no_ack_and_does_not_retry(self):
        player, api, handler, diagnostics = self.prepare()
        self.failure = 'SetAVTransportURI'
        with self.assertLogs('qobuz.upstream_diagnostics', level='INFO') as logs:
            self.assertFalse(await player.play())
        output = '\n'.join(logs.output)
        self.assertIn('acknowledged=False', output)
        self.assertIn('outcome=rejected_or_error', output)
        self.assertEqual(self.mutations(), ['SetAVTransportURI'])
        self.assertTrue(self.backend._external_playback)
        api.report_streaming_start.assert_not_awaited()

    async def test_source_switch_after_uri_ack_is_visible_and_still_prevents_play(self):
        player, api, handler, diagnostics = self.prepare()
        async def hook(action):
            if action == 'SetAVTransportURI':
                handler._note_active(False)
        self.hook = hook
        with self.assertLogs('qobuz.upstream_diagnostics', level='INFO') as logs:
            self.assertFalse(await player.play())
        output = '\n'.join(logs.output)
        self.assertIn('phase=soap_response', output)
        self.assertIn('acknowledged=True', output)
        self.assertIn('authorityChanged=True', output)
        self.assertEqual(self.mutations(), ['SetAVTransportURI'])
        api.report_streaming_start.assert_not_awaited()

    async def test_report_send_observes_categories_and_preserves_return_value(self):
        player, api, handler, diagnostics = self.prepare()
        manager = MagicMock()
        manager.send_state_update = AsyncMock(return_value=False)
        original = manager.send_state_update
        diagnostics.bind_ws(manager)
        with self.assertLogs('qobuz.upstream_diagnostics', level='INFO') as logs:
            self.assertFalse(await manager.send_state_update(playing_state=2, position_ms=3456789,
                queue_item_id=876543210, duration_ms=987654321, position_timestamp_ms=4567890123))
        original.assert_awaited_once()
        output = '\n'.join(logs.output)
        self.assertIn('playing=playing position=positive', output)
        self.assertIn('sent=False', output)
        for private in ('3456789', '876543210', '987654321', '4567890123'):
            self.assertNotIn(private, output)
        self.assertEqual(self.mutations(), [])

    async def test_non_state_dispatch_and_cancellation_preserve_task_semantics(self):
        player, api, handler, diagnostics = self.prepare()
        original = MagicMock(return_value='same-result')
        self.assertEqual(diagnostics.dispatch(43, object(), original), 'same-result')
        original.assert_called_once()
        entered = asyncio.Event()
        async def wait():
            entered.set()
            await asyncio.Event().wait()
        task_handler = lambda *args: asyncio.create_task(wait())
        with self.assertLogs('qobuz.upstream_diagnostics', level='INFO') as logs:
            task = diagnostics.dispatch(41, self.message(), task_handler)
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
        self.assertIn('cancelled=True', '\n'.join(logs.output))
        self.assertEqual(event.get(), 0)
        self.assertEqual(self.mutations(), [])

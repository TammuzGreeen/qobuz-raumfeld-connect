"""Opt-in, categorical observations only; never log protobufs or identifiers."""
import asyncio
from contextvars import ContextVar
from functools import wraps
import logging

from qobuz_proxy.backends.types import PlaybackState

logger = logging.getLogger(__name__)
event = ContextVar('upstream_diagnostic_event', default=0)


def playing(value):
    return {None: 'absent', 1: 'stopped', 2: 'playing', 3: 'paused'}.get(value, 'unknown')


def position(value):
    if value is None:
        return 'absent'
    return 'zero' if value == 0 else 'positive' if value > 0 else 'invalid'


class StateDiagnostics:
    def __init__(self, backend):
        self.backend = backend
        self.sequence = 0  # Local diagnostic correlation, never a cloud message ID.

    def emit(self, phase, **fields):
        try:
            player = self.backend.player
            state = player.state if player else None
            stage = state.name.lower() if isinstance(state, PlaybackState) else 'unavailable'
            authority = self.backend.capture_operation()[2]
            source_active = authority[1] if authority is not None else 'unavailable'
            values = dict(room=self.backend.diagnostic_alias, event=event.get(), phase=phase,
                          stage=stage, sourceActive=source_active, **fields)
            logger.info('Upstream state diagnostic %s', ' '.join(f'{k}={v}' for k, v in values.items()))
        except Exception:
            pass  # Diagnostics must never change command success or safety.

    def target(self, track_id, item_id):
        track = self.backend.player.current_track if self.backend.player else None
        return {
            'track': 'absent' if track_id is None else 'unloaded' if track is None
                     else 'same' if str(track_id) == track.track_id else 'different',
            'item': 'absent' if item_id is None else 'unloaded' if track is None
                    else 'same' if item_id == track.queue_item_id else 'different',
        }

    def changes(self, before):
        after = self.backend.capture_operation()
        return dict(selectionChanged=before[0] != after[0],
                    commandChanged=before[1] != after[1], authorityChanged=before[2] != after[2])

    def dispatch(self, msg_type, message, handler):
        if msg_type != 41:
            return handler(msg_type, message)
        self.sequence += 1
        token = event.set(self.sequence)
        before = self.backend.capture_operation()
        try:
            present = message.HasField('srvrRndrSetState')
            if present:
                state = message.srvrRndrSetState
                current = state.currentQueueItem if state.HasField('currentQueueItem') else None
                self.emit('wire_received', playing=playing(state.playingState if state.HasField('playingState') else None),
                          position=position(state.currentPosition if state.HasField('currentPosition') else None),
                          nextPresent=state.HasField('nextQueueItem'), queueVersionPresent=state.HasField('queueVersion'),
                          **self.target(current.trackId if current else None, current.queueItemId if current else None))
            else:
                self.emit('wire_received', payloadPresent=False)
            task = handler(msg_type, message)
            if isinstance(task, asyncio.Task):
                def done(completed):
                    # Do not retrieve exceptions: preserve stock task handling.
                    self.emit('handler_finished', cancelled=completed.cancelled(), **self.changes(before))
                task.add_done_callback(done)
            return task
        finally:
            event.reset(token)

    def bind_player(self, player):
        invalidate = player.invalidate_pending_commands
        @wraps(invalidate)
        def invalidated():
            self.emit('intent_invalidation', selectionPending=self.backend.selection_pending,
                      creationOutstanding=self.backend.creation_outstanding)
            return invalidate()
        player.invalidate_pending_commands = invalidated
        for name in ('apply_remote_state', '_load_track_locked', '_play_locked', '_start_playback',
                     '_pause_locked', '_stop_playback_locked', '_handle_track_ended', 'seek', '_send_state_update'):
            original = getattr(player, name)
            def wrap(original, name):
                @wraps(original)
                async def observed(*args, **kwargs):
                    before = self.backend.capture_operation()
                    fields = {}
                    if name == 'apply_remote_state':
                        fields = dict(playing=playing(kwargs.get('playing_state')),
                                      position=position(kwargs.get('position_ms')),
                                      **self.target(kwargs.get('track_id'), kwargs.get('queue_item_id')))
                    self.emit(name + '_entered', **fields)
                    outcome = 'error'
                    try:
                        result = await original(*args, **kwargs)
                        outcome = 'true' if result is True else 'false' if result is False else 'completed'
                        return result
                    except asyncio.CancelledError:
                        outcome = 'cancelled'
                        raise
                    finally:
                        self.emit(name + '_finished', outcome=outcome, **self.changes(before))
                return observed
            setattr(player, name, wrap(original, name))

    def bind_ws(self, manager):
        release = manager.release_external_playback
        @wraps(release)
        def released():
            result = release()
            self.emit('cloud_session_released', selectionPending=self.backend.selection_pending,
                      creationOutstanding=self.backend.creation_outstanding)
            return result
        manager.release_external_playback = released
        original = manager.send_state_update
        @wraps(original)
        async def send_state_update(*args, **kwargs):
            # Only named primitive state fields; never serialize the report.
            self.emit('report_send', playing=playing(kwargs.get('playing_state')),
                      position=position(kwargs.get('position_ms')),
                      **self.target(None, kwargs.get('queue_item_id')))
            result = await original(*args, **kwargs)
            self.emit('report_send_finished', sent=result is True)
            return result
        manager.send_state_update = send_state_update


def trace_soap(method):
    @wraps(method)
    async def observed(self, url, service, action, args, max_retries=None):
        diagnostics = getattr(self.backend, 'diagnostics', None)
        if diagnostics is None or action not in {'SetAVTransportURI', 'Play', 'Pause', 'Stop', 'Seek', 'SetVolume'}:
            return await method(self, url, service, action, args, max_retries)
        diagnostics.emit('soap_requested', action=action)
        outcome = 'rejected_or_error'
        try:
            result = await method(self, url, service, action, args, max_retries)
            outcome = 'acknowledged' if result.success else 'not_acknowledged'
            return result
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        finally:
            diagnostics.emit('soap_finished', action=action, outcome=outcome)
    return observed

"""Narrow adaptation for a renderer that rejects the initial resume seek."""
from qobuz_proxy.playback import QobuzPlayer
from .backend import SeekUnsupported, VolumeUncertain


class RaumfeldPlayer(QobuzPlayer):
    async def set_volume(self, level):
        try:
            return await super().set_volume(level)
        except VolumeUncertain:
            observed = (self.backend.last_volume_result or {}).get('observedVolume')
            if isinstance(observed, int) and 0 <= observed <= 100:
                self._volume = observed
                await self._report_volume_change()
            # Preserve the failed outcome; do not report the requested level as
            # successfully applied. Upstream's volume handler catches this.
            raise

    async def _play_locked(self, position_ms=0):
        timeline = self.backend.relay.timeline
        if not timeline:
            return await self._play_with_seek_reconciliation(position_ms)
        token = timeline.begin()
        try:
            return await self._play_with_seek_reconciliation(position_ms)
        finally:
            timeline.attempt.reset(token)

    async def _play_with_seek_reconciliation(self, position_ms=0):
        generation = self._command_generation
        try:
            return await super()._play_locked(position_ms)
        except SeekUnsupported:
            # Upstream starts playback before applying the phone's position.
            # Keep that successful start, but only with a still-valid lease.
            if not self.backend.started or not self.backend.token:
                raise
            position = await self.backend.observed_position()
            if generation != self._command_generation:
                return False
            self._set_position(position)
            await self._send_state_update()
            return True

    async def _start_playback(self, start_position_ms=0):
        # Natural advancement calls this public-to-the-player path without
        # _play_locked. Correlate it without changing upstream track logic.
        timeline = self.backend.relay.timeline
        if not timeline or timeline.attempt.get():
            return await super()._start_playback(start_position_ms)
        token = timeline.begin()
        try:
            return await super()._start_playback(start_position_ms)
        finally:
            timeline.attempt.reset(token)

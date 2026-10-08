"""Narrow adaptation for a renderer that rejects the initial resume seek."""
from qobuz_proxy.playback import QobuzPlayer
from .backend import SeekUnsupported


class RaumfeldPlayer(QobuzPlayer):
    async def _play_locked(self, position_ms=0):
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

"""Fail-closed backport installation on the exact pinned source."""
import hashlib
from pathlib import Path
import runpy
import unittest

from qobuz_proxy.playback import player


class UpstreamPatchTests(unittest.TestCase):
    def test_installed_patch_and_hash_rejection(self):
        root = Path(__file__).resolve().parents[1]
        installer = runpy.run_path(str(root / 'docker/apply-upstream-player-patch.py'))
        installed = Path(player.__file__).read_bytes()
        self.assertNotEqual(hashlib.sha256(installed).hexdigest(), installer['EXPECTED'])
        self.assertIn(b'_requested_playing_state', installed)
        with self.assertRaisesRegex(ValueError, 'Unreviewed upstream'):
            installer['apply'](installed, (root / 'upstream-patches/partial-playback-state.patch').read_text())

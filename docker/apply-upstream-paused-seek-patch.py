"""Hash-checked paused seek deferral after the acknowledged-seek backport."""
import importlib.util
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = 'f25fa09da3de8deb117ceda2b03ac0cc876d31fa53afa675bd239ae83c68cfd2'


if __name__ == '__main__':
    package = Path(importlib.util.find_spec('qobuz_proxy').origin).parent
    target = package / 'playback/player.py'
    apply = runpy.run_path(str(ROOT / 'docker/apply-upstream-player-patch.py'))['apply']
    result = apply(target.read_bytes(),
                   (ROOT / 'upstream-patches/deferred-paused-seek.patch').read_text(), EXPECTED)
    compile(result, str(target), 'exec')
    target.write_bytes(result)

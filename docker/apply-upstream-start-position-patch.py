"""Hash-checked desired start-position retention after the paused-seek patch."""
import importlib.util
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = '7bc008e7b3251456c4c55fb2f8bd7ab45a0b5b44fce243f64e05b4b0ee598b91'


if __name__ == '__main__':
    package = Path(importlib.util.find_spec('qobuz_proxy').origin).parent
    target = package / 'playback/player.py'
    apply = runpy.run_path(str(ROOT / 'docker/apply-upstream-player-patch.py'))['apply']
    result = apply(target.read_bytes(),
                   (ROOT / 'upstream-patches/deferred-start-position.patch').read_text(), EXPECTED)
    compile(result, str(target), 'exec')
    target.write_bytes(result)

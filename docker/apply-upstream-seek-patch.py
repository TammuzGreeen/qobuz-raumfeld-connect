"""Fail-closed isolated seek acknowledgement patch, after the intent backport."""
import importlib.util
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    'qobuz_proxy/backends/dlna/backend.py': '751c713f04ef0cab9f1170a7768bedc14e772a1c7601dfe967177f405d7cdba9',
    'qobuz_proxy/playback/player.py': 'eedcb9fcedc5a807936fc2073035f749dc13195b52a092208279c45384fb457e',
}


def prepare(root, diff):
    apply = runpy.run_path(str(ROOT / 'docker/apply-upstream-player-patch.py'))['apply']
    prepared = {}
    for section in diff.split('diff --git ')[1:]:
        header = section.splitlines()[0].split()
        path = header[0].removeprefix('a/')
        if path not in EXPECTED or header != ['a/' + path, 'b/' + path] or path in prepared:
            raise ValueError('Unreviewed seek patch target')
        target = root / path
        result = apply(target.read_bytes(), section, EXPECTED[path])
        compile(result, str(target), 'exec')
        prepared[path] = result
    if set(prepared) != set(EXPECTED):
        raise ValueError('Missing seek patch target')
    return prepared


if __name__ == '__main__':
    root = Path(importlib.util.find_spec('qobuz_proxy').origin).parent.parent
    prepared = prepare(root, (ROOT / 'upstream-patches/seek-acknowledgement.patch').read_text())
    # Verify and compile both files before changing either file.
    for path, result in prepared.items():
        (root / path).write_bytes(result)

"""Apply the isolated player backport only to the reviewed, pinned source."""
import hashlib
import importlib.util
from pathlib import Path
import re


EXPECTED = '5642865f95d19620227d0e5f415309c132080c63c40a44447a3e35f5760f35e4'


def apply(source, diff):
    if hashlib.sha256(source).hexdigest() != EXPECTED:
        raise ValueError('Unreviewed upstream player source; refusing backport')
    original = source.decode().splitlines(keepends=True)
    output, cursor = [], 0
    lines = iter(diff.splitlines(keepends=True))
    for line in lines:
        if not line.startswith('@@ '):
            continue
        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
        if not match:
            raise ValueError('Invalid patch hunk')
        start, count = int(match[1]) - 1, int(match[2] or 1)
        added = int(match[4] or 1)
        if start < cursor:
            raise ValueError('Overlapping patch hunks')
        output.extend(original[cursor:start])
        cursor = start
        consumed, produced = 0, 0
        while consumed < count or produced < added:
            body = next(lines)
            if body[0] in ' -':
                if original[cursor] != body[1:]:
                    raise ValueError('Patch context mismatch')
                cursor += 1
                consumed += 1
            if body[0] in ' +':
                output.append(body[1:])
                produced += 1
            if body[0] not in ' +-':
                raise ValueError('Invalid patch content')
    output.extend(original[cursor:])
    return ''.join(output).encode()


if __name__ == '__main__':
    spec = importlib.util.find_spec('qobuz_proxy.playback.player')
    target = Path(spec.origin)
    patch = Path('/app/upstream-patches/partial-playback-state.patch').read_text()
    result = apply(target.read_bytes(), patch)
    compile(result, str(target), 'exec')
    target.write_bytes(result)

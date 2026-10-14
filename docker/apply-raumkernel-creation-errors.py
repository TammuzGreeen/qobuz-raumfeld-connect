"""Preserve categorical errors in pinned node-raumkernel's explicit zone demand.

No discovery, subscription, transport, retry or successful-response changes.
Original implementation remains node-raumkernel (MIT); this isolated adaptation
is reviewed against the exact installed source, not a dependency upgrade.
"""
import hashlib
from pathlib import Path


EXPECTED = '78695a3c36ad89b49d31b1f4918e303a37d9e32f9ea508cd95cc587caa559efb'


def apply(source):
    if hashlib.sha256(source).hexdigest() != EXPECTED:
        raise ValueError('Unreviewed node-raumkernel zone manager')
    # Preserve line endings: the pinned source is CRLF.
    text = source.decode()
    start = text.index('    connectRoomToZone(')
    end = text.index('    async dropRoomFromZone(', start)
    section = text[start:end]
    old = '_reject(new Error("Executing connectRoomToZone zone without a host!"));'
    assert section.count(old) == 1
    section = section.replace(old, '_reject(Object.assign(new Error("renderer_host_unavailable"), '
        '{rendererRequestOrigin: "local_check", rendererRequestAttempted: false})); return;')
    old = '_reject(new Error("Error connectRoomToZone: " + error));'
    assert section.count(old) == 2
    replacement = ('_reject(Object.assign(new Error("renderer_request_failed"), '
        '{rendererRequestOrigin: error || !response ? "network" : "raumfeld_response", '
        'rendererRequestAttempted: true, rendererHttpStatus: response && response.statusCode, '
        'rendererNetworkCode: error && error.code}));')
    section = section.replace(old, replacement, 1)
    # Exit the transport-failure branch rather than falling through and logging
    # a second rejection. Never repeat the request or change success handling.
    section = section.replace(old, replacement, 1)
    marker = replacement + '\r\n                }'
    assert section.count(marker) == 2
    section = section.replace(marker, replacement + ' return;\r\n                }', 1)
    return (text[:start] + section + text[end:]).encode()


if __name__ == '__main__':
    target = Path('/app/node_modules/node-raumkernel/lib/lib.manager.zoneManager.js')
    target.write_bytes(apply(target.read_bytes()))

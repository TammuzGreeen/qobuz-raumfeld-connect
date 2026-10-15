"""Legacy generated labels are normalized without touching room identity/storage."""
import json
from pathlib import Path
import tempfile
import unittest

from qobuz.room_names import room_display_name
from qobuz.upstream_app import load_runtime


class RoomNameTests(unittest.TestCase):
    def test_only_exact_legacy_generated_suffix_is_removed(self):
        for name, expected in (
            ('Example Qobuz abc123', 'Example Qobuz'),
            ('Example Qobuz', 'Example Qobuz'),
            ('Custom beta abc123', 'Custom beta abc123'),
            ('Example Qobuz def456', 'Example Qobuz def456'),
            ('Example Qobuz abc123 Extra', 'Example Qobuz abc123 Extra'),
        ):
            self.assertEqual(room_display_name({'id': 'synthetic-abc123', 'name': name}), expected)

    def test_runtime_name_is_consistent_and_keeps_files_uuid_and_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            rooms = [{'id': 'synthetic-abc123', 'name': 'Example Qobuz abc123', 'port': 8790},
                     {'id': 'synthetic-def456', 'name': 'Other Qobuz', 'port': 8791}]
            path.write_text(json.dumps({'rooms': rooms, 'quality': 6}))
            original = path.read_bytes()
            normalized, config, _ = load_runtime(directory, '192.0.2.10')
            self.assertEqual([r['name'] for r in normalized], ['Example Qobuz', 'Other Qobuz'])
            self.assertEqual([s.name for s in config.speakers], ['Example Qobuz', 'Other Qobuz'])
            self.assertEqual([r['id'] for r in normalized], [r['id'] for r in rooms])
            uuids = [s.uuid for s in config.speakers]
            rooms[0]['name'] = 'Example Qobuz'
            path.write_text(json.dumps({'rooms': rooms, 'quality': 6}))
            _, clean, _ = load_runtime(directory, '192.0.2.10')
            self.assertEqual(uuids, [s.uuid for s in clean.speakers])
            self.assertEqual([s.http_port for s in config.speakers], [8790, 8791])
            path.write_bytes(original)
            load_runtime(directory, '192.0.2.10')
            self.assertEqual(path.read_bytes(), original)

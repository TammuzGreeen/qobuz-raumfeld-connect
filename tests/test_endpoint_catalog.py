import unittest

from qobuz.endpoint_catalog import EndpointCatalog


class EndpointCatalogTests(unittest.TestCase):
    def row(self, url='http://renderer.example.test:47365/description.xml', renderer='synthetic-zone'):
        return {'roomId': 'synthetic-room', 'endpoint': {'roomId': 'synthetic-room',
                'rendererId': renderer, 'descriptionUrl': url}}

    def test_unchanged_endpoint_is_noop(self):
        catalog = EndpointCatalog(['synthetic-room'])
        self.assertEqual(catalog.reconcile([self.row()])[0].kind, 'bind')
        self.assertEqual(catalog.reconcile([self.row()]), [])

    def test_missing_and_reappearing_renderer_preserve_room_identity(self):
        catalog = EndpointCatalog(['synthetic-room'])
        catalog.reconcile([self.row()])
        self.assertEqual(catalog.reconcile([])[0].kind, 'unavailable')
        self.assertEqual(catalog.reconcile([]), [])
        self.assertEqual(list(catalog.endpoints), ['synthetic-room'])
        change = catalog.reconcile([self.row(renderer='synthetic-new-zone')])[0]
        self.assertEqual(change.kind, 'bind')
        self.assertEqual(change.room_id, 'synthetic-room')

    def test_address_change_requests_rebind_not_restart_or_play(self):
        catalog = EndpointCatalog(['synthetic-room'])
        catalog.reconcile([self.row()])
        change = catalog.reconcile([self.row('http://replacement.example.test/description.xml')])[0]
        self.assertEqual(change.kind, 'rebind')
        self.assertEqual(change.previous.renderer_id, change.endpoint.renderer_id)
        self.assertEqual(change.room_id, 'synthetic-room')

    def test_invalid_snapshot_is_atomic(self):
        catalog = EndpointCatalog(['synthetic-room'])
        catalog.reconcile([self.row()])
        before = dict(catalog.endpoints)
        for rows in [[self.row(), self.row()], [{'roomId': 'unknown', 'endpoint': None}],
                     [self.row('http://user:secret@example.test/device')],
                     [self.row('http://example.test:99999/device')],
                     [self.row('http://example.test/device?secret=private')]]:
            with self.assertRaises(ValueError):
                catalog.reconcile(rows)
            self.assertEqual(catalog.endpoints, before)


if __name__ == '__main__':
    unittest.main()

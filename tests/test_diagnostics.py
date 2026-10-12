import json
import logging
import unittest
from qobuz.diagnostics import ConnectDiagnostics


class ConnectDiagnosticsTests(unittest.TestCase):
    def test_cloud_errors_and_stages_are_retained_without_sensitive_messages(self):
        diagnostics = ConnectDiagnostics()
        for message in [
            'Received connection from app (session: PRIVATE...)',
            '[PRIVATE ROOM] Connecting to wss://host/PRIVATE-TOKEN...',
            '[PRIVATE ROOM] Sent JOIN_SESSION: isActive=True',
            'Server error 1003: PRIVATE account and URI details',
            'Connection closed: 1008 PRIVATE reason and token',
            'Connection failed: PRIVATE endpoint and credentials',
            'PRIVATE unknown message',
        ]:
            diagnostics.handle(logging.LogRecord('test', logging.INFO, '', 0, message, (), None))
        events = diagnostics.snapshot()
        self.assertEqual([e['event'] for e in events], [
            'handshake_accepted', 'cloud_connecting', 'cloud_join_sent',
            'cloud_server_error', 'cloud_connection_closed', 'cloud_connection_failed'])
        self.assertEqual(events[3]['code'], 1003)
        self.assertEqual(events[4]['code'], 1008)
        self.assertNotIn('PRIVATE', json.dumps(events))
        self.assertNotIn('wss://', json.dumps(events))

    def test_events_are_bounded_and_snapshots_are_copied(self):
        diagnostics = ConnectDiagnostics()
        for _ in range(60):
            diagnostics.handle(logging.LogRecord('test', logging.INFO, '', 0,
                'Connection failed: PRIVATE', (), None))
        events = diagnostics.snapshot()
        self.assertEqual(len(events), 50)
        events[0]['event'] = 'modified'
        self.assertNotEqual(diagnostics.snapshot()[0]['event'], 'modified')

    def test_installed_collector_does_not_propagate_raw_upstream_messages(self):
        from qobuz_proxy.connect import DiscoveryService, WsManager
        from qobuz_proxy.playback import PlaybackCommandHandler, QobuzPlayer, MetadataService, StateReporter
        from qobuz_proxy.auth.api_client import QobuzAPIClient
        components = (DiscoveryService, WsManager, PlaybackCommandHandler, QobuzPlayer, MetadataService, StateReporter, QobuzAPIClient)
        loggers = [logging.getLogger(component.__module__) for component in components]
        previous = [(logger, logger.level, logger.propagate, logger.handlers[:]) for logger in loggers]
        diagnostics = ConnectDiagnostics()
        try:
            diagnostics.install()
            for logger in loggers:
                self.assertFalse(logger.propagate)
                self.assertEqual(logger.handlers, [diagnostics])
                logger.info('Connection failed: PRIVATE')
            self.assertNotIn('PRIVATE', json.dumps(diagnostics.snapshot()))
        finally:
            for logger, level, propagate, handlers in previous:
                logger.setLevel(level)
                logger.propagate = propagate
                logger.handlers = handlers

    def test_playback_failures_retain_phase_without_track_or_stream_details(self):
        diagnostics = ConnectDiagnostics()
        for message in ['Failed to load track PRIVATE: PRIVATE',
                        'Failed to fetch URL for PRIVATE: https://PRIVATE',
                        'Failed to fetch metadata for PRIVATE: PRIVATE',
                        'Error handling playback command 41: PRIVATE',
                        'Failed to send state update: PRIVATE']:
            diagnostics.handle(logging.LogRecord('test', logging.ERROR, '', 0, message, (), None))
        events = diagnostics.snapshot()
        self.assertEqual([e['event'] for e in events], ['track_load_failed', 'stream_url_fetch_failed',
                         'track_metadata_failed', 'playback_command_failed', 'state_report_failed'])
        self.assertNotIn('PRIVATE', json.dumps(events))

    def test_api_http_errors_expose_status_only_not_response_bodies(self):
        diagnostics = ConnectDiagnostics()
        for message in ['API request failed: 401',
                        'Failed to get track URL: 403 — PRIVATE JWT and account body',
                        'Session start failed: HTTP 400 — PRIVATE signed parameters']:
            diagnostics.handle(logging.LogRecord('test', logging.WARNING, '', 0, message, (), None))
        events = diagnostics.snapshot()
        self.assertEqual([(e['event'], e['code']) for e in events], [
            ('metadata_api_http_error', 401), ('stream_api_http_error', 403),
            ('stream_session_http_error', 400)])
        self.assertNotIn('PRIVATE', json.dumps(events))

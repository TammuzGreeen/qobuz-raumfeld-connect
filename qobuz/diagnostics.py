"""Bounded Connect events; never expose upstream messages or exception text."""
from collections import deque
import logging
import re
import time


class ConnectDiagnostics(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.events = deque(maxlen=50)

    def emit(self, record):
        # These messages may contain endpoints, JWTs, session IDs or names.
        # Match known events in memory, but retain only fixed codes/numbers.
        try:
            text = record.getMessage()
        except Exception:
            return
        event = None
        number = None
        if text.startswith('Server error '):
            match = re.match(r'^Server error ([0-9]{1,6}):', text)
            if match:
                event, number = 'cloud_server_error', int(match[1])
        elif re.match(r'^(API request failed: |Failed to get track URL: |Session start failed: HTTP )[0-9]{3}', text):
            match = re.match(r'^(API request failed: |Failed to get track URL: |Session start failed: HTTP )([0-9]{3})', text)
            event = {'API request failed: ': 'metadata_api_http_error',
                     'Failed to get track URL: ': 'stream_api_http_error',
                     'Session start failed: HTTP ': 'stream_session_http_error'}[match[1]]
            number = int(match[2])
        elif text.startswith('Connection closed: '):
            event = 'cloud_connection_closed'
            match = re.match(r'^Connection closed: ([0-9]{1,5})\b', text)
            if match:
                number = int(match[1])
        else:
            patterns = [
                ('Received connection from app', 'handshake_accepted'),
                ('Connection selection rejected', 'handshake_selection_rejected'),
                ('Invalid tokens in connect request', 'handshake_tokens_invalid'),
                ('Error handling connect request', 'handshake_failed'),
                ('] Connecting to ', 'cloud_connecting'),
                ('Connected and authenticated', 'cloud_connected'),
                ('Sent JOIN_SESSION:', 'cloud_join_sent'),
                ('Renderer set active: True', 'cloud_active'),
                ('Renderer set active: False', 'cloud_inactive'),
                ('Cannot start WsManager: no valid tokens', 'cloud_tokens_invalid'),
                ('Token expired, waiting for refreshed token', 'cloud_waiting_for_token'),
                ('WS token refresh failed:', 'cloud_token_refresh_failed'),
                ('Server requested disconnect', 'cloud_disconnect_requested'),
                ('Left Qobuz session after external source takeover', 'cloud_session_released'),
                ('Connection failed:', 'cloud_connection_failed'),
                ('Connection error:', 'cloud_connection_failed'),
                ('Handler error for type ', 'cloud_handler_failed'),
                ('Error handling playback command ', 'playback_command_failed'),
                ('Failed to load track ', 'track_load_failed'),
                ('Failed to start playback:', 'playback_start_failed'),
                ('Failed to get URL for track ', 'stream_url_unavailable'),
                ('No streaming URL available for ', 'stream_url_unavailable'),
                ('Failed to fetch URL for ', 'stream_url_fetch_failed'),
                ('Failed to fetch metadata for ', 'track_metadata_failed'),
                ('No metadata found for track ', 'track_metadata_unavailable'),
                ('Playback error:', 'playback_error'),
                ('Failed to send state update', 'state_report_failed'),
            ]
            event = next((code for marker, code in patterns if marker in text), None)
        if event:
            item = {'at': int(time.time() * 1000), 'event': event}
            if number is not None:
                item['code'] = number
            self.events.append(item)

    def snapshot(self):
        self.acquire()
        try:
            return [dict(item) for item in self.events]
        finally:
            self.release()

    def install(self):
        from qobuz_proxy.connect import DiscoveryService, WsManager
        from qobuz_proxy.playback import PlaybackCommandHandler, QobuzPlayer, MetadataService, StateReporter
        from qobuz_proxy.auth.api_client import QobuzAPIClient
        for component in (DiscoveryService, WsManager, PlaybackCommandHandler, QobuzPlayer, MetadataService, StateReporter, QobuzAPIClient):
            logger = logging.getLogger(component.__module__)
            logger.setLevel(logging.INFO)
            # Bypass ordinary log output: only this sanitized event collector
            # receives these records. Other upstream logging remains suppressed.
            logger.propagate = False
            logger.handlers = [self]

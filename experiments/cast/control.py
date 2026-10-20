"""Diagnostic-only source-neutral boundary. No speaker mutation implementation."""
import json
import os
from pathlib import Path
import secrets
from typing import Protocol


class Transport(Protocol):
    """Future admitted media → native zone transport, never a Qobuz session.

    Implementations must separate requested/confirmed offsets; all operations
    carry session generation + explicit logical membership, and fail terminally
    after source/member/endpoint retirement. No timed leases or replay registry.
    """
    async def load(self, media, position_ms: int, autoplay: bool): ...
    async def play(self): ...
    async def pause(self): ...
    async def stop(self): ...
    async def seek(self, position_ms: int): ...
    async def volume(self, percent: int): ...
    async def observed_state(self): ...


class Blocked(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class DiagnosticControl:
    def __init__(self, config, directory):
        rooms = config.get('rooms', [])
        if (not rooms or len(rooms) > 50 or any(not isinstance(r, dict) or not isinstance(r.get('id'), str)
                or not r['id'] or not isinstance(r.get('name'), str) or not r['name'] for r in rooms)):
            raise ValueError('invalid_experimental_rooms')
        self.rooms = {r['id']: dict(r) for r in rooms}
        if len(self.rooms) != len(rooms):
            raise ValueError('duplicate_logical_room')
        self.members = frozenset(self.rooms)
        self.selected = config.get('selectedRoom', rooms[0]['id'])
        if self.selected not in self.members:
            raise ValueError('invalid_selected_room')
        self.directory = Path(directory)
        self.failure = None

    def status(self, snapshot):
        fresh = snapshot.get('topologyFresh') is True
        observed = {r['id']: r for r in snapshot.get('rooms', [])}
        renderers = {r['id']: r for r in snapshot.get('renderers', [])}
        rows = []
        stopped = True
        for room_id in sorted(self.members):
            room = observed.get(room_id)
            available = bool(fresh and room and room.get('fresh') and room.get('rendererIds'))
            ids = (room.get('rendererIds', []) + ([room['zoneId']] if room.get('zoneId') else [])) if room else []
            confirmed_stopped = bool(available and ids and all(renderers.get(i, {}).get('fresh') is True
                and renderers[i].get('transport') in ('STOPPED', 'NO_MEDIA_PRESENT') for i in ids))
            stopped = stopped and confirmed_stopped
            rows.append({'id': room_id, 'name': self.rooms[room_id]['name'], 'available': available,
                'limitation': None if available else 'missing_or_stale_observation', 'stopped': confirmed_stopped})
        groups = [set(z.get('roomIds', [])) for z in snapshot.get('zones', [])]
        if not fresh:
            grouping = 'unknown'
        elif self.members in groups:
            grouping = 'all_configured' if len(self.members) > 1 else 'individual'
        elif any(len(group) > 1 and group & self.members for group in groups):
            grouping = 'mixed_or_external_members'
        else:
            grouping = 'individual' if all(r['id'] in observed for r in rows) else 'unknown'
        return {'mode': 'diagnostic_only', 'blocker': 'google_device_auth_and_abs_application',
            'selectedRoom': self.selected, 'membership': sorted(self.members), 'rooms': rows,
            'grouping': grouping, 'pending': False, 'failure': self.failure,
            'playbackStopped': stopped, 'groupingImplemented': False, 'mediaEnabled': False}

    def select(self, room_id):
        if room_id not in self.members:
            raise Blocked('room_not_configured')
        self.selected = room_id  # RAM only. No source selection or renderer demand.

    def group(self, enabled, snapshot):
        if type(enabled) is not bool:
            raise Blocked('boolean_required', 400)
        state = self.status(snapshot)
        self.failure = 'playback_not_confirmed_stopped' if not state['playbackStopped'] else 'cast_compatibility_blocked'
        raise Blocked(self.failure, 409 if not state['playbackStopped'] else 501)

    def save_snapshot(self, snapshot):
        if snapshot.get('topologyFresh') is not True:
            raise Blocked('fresh_topology_required')
        # Recovery material stays private; no automatic restore implementation.
        path = self.directory / 'recovery'
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = path / ('topology-' + secrets.token_hex(8) + '.json')
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump({key: snapshot.get(key) for key in ('revision', 'topologyAt', 'rooms', 'zones')}, file)
        return {'saved': True, 'automaticRecovery': False}

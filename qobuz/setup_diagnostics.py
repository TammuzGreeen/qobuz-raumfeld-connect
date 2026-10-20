"""Categorical setup diagnostics: never include upstream errors or household data."""
import contextlib
import errno
import json
import os
from pathlib import Path
import tempfile


class StorageError(Exception):
    def __init__(self, path, operation, code):
        self.detail = {'file': Path(path).name if Path(path).name in
                       ('config.json', 'credentials.json') else 'storage',
                       'operation': operation, 'code': code}
        super().__init__(code)

    @property
    def message(self):
        return {
            'storage_permission_denied': 'The container user cannot read or update /data. Check mount ownership and ACLs for the runtime UID/GID, including directory create/replace permission; do not use chmod 777.',
            'storage_read_only': 'The /data mount is read-only. Provide a writable persistent data mount.',
            'storage_full': 'The data filesystem is full or its quota is exhausted.',
            'storage_invalid_json': 'A saved JSON file is invalid. Back it up and correct it; existing contents were not overwritten.',
        }.get(self.detail['code'], 'Persistent storage access failed. Check the data mount and container logs.')


def storage_error(path, operation, error):
    code = {errno.EACCES: 'storage_permission_denied', errno.EPERM: 'storage_permission_denied',
            errno.EROFS: 'storage_read_only', errno.ENOSPC: 'storage_full',
            errno.EDQUOT: 'storage_full'}.get(error.errno, 'storage_io_error')
    return StorageError(path, operation, code)


def save_json(path, data):
    path = Path(path)
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
        with os.fdopen(fd, 'w', encoding='utf8') as out:
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)  # mkstemp creates mode 0600; no post-replace chmod needed.
    except OSError as error:
        raise storage_error(path, 'write', error) from None
    finally:
        if temp:
            with contextlib.suppress(OSError):
                os.unlink(temp)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise StorageError(path, 'read', 'storage_invalid_json') from None
    except OSError as error:
        raise storage_error(path, 'read', error) from None


def advertisement_diagnostics(service, address_local):
    """Explain the existing guarded/direct admission checks, never authorize writes."""
    blockers = []
    if service.storage_errors:
        blockers += sorted({v['code'] for v in service.storage_errors.values()})
    if not service.settings['rooms']:
        blockers.append('no_rooms_configured')
    if service.api is None:
        blockers.append('authentication_required' if service.auth_status == 'login_required'
                        else 'authentication_pending')
    if not service.address:
        blockers.append('lan_address_missing')
    elif not address_local:
        blockers.append('lan_address_not_local')
    if service.state_error:
        blockers.append(service.state_error)
    known = {r['id']: r for r in service.state['rooms']}
    zones = {z['id']: z['roomIds'] for z in service.state.get('zones', [])}
    rows = []
    for ordinal, configured in enumerate(service.settings['rooms'], 1):
        room = known.get(configured['id'])
        reasons = list(blockers)
        if service.playback_path != 'direct_dlna':
            if not service.state['topologyFresh']:
                reasons.append('topology_unavailable_or_stale')
            if not room:
                reasons.append('configured_room_not_discovered')
            else:
                if room.get('zoneId') and len(zones.get(room['zoneId'], [])) != 1:
                    reasons.append('grouped_or_unresolved_zone')
                if not (room.get('fresh') or room.get('transitioning')):
                    reasons.append('incomplete_renderer_observation')
        error = service.receiver_errors.get(configured['id'])
        if error:
            reasons.append(error)
        advertised = configured['id'] in service.receivers
        ids = set(room.get('rendererIds', []) + [room.get('zoneId')]) if room else set()
        read_errors = [{'action': e['action'], 'code': e['code']}
                       for e in service.state.get('observationErrors', []) if e.get('id') in ids
                       and e.get('action') in ('GetMediaInfo', 'GetTransportInfo', 'GetPositionInfo', 'QueryLastChange', 'Observation')
                       and e.get('code') in ('ENOACTION', 'ENOSERVICE', 'ECONNRESET', 'ECONNREFUSED',
                                            'ETIMEDOUT', 'EHOSTUNREACH', 'EUPNP', 'observation_failed_or_timed_out')]
        rows.append({'roomId': configured['id'], 'alias': f'room_{ordinal:02d}',
                     'advertised': advertised, 'blockers': list(dict.fromkeys(reasons)),
                     'observationErrors': read_errors})
    return {'blockers': list(dict.fromkeys(blockers)), 'rooms': rows,
            'meaning': 'Local advertiser registration, not verified phone visibility or audible playback.'}

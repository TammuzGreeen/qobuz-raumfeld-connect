"""Pure discovery reconciliation for the upstream-first candidate.

No receiver, session admission or playback authority here. Actions describe
in-place endpoint maintenance; they must not be applied through upstream's
restart-on-edit API. The lifecycle adapter is a separate implementation step.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Endpoint:
    room_id: str
    renderer_id: str
    description_url: str

    @classmethod
    def parse(cls, room_id, value):
        if not isinstance(value, dict) or value.get('roomId') != room_id:
            raise ValueError('invalid_endpoint')
        renderer = value.get('rendererId')
        url = value.get('descriptionUrl')
        if not isinstance(renderer, str) or not renderer or not isinstance(url, str):
            raise ValueError('invalid_endpoint')
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or not parsed.path):
            raise ValueError('invalid_endpoint')
        # Accessing port also rejects malformed or out-of-range port numbers.
        if parsed.port is not None and parsed.port == 0:
            raise ValueError('invalid_endpoint')
        return cls(room_id, renderer, url)


@dataclass(frozen=True)
class EndpointChange:
    kind: str
    room_id: str
    previous: Endpoint | None
    endpoint: Endpoint | None


class EndpointCatalog:
    """Keep configured room identity even when its renderer disappears.

    This class deliberately stores no session IDs, selection timestamps, source
    permissions or ownership leases. Availability is not permission to play.
    """
    def __init__(self, room_ids):
        ids = tuple(room_ids)
        if not ids or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
            raise ValueError('invalid_rooms')
        self.endpoints = dict.fromkeys(ids)

    def reconcile(self, rows):
        if not isinstance(rows, list):
            raise ValueError('invalid_catalog')
        by_room = {}
        for row in rows:
            if not isinstance(row, dict) or row.get('roomId') not in self.endpoints or row['roomId'] in by_room:
                raise ValueError('invalid_catalog')
            room_id = row['roomId']
            value = row.get('endpoint')
            by_room[room_id] = Endpoint.parse(room_id, value) if value is not None else None
        # Validate the whole snapshot before changing any remembered endpoint.
        changes = []
        for room_id, previous in self.endpoints.items():
            endpoint = by_room.get(room_id)
            if endpoint != previous:
                kind = 'unavailable' if endpoint is None else 'bind' if previous is None else 'rebind'
                changes.append(EndpointChange(kind, room_id, previous, endpoint))
        self.endpoints.update({i: by_room.get(i) for i in self.endpoints})
        return changes

# Internal API v1

Node listens at localhost:8787. All routes except `/healthz` require
`Authorization: Bearer <API_TOKEN>`. JSON uses Unix milliseconds. Room IDs are
room UDNs, not renderer UDNs. `api.schema.json` describes state and requests.

| Method | Path | Meaning |
|---|---|---|
| GET | `/healthz` | Process liveness |
| GET | `/readyz` | 200 with fresh topology, otherwise 503 |
| GET | `/v1/state` | Rooms, zones, renderers, source, freshness and capabilities |
| POST | `/v1/arbitration/evaluate` | Non-executable policy evaluation; never a grant |
| POST | `/v1/control` | Select, heartbeat, release or guarded playback command |

Control requests require `roomId` and `action`. Actions:

- `select`: also `selectionId`, a fresh explicit app handshake ID. Returns opaque
  `token` and `expiresAt`. Only enabled, observed single rooms can be selected.
- `heartbeat`: requires `token`; renews active ownership but not unused selection.
- `release`: requires `token`; forgets matching ownership without sending Stop.
- `play`: requires `token`, relay `url`, and optional string metadata fields
  `title`, `artist`, `album`, `mime`. Confirms source before starting playback.
- `pause`, `resume`, `stop`: require `token` and existing playback ownership.
- `seek`: requires `token`, integer `value` in milliseconds, 0–86400000.
- `volume`: requires `token`, integer `value` 0–100.

Control success includes `apiVersion: "1"`. Policy/control failures return 409
with a stable error string (e.g. `ownership_lost`, `grouped_room_not_supported`,
`state_unavailable`, `selection_already_used`); malformed requests return 400,
missing auth 401, unsupported content type 415, oversized body 413. JSON request
limit is 16384 bytes. Unexpected service failures return sanitized 503 responses.
The caller must not retry uncertain commands or automatically reselect.

State includes `positionMs`, `durationMs`, `volume` and `transport` for renderers.
Controlled snapshots add room `enabled`, `controllable`, `owned`; `source:qobuz`
and `protected:false` apply only while a current lease owns fresh state. Raw media
URLs, credentials and lease tokens are never in snapshots. Feature capabilities
describe installed functionality, not whether an account or room is ready.

The dry-run arbitration endpoint retains its v0.1 non-executable response for
compatibility; it is not used for playback authorization. `/v1/control` enforces
the stricter runtime rules. No grouping route exists.

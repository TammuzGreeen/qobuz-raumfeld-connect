# Architecture

```text
Qobuz app ── mDNS / Connect HTTP ──> per-room upstream DiscoveryService
                                            │ explicit selection
Qobuz cloud <── upstream WsManager / QobuzPlayer / Queue / StateReporter
                                            │
                                     RaumfeldBackend
                                            │ authenticated HTTP
                                  Node ownership controller
                                            │ guarded read / write actions
                                     node-raumkernel
                                            │
                                     Raumfeld speakers <── native Spotify

Qobuz audio URL ── HTTP relay with Range support ──> speaker
```

## Upstream integration

- `node-raumkernel` at `2c79c9a3d2c7dcd2d8bc586540d435808142c116`:
  https://github.com/ChriD/node-raumkernel/tree/2c79c9a3d2c7dcd2d8bc586540d435808142c116
- `qobuz-proxy` at `91db990abeb486005a8315a6b1e722df776c4ba4`:
  https://github.com/leolobato/qobuz-proxy/tree/91db990abeb486005a8315a6b1e722df776c4ba4

Both source archives are unchanged. Python composes upstream OAuth, API client,
Connect discovery, WebSocket, queue, playback command/volume handlers, player and
reporter around our `AudioBackend`. We do not launch the standalone DLNA backend
or patch its factory. A tiny DiscoveryService subclass overrides local-address
selection to advertise the explicitly configured LAN interface. Pinned interface
tests catch upstream signature changes before any upgrade.

## Identity and observation

Room UDNs are identity; physical and dynamic virtual renderers are distinct.
Full topology snapshots replace prior room membership. Event subscriptions plus
periodic read-only observations track transport and volume, with bounded waits.
Reads specify UPnP instance 0. Physical Raumfeld renderers can omit GetMediaInfo
and omit URI fields from GetPositionInfo. Only an explicit ENOACTION response
enables a read-only QueryStateVariable(LastChange) fallback. Its new snapshot must
contain exactly one AVTransportURI for instance 0; missing/failed reads never
renew freshness. Cached source/volume events cannot substitute for this query.
Renderer disappearance, host loss, source changes and stale snapshots invalidate
ownership. Generation checks discard reads that race newer events or reconnects.
Private URI evidence remains in Node memory and is omitted from the state API.
Observation diagnostics expose only action/error codes and timestamps, never raw
SOAP bodies or exception messages. Setup separately reports receiver bind/start
failures and checks that LAN_ADDRESS is a locally assigned, non-loopback address.
Spotify is recognized from `spotify:` / `spotifyconnect` transport URI markers,
following ha-raumkernel's source detection, including physical renderer evidence.

## Ownership

The Node controller is the sole route to speaker mutations. Python gets no raw
UPnP devices. Enabling a room in setup permits it to be selected; it does not
grant ownership. Only a fresh Connect discovery handshake requests a selection.
Handshake IDs are deduplicated for 24 hours in Node and the receiver, with bounded
storage. Expiring deduplication entries is a practical replay limit, not a
cryptographic user-intent proof; the Connect endpoint is on the trusted LAN.

A selection token lasts 30 seconds and can only begin with `play`. Initial cloud
Stop/volume snapshots cannot disturb native playback. After URI confirmation,
the token becomes a 15-second renewable lease. Heartbeats keep existing ownership
alive only while state is fresh. A new external URI, topology/member change,
renderer disappearance or host loss revokes it. No automatic path calls select.
Credentials and ownership are separate: tokens are persisted; leases are not.

Commands are serialized conservatively across rooms. Every mutation checks the
current token, enabled room, single-room membership and refreshed observations.
Loading checks the resulting URI before Play and requires Spotify to have
relinquished both physical and virtual renderers. Only initial explicit playback
may call `connectRoomToZone(room, '')` once for an unassigned room. There is no
automatic zone recreation or fallback to a different physical renderer.

Old Python callbacks cannot borrow new permissions: each selection gets new
backend/player/WebSocket objects; the prior backend token is released first.
After native takeover, upstream `release_external_playback` clears Qobuz state
without Stop. Reconnects keep the existing revoked backend and cannot select.
Every pause/resume/stop/seek/volume action remains checked in Node even if an
upstream path bypasses the player's permission callback.

Network actions are not transactional. A source change after dispatch cannot
cancel a SOAP request already sent. Timeout means uncertain outcome, revocation
and no automatic retry. Physical hardware is needed to measure this race and
validate firmware behavior. Tests prove software ordering, not absence of all
network races. Grouped playback is explicitly rejected pending group-wide consent.

## Audio, configuration and lifecycle

The relay uses opaque, unguessable per-track URLs, retains at most four mappings,
forwards Range/If-Range and streams without loading complete tracks into memory.
Node accepts only HTTP relay URLs at LAN_ADDRESS, ports 8790–8839, and the exact
opaque path shape. It does not fetch arbitrary URLs. Actual Qobuz signed URLs
stay in Python memory. Metadata is XML-escaped before reaching UPnP.

The setup API requires the installation token. OAuth callbacks require a
short-lived, single-use state nonce created by an authenticated setup request.
OAuth uses the unmodified upstream desktop flow; account passwords never pass
through this service. Atomic owner-only files under `/data` hold settings and
tokens. UI uses text nodes for renderer names, and no raw account/stream secrets
are returned. The setup interface uses trusted LAN HTTP by default.

The Python supervisor runs both children, forwards shutdown and terminates the
sibling if either dies. Containers run as UID 10001 with a read-only root and a
named data volume. Releases build amd64/arm64, include provenance/SBOM attestations,
and publish version, source SHA and latest tags after tests. Hardware/account
validation remains separate from CI.

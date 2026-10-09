# Upstream-first one-room experiment

## Status and scope

This branch is a separate, **incomplete and undeployed** replacement for the
backend-only direct-DLNA hybrid. The retained Dockerfile/supervisor still starts
the old hybrid; no build from this branch should be presented as upstream-first
until the upstream application integration and safety tests below are complete.
No deployment, publication, speaker commands or runtime credential changes are
part of this milestone.

Target playback path:

```text
Qobuz Android
  -> pinned upstream DiscoveryService / Speaker / WsManager
  -> upstream CommandHandler / QobuzPlayer / reporting / AudioProxyServer
  -> upstream DLNABackend (narrow lifecycle compatibility only)
  -> discovered Raumfeld virtual renderer
```

Keep upstream pinned at `91db990abeb486005a8315a6b1e722df776c4ba4`.
Use the original reconciler's user-observed successful Qobuz behavior as the
acceptance baseline, not the custom receiver's admission/replay behavior.

## Implemented first milestone

- `raumkernel/companion-main.js` is an independent Node entrypoint; it does not
  import the custom controller, binding service, arbitration or receiver.
- `GET /v1/endpoints` exposes configured room identity and the current virtual
  renderer description URL. Reads never create, drop or regroup a zone.
- `POST /v1/zone-for-play` permits one creation attempt for a truly unassigned
  configured room. It cannot repair an assigned missing renderer. Pending or
  uncertain operations cannot be retried until discovery observes assignment.
- The companion API is authenticated and loopback-only. It has no session,
  lease, replay registry, transport, volume or arbitration endpoint.
- `qobuz/endpoint_catalog.py` validates whole snapshots atomically and computes
  bind/rebind/unavailable changes without deleting stable room identity. It is
  a pure planner, **not an implemented upstream hot-rebinding adapter**.

The zone API is an internal capability, not proof of an Android Play. Only the
future upstream explicit-Play integration may invoke it. Background discovery,
token refresh and renderer retries must not invoke it. This call-site boundary
needs integration tests before enabling the candidate.

Validation for this milestone: **8 new Node tests and 4 new Python tests** passed;
the full inherited regression suites passed **103 Node / 85 Python tests**.
Standalone companion startup, authenticated catalog and clean SIGTERM passed
with discovery disabled. Python regressions used a network-isolated container;
Node tests used synthetic services and loopback HTTP. These results do not prove
upstream application integration or hardware acceptance.

## Required next milestone: actual upstream lifecycle

1. Start upstream `QobuzProxy` and upstream `Speaker` instances, not
   `qobuz.service` / `Receiver`. Preserve existing credential storage and LAN
   configuration through an explicit compatibility loader, without importing
   the custom receiver or modifying the original configuration files.
2. Expose a stable upstream speaker even when its room is truly unassigned;
   defer endpoint acquisition to an explicit Play. Stock backend startup currently
   requires an available renderer before discovery is advertised. Add a narrow
   deferred-connection hook rather than another handshake/admission layer.
3. Apply endpoint changes in place. The stock speaker edit API stops/restarts
   the speaker, and stock backend disconnect attempts Stop. Do not use that API
   for maintenance. Retire clients read-only, invalidate old endpoint work, retain
   speaker UUID/session identity, and never load/Play/reactivate on discovery.
4. Reuse upstream external-playback release and command generations. Add only
   native Raumfeld Spotify notifications if virtual URI observation is inadequate.
   After takeover, late cloud activation, natural advancement and old async
   callbacks must not write. Fresh explicit selection plus Play permits takeover;
   no consumed-session-ID blacklist or renewable room ownership lease.
5. Preserve the complete-HTTP-body XML fix independently of the old fenced client.
   Audit stock recovery Stop, mutation retries, volume debounce and shutdown for
   writes that can outlive source authority. Keep necessary fixes narrow; do not
   silently reintroduce physical-forwarding admission checks.
6. Wire a distinct opt-in build/start mode only after synthetic integration proves
   actual upstream discovery/session/player/audio-proxy construction. Retain old
   branches/images unchanged and request separate deployment approval.

## Acceptance

Offline: upstream connect semantics including repeated legitimate session IDs;
initial Play/seek, pause/resume, next/natural completion/reporting; deferred zone
creation from explicit Play only; unchanged/address-changed/missing endpoint;
source release and stale callbacks; no maintenance/shutdown Stop after Spotify;
no uncertain mutation retries or fallback volume setters. Use synthetic fixtures.

Hardware (after explicit deployment approval): audible initial play and steady
connection; pause/resume; small volume and knob/no-echo tests; manual next and
ordinary natural completion; real report responses and user-visible history;
continuous idle Spotify; both source handoffs; disappearance/rebinding. SOAP
acknowledgement, reported PLAYING, audio bytes, audible playback and visible
scrobbles are separate outcomes. The deployed hybrid's failures do not validate
or invalidate this as-yet incomplete architecture.

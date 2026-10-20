# Logical-room DLNA renderer experiment

**Branch:** `development/dlna-renderers`, based on the latest non-prerelease
**v1.0.0**, commit `c5b87d52937fbe62d983da5a4863ff2c8a1e43ba`.
This is not main/latest, a release, or an approved hardware deployment. No Cast
code is included. Existing tags and running stable/Cast services are untouched.

## Implemented, not yet hardware-verified

```
Symfonium / BubbleUPnP (controller, supplies URI + DIDL)
    → persistent bridge-owned UPnP MediaRenderer per logical room
    → stable deferred-room compatibility backend + read-only Node companion
    → currently discovered Raumfeld zone renderer → logical-room speakers
Raumfeld → HTTP media server (direct audio fetch; phone is not the bridge relay)
```

- One root MediaRenderer:1 per configured **logical room**, named `<name> DLNA`.
  Never enumerate physical renderer IDs as targets. A stereo pair stays one room.
  UDN is UUIDv5 of a persistent private `bridge_id` plus the room ID. Endpoint IP,
  zone renderer ID and display name do not affect that identity. Keep the same
  config across restarts; do not create a second instance with the same identity.
- SSDP M-SEARCH responses, alive rotation and shutdown byebye; device/SCPD
  descriptions, SOAP and GENA subscriptions with initial state, renew, expiration,
  unsubscribe, SID and sequence numbers. AVTransport/RenderingControl use
  LastChange. Physical volume/mute and transport/position are polled every two
  seconds per room (plus request-time reads) and evented on change.
- SetAVTransportURI stores URI/DIDL **only in that room's memory**, without
  selection, renderer creation, stopping Spotify, or starting audio. Play is the
  explicit takeover boundary. Stop/Pause/Seek/volume/mute require confirmed source
  ownership; control cannot fall back to another renderer or another setter.
- Existing stable dynamic endpoint validation, single explicit on-demand zone
  demand, single-attempt writes, ABS_TIME seek, volume action verification,
  Spotify evidence and terminal source/endpoint generations are reused. Two tiny
  default-preserving backend hooks select the experimental client; its only extra
  write action is SetMute. No ownership leases, replay registry or repair loops.
- Backing absence does not remove the bridge advertisement. Polling, descriptions,
  queries, subscriptions and reconnects do not create zones or resume playback.
  Failures return SOAP errors; source changes retire loaded transport and pending
  seek intent. Rediscovery updates the backing client without changing the UDN;
  fresh explicit Play is required to load again. Grouped rooms fail with an
  explicit `grouped_room_not_supported` operator limitation; no regrouping occurs.
- The bridge UDNs are excluded from the Node observer, and its manufacturer/model
  are distinct from Raumfeld, preventing feedback through its own advertisements.
- All declared SCPD actions have dispatch implementations. Invalid action/arguments,
  instance/channel, media format, play speed/mode, connection IDs and unsupported
  seeks return UPnP errors. Factory preset application is explicitly rejected; no
  reset, record, next/previous, gapless, grouping or Whole Home is implemented.

### Device stack choice and dependency pin

[async-upnp-client](https://github.com/StevenLooman/async_upnp_client/tree/0.49.0)
**0.49.0** has a server API (`UpnpServerDevice`, `UpnpServerService`, XML serializer,
SOAP validation, SSDP responder/announcer and event subscriptions). It integrates
with the existing aiohttp/asyncio implementation. GUPnP also has real hosted-device
support (`RootDevice` and `Service`), but requires GLib/GObject/Soup/GSSDP and Python
native introspection bindings. It was evaluated, not selected.

The experimental image upgrades **only async-upnp-client 0.48.1 → 0.49.0** over
the stable-derived base; all other resolved stack dependencies match the existing
environment and are hash-locked in `requirements.lock`. Stable's qobuz lockfile is
unchanged. `server.py` uses the pinned stack's description/action/event machinery
with bounded HTTP routing and narrow compatibility fixes: proper SOAP errors,
`uuid:` SIDs, `Second-N` timeouts, expiry-aware renewals, bounded subscriptions,
same-controller literal-IP callbacks, no callback redirects and complete socket/
HTTP cleanup. It does not reimplement SSDP or hand-code the UPnP protocol.

### Audio, formats, progress and queue limits

**Direct HTTP only in this milestone.** Supply a URL reachable by the speakers
with the correct Content-Type and suitable range/time-seek support at the media
server. No relay, transcoder, phone proxy, URL credential/header injection, HTTPS
fetch bridge or artificial range responses are implemented. Do not use a controller
option that proxies audio through the phone. Start with a known-seekable FLAC or MP3
served on the speaker LAN, not a phone-hosted file or an expiring cloud URL.

ConnectionManager reports the backing renderer's exact `http-get` audio/flac,
audio/x-flac, audio/mpeg, audio/wav, audio/x-wav and audio/L16 entries, filtered to
those actually reported. No wildcard/video/transcoding profiles or seek flags are
invented. Until a backing renderer can be queried, SinkProtocolInfo is empty; some
apps may filter that target until capabilities are known. The SSDP target itself
remains advertised. Play rejects a type absent from the backing capabilities before
sending SetAVTransportURI. Supply matching resource protocolInfo in DIDL, or a
recognized URL filename extension. Codec/header/bit-depth compatibility still
requires hardware tests; a format declaration is not audible evidence.

Prepared/stopped/paused Seek records **pending intent**, not applied position, and
returns success for accepting that intent. Position/LastChange retain actual
readback, not the requested target. On explicit Play the initial sequence is
SetAVTransportURI → acknowledged ABS_TIME Seek (if pending) → Play. Paused resume
applies a pending Seek only after explicit Play arrives. Raumfeld Seek can itself
resume audio, so no seek is sent during paused scrubbing. Failed/unacknowledged
seeks are not reported as applied; invalid or unsupported modes return errors.
Timing, truly audible nonzero starts and app interpretation of deferred seek
success are **pending hardware acceptance**, not certified by synthetic SOAP.

There is **one prepared item**, no persistent bridge queue and no SetNextAVTransportURI.
The controller supplies subsequent items. Direct delivery can continue the current
track with the screen off, but does not prove phone-independent queue advancement.
No Audiobookshelf account-progress integration, Qobuz reporting from DLNA media,
Last.fm scrobbling or listening-history acceptance is claimed.

## Build (Linux amd64 tested)

From this branch's repository root; no registry publication or deployment:

```sh
git switch development/dlna-renderers
docker build --build-arg PLAYBACK_PATH=upstream_first \
  -t raumfeld-connect:dlna-base-c5b87d5 .
export DLNA_IMAGE="raumfeld-connect:dlna-renderers-$(git rev-parse --short=12 HEAD)"
docker build -f experiments/dlna/Dockerfile \
  --label org.opencontainers.image.revision="$(git rev-parse HEAD)" \
  -t "$DLNA_IMAGE" .
```

Use a unique tag, not stable/latest. ARM64 compilation/hardware has not been tested.
The base name identifies its stable source ancestry, not byte-for-byte image identity.
Preserve the existing deployed stable image separately for exact rollback.

## Private state and standalone Compose

Keep runtime files, `.env`, backups and logs **outside Git**. Create a separate
directory, readable by container UID/GID `10001:10001`; copy the **logical room IDs**
and names from your private stable config, not individual physical renderer IDs.
Do not mount the live stable data directory or copy account credentials here.

Create `<PRIVATE_TEST_DIR>/config.json`:

```json
{
  "bridge_id": "<GENERATE_ONCE_WITH_uuidgen_AND_KEEP_IT>",
  "rooms": [
    {"id": "<LOGICAL_KITCHEN_ROOM_ID>", "name": "Kitchen", "dlna_port": 8920},
    {"id": "<LOGICAL_OFFICE_ROOM_ID>", "name": "Office", "dlna_port": 8921}
  ]
}
```

Add every supported logical room, each once, each with a free distinct TCP port
8900–65000. Do not change existing stereo topology. Room list order/addresses do
not define the UUID. Configuration is loaded at startup; restart for config edits.
Media/transport state is intentionally not restored after a restart.

Use this **standalone** Compose (also `experiments/dlna/compose.yml`):

```yaml
services:
  dlna-renderers:
    image: ${DLNA_IMAGE:?unique local experimental image tag}
    pull_policy: never
    network_mode: host
    init: true
    restart: "no"
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    environment:
      API_TOKEN: ${DLNA_API_TOKEN:?separate random token of at least 32 characters}
      LAN_ADDRESS: ${LAN_ADDRESS:?Docker host speaker-facing IPv4}
      RAUMFELD_HOST: ${RAUMFELD_HOST:-}
      API_PORT: 8877
      CONFIG_PATH: /data/config.json
    volumes:
      - ${DLNA_TEST_DATA:?absolute private test directory}:/data:ro
    stop_grace_period: 15s
```

Set placeholders in an external private environment file. Use a **new** token
(`openssl rand -hex 32`) for the experiment. Retain existing stable credentials and
token unchanged in their original private storage. Host networking on a trusted
Linux LAN is required: SSDP UDP 1900 multicast/unicast and each configured TCP port
must be reachable by controllers. The companion is loopback-only on **8877**, not
stable's 8787/8788. Never expose unauthenticated UPnP control ports to the Internet.
GENA callback URLs must use the requesting controller's literal IP (not a hostname
or a different notification host); callbacks expire and are limited to 32/service.

Local categorical status (no media URLs or room IDs) is at
`http://<LAN_ADDRESS>:<ROOM_DLNA_PORT>/bridge-status`, accessible from the bridge
host only. It shows unavailable/grouped status and whether seek intent is pending.
Device descriptions, SOAP and subscriptions remain accessible on the LAN.

### Controlled switchover — approval required, not performed

Separate state/ports/UDNs do **not** make two room-control engines safe to run
unattended together. The experiment starts its own discovery companion and no
Qobuz receiver; the stable Qobuz app must be stopped during the approved test.
This is not a concurrent Qobuz+DLNA integration milestone.

1. Record the stable app/image digest, environment, private data backup and current
   room/source state. Confirm rollback image is present. Do not change Cast's app.
2. Obtain approval for the test window. Stop the stable app normally; verify its
   control processes have exited. Do not remove images, volumes, apps or backups.
3. Verify 8877 and all configured DLNA TCP ports are free, the config uses the
   intended logical rooms, and no other instance advertises this `bridge_id`.
4. Start the standalone experiment only after approval, e.g.:
   `docker compose --env-file <PRIVATE_ENV> -f experiments/dlna/compose.yml -p dlna-test up -d`.
5. Advertise the full configured inventory, but validate playback on **one room
   first**, at low volume. Idle rooms must remain idle/native Spotify.
6. Roll back: stop the experiment normally (byebye; shutdown sends no transport
   Stop), verify it exited, then start the original stable app with its unchanged
   image/config/data. Remove stale controller selection by refreshing discovery,
   not by deleting stable state or rebuilding zones. Recheck Qobuz and Spotify.

No current NAS, offsite installation, speaker topology or Cast service was changed.

## Automated verification

Build the disposable test image and run without network access, except the isolated
SSDP test which uses a private internal Docker network (never `--network host`):

```sh
docker tag "$DLNA_IMAGE" raumfeld-connect:dlna-renderers-initial-review
docker build -f experiments/dlna/Dockerfile.test -t raumfeld-connect:dlna-offline-tests .
docker run --rm --network none raumfeld-connect:dlna-offline-tests
docker network create --internal dlna-offline
docker run --rm --network dlna-offline --entrypoint /app/qobuz/.venv/bin/python \
  raumfeld-connect:dlna-offline-tests tests/dlna_ssdp_smoke.py
docker network rm dlna-offline
```

Wire tests exercise real stack XML/SOAP/GENA handlers and two independent synthetic
backing renderers, including a stereo-pair membership, direct HTTP/range fetch,
metadata preservation, prepared/paused/nonzero seeking, actual volume/mute readback,
physical-volume events, missing/rebound endpoints, grouped rooms, source changes
during load, terminal Spotify/other-URI takeover, invalid SOAP/media/DIDL, failed
mutations without retry and failure-aware position feedback. UDP smoke checks real
M-SEARCH, alive and byebye packets for both targets. Original playback/authority
regressions and Python→Node→synthetic-audio tests are retained.

Results are recorded in `RESULTS.md`. Synthetic bytes, a SOAP acknowledgement,
advertisement registration and phone visibility/audible playback are different
evidence levels. None substitutes for the hardware plan below.

## Pending hardware acceptance (after switchover approval)

1. **Inventory:** in both Symfonium and BubbleUPnP refresh renderers. See exactly one
   `<room> DLNA` per configured logical room; stereo pairs stay together. Record
   target count/UUIDs privately. No duplicate on bridge restart or app refresh.
2. **First room:** disable phone/audio proxying and choose a known speaker-reachable
   HTTP FLAC/MP3 source. Select one room in Symfonium, then repeat separately in
   BubbleUPnP. Confirm metadata and **audible** output only in that room.
3. **Transport/seek:** Pause → wait (silence) → scrub paused (still silence) → Play.
   Confirm audible nonzero position and matching feedback. Test prepared nonzero
   starts, playing Seek, Stop → Play and duration/position. A 710 rejection must
   leave actual position unchanged, not claim the requested target was applied.
4. **Volume:** change app volume at safe levels, then the physical knob. Confirm
   actual sound and controller feedback (allow poll latency), no jump-back. Test
   mute only if the backing renderer exposes it; unsupported must return an error.
5. **Screen off:** keep phone proxying disabled, turn screen off for several minutes.
   Confirm the current track continues and audio comes from the media server.
   Separately test next-track advancement; record controller dependence, not a
   bridge-owned queue or phone-independent playback claim.
6. **Isolation:** repeat playback/transport/volume on a second room while the first
   plays. No cross-room URI, Stop, volume or state changes. Then repeat remaining
   logical rooms; do not test Whole Home or regroup speakers.
7. **Spotify:** while bridge idle, native Spotify must play uninterrupted, including
   during discovery refresh/subscriptions. Explicit DLNA Play may take over only
   the selected room. Switch back to Spotify; stale DLNA pause/seek/volume must
   fail without interruption, and no background task may reclaim it. Fresh DLNA
   Play is a new explicit takeover. After rollback, recheck stable Qobuz handoffs.
8. **Endpoint lifecycle:** with approved disappearance/reconnection procedure,
   observe a backing renderer vanish/reappear. The bridge UDN remains the same,
   no duplicate target/zone creation, no automatic resume. Playback failure is
   reported; fresh Play resolves the new endpoint. Do not split/regroup stereo pairs.

**Hardware results: pending.** Audiobookshelf progress/account integration is out
of scope and must not be inferred from a successful DLNA test.

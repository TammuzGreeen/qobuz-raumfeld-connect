# Experimental Cast receiver — diagnostic checkpoint

Branch `development/chromecast-receiver`, created from the verified remote stable
tag `v1.0.0` at `c5b87d52937fbe62d983da5a4863ff2c8a1e43ba`, not from the
then-current experiment branch. Working tree was clean. Stable/default launchers,
production configuration/storage/credentials, private rollback images and stable
tags are not changed. No hardware deployment is authorized by this experiment.

Tested candidate source: **`5f6a2562cbbc3f0e975dc75a39032663d84d419b`**.
Local-only amd64 image: **`raumfeld-connect:cast-diagnostic-5f6a2562cbbc`**.
The later publication checkpoint changes documentation/records only; its commit
is the branch HEAD. Exact image/base/archive identities are in [checkpoint.json](checkpoint.json).

## Assessment: blocked before legitimate stock Android media reception

Source inspection identifies a device-authentication trust boundary, **not a live
Android test result**. No official Audiobookshelf Android connection has yet been
tested. Stop at a useful safe diagnostic receiver rather than building misleading
speaker playback/grouping behind an unavailable front end. No DLNA target/front-end
fallback is implemented; DLNA remains the intended internal Raumfeld transport.

Both evaluated emulator projects require real Google-issued receiver device
credentials for validating stock Cast SDK senders. Self-signed TLS can establish a
socket, but self-signed **device-auth** certificates cannot chain to the sender's
Cast trust anchors. A receiver-app developer registration does not issue hardware
device credentials. Do not extract device keys, use certificate rental services,
disable sender verification, modify the Android app or claim a pychromecast pass
proves stock SDK compatibility. Legitimate next options: investigate a licensed
Cast receiver integration with the vendor/Google, or an actual certified Cast
device with an authorized receiver/forwarding integration (separate hardware/audio
path, not this emulator). These are options, not established solutions.

Primary trust-boundary evidence: pinned Open Screen
[`boringssl_trust_store.cc`](https://chromium.googlesource.com/openscreen/+/978e90d34a539bb6580634e1e67b0d673869f11c/cast/common/certificate/boringssl_trust_store.cc)
defines Cast Root CA and Eureka Root CA anchors;
[`cast_cert_validator.cc`](https://chromium.googlesource.com/openscreen/+/978e90d34a539bb6580634e1e67b0d673869f11c/cast/common/certificate/cast_cert_validator.cc)
requires a valid device certificate path. The selected emulator's pinned
[`crypto.rs`](https://github.com/yukarikaname/openchromecast/blob/0cc510b2e0fd8c484029ccf8ab08f527d6ad752c/src/crypto.rs)
documents the self-signed device-auth limitation. Our fixture does not import or
alter these Google trust anchors; its explicit public TLS cert pin is test-local.

## Evaluated implementations (pins in sources.json)

| Project | Current inspected activity | Fit / decision |
| --- | --- | --- |
| openchromecast 1.0.5, MIT | Not archived; last push 2026-10-08 | Best URL-flinging/media starting point. Rust/Tokio/rustls/prost/RSA; normal build also GTK/tray/winit and mpv/VLC. Upstream reports VLC Android and pychromecast, not ABS SDK success. Selected **only its pinned identity/protobuf modules** for a headless diagnostic, not its optimistic media/application handlers. |
| go-cast, MIT | Not archived; last push 2026-08-02 | Primarily streaming VP8; README says audio unsupported. Requires certificate manifest; rooted-device/service instructions are outside constraints. Not selected. |
| Google Open Screen, BSD-3-Clause | Main inspected at pinned revision | Reference discovery/control and Cast Streaming/remoting, not a ready URL-audiobook receiver. GN/depot_tools/clang plus media libraries. Does not remove device-auth or ABS custom application requirements. Not selected. |

Upstream openchromecast reports every queried app available and synthesizes launch
sessions without hosting the actual requested app. We deliberately do **not** reuse
that behavior: claiming ABS app launch just because FD1F76C5 was echoed is not
application compatibility. Diagnostic receiver will report no applications and
explicit launch/media errors, never successful load/position/audible output.
No mpv/VLC decoding or phone audio relay is included in this checkpoint.

## Audiobookshelf source inspection

Pinned current Android source (not necessarily the user's installed version):

- `CastOptionsProvider.kt`: Google `CastOptions` / `CastContext`, custom app
  **FD1F76C5**, not the default media receiver ID. Manifest registers this provider.
- `player/CastPlayer.kt:load`: MediaLoadRequestData with queueData (items, audiobook/
  podcast type, startIndex, startTime, no repeat), currentTime, autoplay and rate.
  Supporting a single default-receiver LOAD alone is insufficient.
- `data/PlaybackSession.kt:getQueueItem`: buffered MediaInfo with contentId/
  contentUrl, MIME, audiobook-chapter metadata, queue-item playback duration.
  No required customData is set in this inspected queue/load builder. Receiver
  FD1F76C5 behavior is **not** therefore proven identical to default receiver.
- `player/CastPlayer.kt`: RemoteMediaClient status and progress callbacks update
  sender position; `PlayerNotificationService` switches its player to CastPlayer.
  `media/MediaProgressSyncer.kt` and `server/ApiHandler.kt` implement sender progress
  synchronization to `/api/session/{sessionId}/sync`. Do not add another writer.
  Actual account-visible Cast progress (especially phone sleep/disconnect) remains
  unverified, not established by reading the sender.

`PlaybackSession.getContentUri` appends the account token to server media URLs.
Such URLs must remain private; this experiment never fetches or logs them.
Source URLs/headers/tokens and long-book Range/format compatibility must be checked
only after authenticated receiver and ABS application support. Never log URLs or
media payload/customData. Prefer direct server-to-speaker fetch; any later relay
must allowlist source origins, handle Range/If-Range/206/416, preserve auth privately,
avoid arbitrary URL fetch/SSRF and never use phone forwarding as the normal path.

## Implementation boundary and grouping

Implemented an opt-in, isolated diagnostic TLS/auth target and loopback GUI, not a
working speaker bridge. It has no speaker mutation client and no credential import
option. Distinguish TLS/auth response sent from **sender acceptance**, and separate
discovery, auth, app launch, media load, audible playback and feedback results.

The GUI's “Group all rooms” control is explicitly blocked at this checkpoint,
including on the server. Individual selection and an explicit configured membership
set are preparatory only. Show actual observed topology, unavailable participants,
pending=false and explicit failure, never synthetic success. Never group on page
load/startup/refresh. Even stopped playback cannot override the auth/application
block. Native grouping/ungrouping, private pre-mutation topology snapshot/recovery
and synchronized audio are **not implemented or hardware tested** until a viable
Cast input exists. Stable Qobuz's single-room/group rejection remains untouched.

Future grouping must require all affected sources stopped, snapshot privately before
one explicit native change, serialize conflicting logical-room membership sets,
preserve stereo-renderer identities, observe actual result and fail closed without
retry on uncertain mutation. Off means individual logical rooms, not splitting
physical renderers or reconstructing arbitrary prior groups. Recovery to a previous
topology is a separate explicit action, not restart repair. Future transport must be
source-neutral, retain requested offsets separately from acknowledged applied ones,
defer paused seeks until explicit Play and invalidate old operations on member/source/
endpoint change, with no lease/replay timer or automatic Spotify reclamation.

## Hardware checks — all pending, deployment approval required

1. Approve isolated diagnostic installation only, with distinct writable storage and
   nonconflicting ports. Do not mount production /data or start a second Qobuz process.
2. Open unchanged ABS Android Cast picker; record app version and whether the one
   experimental target appears. Discovery alone is not SDK acceptance.
3. Select it; record connection failure/result and categorical auth/launch counters.
   Expect device-auth rejection or unavailable application; no extracted keys or
   sender changes are permitted. Do not troubleshoot this by weakening verification.
4. If a legitimate authenticated/app-compatible route is later established, obtain
   separate speaker-test approval before audible load/nonzero resume/pause/seek,
   actual position/volume/physical knobs, completion/next, screen-off playback,
   independent account-visible progress and another unchanged Android Cast app.
5. Only then test idle Spotify continuity/handoffs, stopped-only native all-room
   grouping/ungrouping/stereo preservation and synchronized group audio. Pending.

No hardware compatibility, account progress, audible output or grouping pass is
claimed. No stable deployment replacement or production release is part of it.

## Architecture and configuration

Only `experiments/cast/service.py` starts the experiment; stable launchers never
import it. `CAST_EXPERIMENT=0` exits before reading config, opening sockets or
writing data. The opt-in supervisor starts one Rust TLS listener and one local,
bearer-authenticated Node **read-only** observer. The Rust listener uses only the
two hash-checked upstream identity/protobuf modules; Cargo.lock fixes transitive
dependencies. License texts/manifests are retained in the image. No receiver key
import exists. Auth signing uses nonce + peer TLS certificate, but signing and
cryptographic validity are not sender trust or successful device authentication.

The source-neutral `Transport` protocol is preparatory, with **no speaker backend**.
No Play/Pause/Stop/Seek/volume, room provisioning, native grouping or media relay is
implemented. Native Spotify cannot be replaced by this diagnostic. Accordingly,
handoff retirement across group membership is still future work, not claimed from
the preserved Qobuz regressions. All media commands receive an error, including
nonzero start/paused seek; no requested offset is reported as confirmed applied.

| Setting | Default / meaning |
| --- | --- |
| `CAST_EXPERIMENT` | `0`; exact `1` enables diagnostic only |
| `CAST_DATA_DIR` | `/cast-data`; isolated writable storage, never production `/data` |
| `CAST_BIND_ADDRESS` | `127.0.0.1`; explicit local IPv4 for TLS/mDNS, not wildcard |
| `CAST_PORT` | `8809`; advertised experimental Cast port, verify availability |
| `CAST_ADVERTISE` | `0`; opt-in `1` advertises exactly one `_googlecast._tcp` target |
| `CAST_OBSERVE_ROOMS` | `0`; opt-in `1` starts existing read-only Raumkernel discovery/polls |
| `CAST_API_TOKEN` | Fresh random per start unless explicitly provided; not a production token |

GUI listens **only on 127.0.0.1:8799**, private observer on **127.0.0.1:8817**.
Experimental config is `/cast-data/config.json`: `targetName`, `selectedRoom` and
an explicit `rooms` list of `{id,name}` logical-room entries. Use private actual
logical IDs, never individual stereo members. Example config is synthetic. Selected
room is RAM-only and has no transport effect. Room discovery/refresh is read-only;
with observation disabled rooms correctly remain unavailable/unknown. It does not
share production config, credential cache, discovery/audio listeners or Qobuz
sessions. UDP 5353 is shared mDNS infrastructure, not a second Qobuz advertisement.

The GUI token is stored mode 0600 at `/cast-data/gui-token`. It is entered manually,
kept in page RAM, never put into a URL, HTML response, browser storage or logs.
GUI needs an authenticated status read; no auto request or grouping occurs on load.
Read-only topology snapshots are saved only by explicit button/API request, in
`/cast-data/recovery/` with mode 0600 and private logical IDs. No auto restore exists.
TLS certificates are short-lived (upstream three-day lifetime); restart the
diagnostic only when approved if expiry impedes later connection testing.

## Verified results and exact compatibility boundary

| Phase | Result at this checkpoint |
| --- | --- |
| Discovery | One-target mDNS records/interface/stable-ID unit test passes. No live multicast/Android discovery claim. |
| Device authentication | Offline TLS framing + signed nonce/certificate exchange passes; signature and changed-nonce failure unit tested. Self-signed auth certificate is not Google-trusted. Sender acceptance explicitly remains `unverified`; no stock Android test performed. |
| Application launch | `FD1F76C5` and default app reported unavailable; explicit LAUNCH_ERROR tested. No custom receiver host or fake launch success. |
| Media loading | LOAD rejected; no media URL fetch, audio relay or invented media session. Nonzero-position fixture confirms rejection, not resume support. |
| Audible playback | Not implemented; listening/phone tests pending, not a pass. |
| State/control feedback | Diagnostic counters and actual/unknown topology GUI tested. No media position/duration/item, volume, seek, completion or physical-control feedback implementation. |

Offline verification: **4 Rust tests**, **9 experimental Python tests**, packaged
TLS/framing/auth/app/media/oversized-frame/GUI/storage/shutdown smoke, JS syntax,
default-disabled launch and synthetic Compose validation all pass. GUI HTTP and
DOM-safety source checks are not a real browser usability test. All network tests
ran in `--network none` containers (loopback only), without host discovery or
speaker commands. The diagnostic fixture keeps TLS validation enabled and pins
only its generated public test certificate; it is not an Android SDK test.

Stable regression: **188 Python tests** and **117 serial Node tests** pass.
Initial Python/Node invocations used an inappropriate inherited PLAYBACK_PATH/
missing dev dependency environment; corrected test-only environment passed without
source changes. One Node run then reproduced the already documented guarded-path
short-deadline `ownership_lost` versus `source_not_confirmed` variance; unchanged
serial rerun passed. No assertion or production code was weakened. Installed
Qobuz/Raumkernel/shared modules plus patched player/SDK match the stable-base image
byte-for-byte across **31 files**. This is preservation evidence, not new hardware
Spotify/Qobuz handoff or simultaneous household playback acceptance.

All outgoing added/changed files and experiment commits were reviewed for private
configuration, tokens, signed URLs, household IDs and raw logs; only synthetic
examples and public source/image identities are included. Raw research, build/test
logs and image archive stay outside Git. No registry image, release or production
deployment is published. Stable main/tags are not merge/push targets.

## Reproducible local build (not installation)

Use the verified stable source as base, **not a registry latest image**. The base
still contains the working Qobuz stack but the experimental CMD never starts it.
Build on a separate host/context if production must not build anything.

```sh
BASE=$(mktemp -d /tmp/opencode/cast-base.XXXXXX)
git archive c5b87d52937fbe62d983da5a4863ff2c8a1e43ba | tar -x -C "$BASE"
docker build --platform linux/amd64 --build-arg PLAYBACK_PATH=upstream_first \
  --label org.opencontainers.image.revision=c5b87d52937fbe62d983da5a4863ff2c8a1e43ba \
  -t raumfeld-connect:cast-stable-base "$BASE"
REV=$(git rev-parse HEAD)
IMAGE="raumfeld-connect:cast-diagnostic-$(git rev-parse --short=12 HEAD)"
docker build --platform linux/amd64 -f experiments/cast/Dockerfile \
  --build-arg STABLE_IMAGE=raumfeld-connect:cast-stable-base \
  --label org.opencontainers.image.revision="$REV" -t "$IMAGE" .
```

Build requires internet for pinned sources/packages. The Rust compiler image and
openchromecast archive are digest/hash pinned; stable Python/Node locks and Cargo
lock are retained. Layer/build timestamps mean bit-for-bit digest reproduction is
not promised. Base npm installation reports existing dependency advisories (3
moderate, 3 high); no stable dependency upgrade or security certification is claimed.

Offline tests (no host networking, no speakers, no persistent volume):

```sh
docker run --rm --network none --read-only --tmpfs /tmp \
  --entrypoint /app/qobuz/.venv/bin/python "$IMAGE" /app/experiments/cast/smoke.py
docker run --rm --network none --read-only --tmpfs /tmp \
  -w /app/experiments/cast --entrypoint /app/qobuz/.venv/bin/python "$IMAGE" \
  -m unittest discover -p 'test_*.py'
```

## Proposed installation — DO NOT execute without approval

1. Transfer/load the uniquely tagged **local** candidate and verify its revision
   label/image ID against the checkpoint. Do not pull a similarly named registry
   image. Leave the stable app, Compose, `/data`, credentials and rollback images
   untouched. Confirm 8799/8809/8817 are unused on the intended test host and there
   is no competing test target. Do not start a second Qobuz supervisor.
   The prepared private `cast-diagnostic-5f6a2562cbbc.tar` archive has SHA-256
   `51ea7eba47b80e0c468cdde2bc608a429b3f37c0d33af37dd66f520a70f2bdd4`.
   After approved transfer, run `sha256sum` against that value, then `docker load
   -i cast-diagnostic-5f6a2562cbbc.tar`; inspect the loaded revision label before use.
2. Make a private config from `config.example.json`, with actual configured logical
   room IDs/labels, initially one test room if desired. Store outside Git. No room is
   controlled in this diagnostic, so stable retains speaker ownership throughout.
3. Seed only the dedicated named volume (default Compose also uses this name):

   ```sh
   docker volume create raumfeld-cast-experiment-data
   docker run --rm -i --network none --read-only \
     -v raumfeld-cast-experiment-data:/cast-data \
     --entrypoint /app/qobuz/.venv/bin/python "$IMAGE" \
     -c 'import os,pathlib,sys;os.umask(0o077);pathlib.Path("/cast-data/config.json").write_text(sys.stdin.read())' \
     < /private/path/cast-config.json
   ```

4. Set `CAST_IMAGE` to the exact candidate tag and `CAST_BIND_ADDRESS` to a local
   LAN IPv4. Inspect `docker compose -p cast-experiment -f
   experiments/cast/compose.example.yml --profile cast-experiment config` privately.
   Optional `CAST_OBSERVE_ROOMS: "1"` enables only read-only observations; the
   provided example leaves even that off. After approval, launch with the same
   Compose arguments plus `up -d`. No blind retries if startup fails.
5. Read `/cast-data/gui-token` privately from this container, tunnel localhost:8799
   if remote, and open the loopback GUI. Do not expose the GUI on a LAN reverse
   proxy or publish private topology/status/log captures. Test phone discovery and
   attempted connection only; listen/transport tests remain blocked.

## Rollback / recovery

Stop only the experimental Compose project with `docker compose -p cast-experiment
-f experiments/cast/compose.example.yml --profile cast-experiment down` (retain the
same required environment values). **Never use `down -v`**, restore the production
volume or delete rollback images. Keep the dedicated experiment volume privately
for evidence/recovery. Stopping withdraws the test mDNS target and closes experiment
ports; it sends no speaker Stop/standby or topology commands. Since this candidate
cannot change topology or production state, normal rollback needs no room recovery
or stable app restart. Saved snapshots are evidence, not an executable restore plan;
any future manual/native topology restore needs separate approval and fresh reads.

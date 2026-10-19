# Experimental Cast receiver — diagnostic checkpoint

Branch `development/chromecast-receiver`, created from the verified remote stable
tag `v1.0.0` at `c5b87d52937fbe62d983da5a4863ff2c8a1e43ba`, not from the
then-current experiment branch. Working tree was clean. Stable/default launchers,
production configuration/storage/credentials, private rollback images and stable
tags are not changed. No hardware deployment is authorized by this experiment.

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

Source URLs/headers/tokens and long-book Range/format compatibility must be checked
only after authenticated receiver and ABS application support. Never log URLs or
media payload/customData. Prefer direct server-to-speaker fetch; any later relay
must allowlist source origins, handle Range/If-Range/206/416, preserve auth privately,
avoid arbitrary URL fetch/SSRF and never use phone forwarding as the normal path.

## Implementation boundary and grouping

Prepare an opt-in, isolated diagnostic TLS/auth target and loopback GUI, not a
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
claimed. Installation/build/test/recovery details will be added at the implementation
checkpoint. No stable deployment replacement or production release is part of it.

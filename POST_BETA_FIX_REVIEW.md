# Post-beta fixes: split start position and legacy room label

The beta `upstream-first-beta-2026-10-15` remains immutable. These fixes are a
separate candidate, not a retroactive change to beta implementation or acceptance.
No household names/IDs, credentials, runtime snapshots or raw logs are published.

## Automatic nonzero start after Spotify

Read-only beta diagnostics establish the exact sequence on the original room:
after Spotify release and fresh activation, a PAUSED snapshot arrives with the
same track/item and a positive position while the player is STOPPED. Player seek
returns false (no owned loaded transport). A subsequent PLAYING-only delta has
no position, so playback starts at zero. Manual paused/playing seek takes a
different path and works. This is not a renderer rejection of a start Seek: no
post-start Seek is issued in this sequence.

Identical actual protobuf → pinned upstream handler/player → synthetic room DLNA
replay reproduces the beta failure: URI, Play, no Seek. The new hash-checked isolated
fourth player backport retains a desired start offset while STOPPED/LOADING, without
changing the applied clock, seeking another source or inventing Play. Later explicit
Play consumes it once through existing post-start acknowledged Seek. Zero replaces
an older target, out-of-range targets clamp at application, rejection is not success
and does not trigger retry. Stop, new track/queue occurrence, source/session loss,
fresh selection and endpoint retirement clear it. All room/source generations and
pre-URI fresh evidence checks remain unchanged. No source lease/replay cache/timer.

Tests cover the actual post-release same-track snapshot, initial startup, both
position/Play orders, feedback-only messages, latest/zero/clamped targets, rejection,
late old-selection callbacks, independent rooms and invalidation paths. Existing
Next, playing/paused seek, silence until explicit Play, handoff and volume suites
remain required. Synthetic success is not audible hardware acceptance.

## Original-room display label

Private read-only inventory/config comparison shows that only the original room
uses the older generated `logical-name + " Qobuz " + last-six-room-ID-characters`
label. The other five use `logical-name + " Qobuz"`. The suffix resembles a release
tag, but its source is the old setup naming rule. No release version is added by
the upstream launcher.

Runtime loading now removes only the exact legacy generated suffix matching that
room's ID; arbitrary custom labels, other suffixes and stable room IDs are preserved.
The normalized name feeds both upstream discovery/display metadata and backend
labels. Original config.json/credentials are not rewritten, UUIDs and discovery/
audio ports do not change. Future guarded setup also uses the logical Qobuz label
without appending ID fragments. Regression tests use synthetic names only.

## Deployment/publication boundary

Build and offline tests are preparation only. The running beta, storage, existing
tags and rollback images remain unchanged until separate correction deployment
approval. No agent speaker-changing tests are authorized here. After deployment,
verify the logical label in Qobuz (cached old mDNS entries may need discovery
refresh), then user-operate Spotify → fresh Qobuz selection with a nonzero paused
position → explicit Play. Confirm audible position, steady connection, no source
reclamation and existing paused/playing seek behavior on the exact candidate.

## Candidate and offline results

- Implementation revision: `1917f07001545ba01bb655a496af29eafaa4f9ba`.
- Local amd64 image: `raumfeld-connect:upstream-first-beta-fixes-review`.
- Local OCI index:
  `sha256:f6e93f67d1f9a3eedeee66b278818fcdb22912038e72ceab3421924c14f0fc90`.
- Docker image config digest (distinct from index):
  `sha256:e998c73001369cfe57e658bf9980196b62c9b8dc4b591d8d4c88e55da29c0119`.
- Private image archive SHA-256:
  `74923a9fad46ebf79e05a689ccede3c214be5446c391f865f77a94e5c17ea1d1`.
- Installed patched player SHA-256:
  `7b914d0e0c58eea39ab6d5195c9c6871eb59ec59093b1d4485104b24342a0910`.
- 200 Python tests (188 project + twelve standalone upstream contribution tests),
  117 serial Node tests, 34 packaged Node observation/boundary tests and six original
  upstream serialization tests passed on the applicable fixtures/build.
- Six-room offline supervisor smoke passed, including legacy label normalization in
  real upstream speaker status, stable storage, health/startup and clean shutdown.
  No startup creation or transport writes. Future setup labels also tested without
  ID suffixes. Start-offset diagnostics expose absent/zero/positive categories only,
  never position values, track IDs, URLs, household identifiers or session tokens.

No new candidate deployment or hardware-control test has occurred. The current
beta remains running with its known bugs until separately approved update and
user-operated checks. No beta tag/release replacement or merge to main.

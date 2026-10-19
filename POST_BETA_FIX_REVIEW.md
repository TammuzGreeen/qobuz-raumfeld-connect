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

Build and offline tests were preparation only; deployment required the separate
approval recorded below. Existing tags and rollback images remain unchanged.
No agent speaker-changing tests are authorized here. After deployment,
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

The candidate was subsequently deployed with separate user approval; see below.
No beta tag/release replacement or merge to main.

## Approved deployment — 2026-10-15

User explicitly approved deploying this correction candidate after offline review.
Deployed the image above once through app.update, after checksum/source/architecture
verification. Imported config digest matches
`sha256:e998c73001369cfe57e658bf9980196b62c9b8dc4b591d8d4c88e55da29c0119`.
Retained fresh private Compose/config/runtime snapshots, complete persistent-storage
backup and exported all-room beta image, plus previous rollback images.

Verified healthy/authenticated, zero restarts, expected player/SDK hashes and six
configured/discovered logical rooms. Credentials, optional token cache and
config.json bytes are unchanged. Environment, mounts, host networking, physical
membership, UUIDs/ports and unrelated running containers are preserved. Zero
startup zone-creation or transport commands; no agent speaker-changing controls.

All six discovery/audio listeners respond, and independent LAN mDNS observation
confirms all expected stable device UUIDs and ports. The original room now advertises
the logical Qobuz label without its suffix; the other five names are unchanged.
The original room's upstream web-UI ID (a display-name-derived slug) changes as
expected; this is distinct from the unchanged Qobuz discovery device UUID.
Verification initially compared that UI slug as if it were a stable device ID;
inspection of pinned Speaker.get_status confirmed the distinction. Verification
also corrected its mDNS TXT field lookup to the pinned SDK's actual `Name` key.
Neither read-only verification correction caused a second deployment or transport
write. Final checks passed against actual UUID/Name/port advertisements.

At deployment, audible automatic nonzero start and phone-visible label acceptance
were pending. The subsequent user result below updates those checks, without
copying every earlier beta hardware pass onto this image. No change to main,
existing prerelease tags or release assets.

Rollback: stop the test app and restore the new private pre-update Compose using
`raumfeld-connect:upstream-first-all-rooms-review` with pull_policy never through
the supported app.update dictionary operation. No room-config migration occurred,
so config.json needs no restoration. Preserve refreshed credentials/cache; the
complete volume backup is disaster recovery only. Earlier seeking-capable/current
and original preview rollback images remain available separately.

## User acceptance — 2026-10-19

After being asked to check the phone-visible room name and Spotify → Qobuz handoff
at a nonzero track position without manual seeking, the user confirmed: “checks
done, everything working right now”. Record both requested correction checks as
passed on deployed implementation `1917f07001545ba01bb655a496af29eafaa4f9ba` and
the image digest above, with a general current-working observation.

This is user-observed hardware acceptance of these fixes, not a newly captured
full per-room/control/reconnection matrix or a reporting/account-history/scrobbling
test. Overall `hardwareVerified: false` remains for that broader outstanding scope.
No further deployment, speaker controls, release/tag update or merge was performed.

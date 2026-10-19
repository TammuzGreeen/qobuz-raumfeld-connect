# Raumfeld Connect 1.0.0 — first stable release

Release date: 2026-10-19. Tag: `v1.0.0`, stable/latest GitHub release.
Promotes the accepted upstream-first implementation
`1917f07001545ba01bb655a496af29eafaa4f9ba`. Publication updates project version
metadata and documents acceptance; playback code, dependency pins, source authority,
player patches and the running deployment are unchanged. Beta/preview tags remain
immutable; no automatic merge to main.

## Accepted behavior and evidence scope

User-confirmed original-room audible playback and steady Spotify → Qobuz handoffs
with Spotify paused and actively playing, Qobuz → Spotify without first pausing
Qobuz, Next, pause/resume, paused/playing seek and app/speaker volume. Cross-room
playback and general all-six-room operation were confirmed. After correction
deployment the user confirmed requested automatic nonzero start after Spotify
without manual seeking and phone-visible room-name checks: everything currently
working. See POST_BETA_FIX_REVIEW.md and ALL_ROOMS_HANDOFF_REVIEW.md for evidence.

Supported release scope is the tested Android upstream-first/CD-quality/amd64
deployment with independent ungrouped logical Raumfeld rooms. Existing logical
stereo pairs are not split; multi-room groups are not supported. User general
all-room acceptance is not a separately captured exhaustive test matrix per room.

Remaining unverified/deferred: account-visible history, report acceptance and
Last.fm scrobbling; wider reconnect/renderer disappearance/endpoint-rebinding and
exhausted-queue scenarios. Gapless arming, arbitrary grouping, automatic Qobuz
reclamation, recovery retries and fallback volume setters remain disabled.
`hardwareVerified: false` remains for the broader acceptance checklist; stable
means the user-approved supported playback scope, not universal certification.

## Build and installation boundary

This release publishes GitHub source archives only. No container registry image or
private runtime/archive/log/credential asset is attached. In particular, do not
substitute the older `0.2.0` or registry `latest` image for this source release.

```sh
git clone --branch v1.0.0 --depth 1 https://github.com/TammuzGreeen/qobuz-raumfeld-connect.git
cd qobuz-raumfeld-connect
docker build --platform linux/amd64 \
  --build-arg PLAYBACK_PATH=upstream_first \
  --tag raumfeld-connect:1.0.0 .
```

The default launcher remains the historical guarded path; explicit build argument
above is required for the accepted upstream-first behavior. It does not change
the default Compose registry reference. Use the locally built image and preserve
the existing approved host-network, read-only-root/tmpfs, security and persistent
`/data` configuration, companion token and LAN_ADDRESS. Room config examples in
`examples/all-rooms-test.json` contain placeholders, not discoverable household IDs.
Runtime config requires supported logical room IDs/names, non-colliding discovery/
audio ports and quality 6. Preserve credentials and any refreshed token cache.
See qobuz/upstream_app.py and the private-installation boundaries in
ALL_ROOMS_HANDOFF_REVIEW.md; publication itself does not authorize deployment.

Rebuilding produces a new image artifact, not the exact already-tested artifact.
Only amd64 acceptance is recorded. Diagnostics remain opt-in via
`UPSTREAM_STATE_DIAGNOSTICS=1`; raw upstream logs may contain personal information.

## Tested implementation and rollback

Current accepted deployed image: `raumfeld-connect:upstream-first-beta-fixes-review`.
Imported Docker config digest:
`sha256:e998c73001369cfe57e658bf9980196b62c9b8dc4b591d8d4c88e55da29c0119`.
Installed player SHA-256:
`7b914d0e0c58eea39ab6d5195c9c6871eb59ec59093b1d4485104b24342a0910`.
Prior candidate checks: 200 Python, 117 serial Node, 34 packaged Node boundary tests,
six original upstream serialization tests and six-room offline startup/storage/
label smoke passed. Publication tests are recorded in the release checkpoint.

Private pre-update Compose/config/storage backups and the accepted image, all-room
beta image and older seeking-capable/original preview rollback images are retained.
To roll back a separately approved future deployment, stop the app and restore the
matching saved Compose/local image with pull_policy never, preserving credentials,
token cache and storage. No whole-volume restore, grouping mutation, transport
retry or forced Git operation is part of publication.

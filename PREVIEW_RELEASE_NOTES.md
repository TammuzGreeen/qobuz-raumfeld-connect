# Upstream-first working preview — seeking outstanding

Source checkpoint on `experiment/upstream-first-one-room`; not a merge to main
or complete hardware acceptance. Publication does not change the deployed image.

## Working and hardware-verified

Recorded user tests on the exact tested image confirm audible playback, Android
Next, Pause/resume, natural track advancement, app volume without jump-back and
physical volume knobs with Android feedback. Connection stayed steady as reported.
Scope: one ungrouped Raumfeld room, Android Connect, configured CD quality, amd64
Linux host networking. The accepted live Next trace used item → Play ordering.

## Known failure: seeking

Mid-track playback/scrubbing fails with renderer Seek error 710. Redundant Seek and
optimistic upstream success are recorded. No seeking correction is in this preview.

## Implemented but awaiting verification

Not yet separately verified on this image: reporting/account history/linked Last.fm
scrobbling (deferred), uninterrupted Spotify, Spotify → Qobuz and Qobuz → Spotify
handoffs, exhausted-queue restart and hardware renderer disappearance/rebinding.
Reverse-order Next has synthetic coverage only. Unverified does not mean absent.

## Intentionally unsupported

Grouped/multiroom and non-CD configuration, gapless arming, upstream UI room edits,
automatic source reclaim, topology repair/drop/regroup and fallback transport retries.
Spotify stays native on the speakers; no separate Spotify receiver is included.

## Tested source and image

- Playback source: `560ee29e19e242fb336281823aa2caa36d9bcafc`.
- Running image ID/config digest:
  `sha256:4baf7a4f6b3d480a00e8f8abe0f9711a8df3747cee2cbaa8368e0e01d528679f`.
- Local OCI index ID:
  `sha256:088c8afa865cf264b5b4be230aa8aaf070694ae8a77207d119c658fb67ae4c56`.
- Publication tag points to documentation/configuration checkpoint; it does not
  claim a new playback build or deployment. The running source files match `560ee29`.
- qobuz-proxy 1.7.2 at `91db990abeb486005a8315a6b1e722df776c4ba4` plus isolated
  hash-checked partial-state player backport; node-raumkernel 1.2.25 at
  `2c79c9a3d2c7dcd2d8bc586540d435808142c116`; Node 24.8.0, Python 3.13.7, uv 0.8.22.

Architecture: upstream Qobuz Connect discovery/session/WebSocket, command handler,
queue/player, reporting, audio proxy and DLNA; explicit room/source-safety wrappers.
Node supplies discovery/native-source evidence and on-demand renderer creation,
not a replacement Qobuz playback engine. No handshake lease/replay registry or
timed message-pairing admission. Source/session/topology conflict is terminal.

Validation before publication: 147 project Python tests, four standalone upstream
contribution tests, six original upstream serialization tests, 104 serial Node tests
and offline smoke. These are distinct from the user hardware evidence. Overall
`hardwareVerified: false` remains until broader acceptance is completed.

## Availability, setup and rollback

**Source-only prerelease: no downloadable preview container image, OCI asset or
published registry digest.** GitHub supplies source ZIP/tar archives automatically.
Older published release images are not this preview. The tested image and private
rollback/configuration snapshots are retained locally, never attached to GitHub.

Use [README.md](README.md), [CHECKPOINT_STATUS.md](CHECKPOINT_STATUS.md),
[PREVIEW_SETUP.md](PREVIEW_SETUP.md), [docker-compose.preview.yml](docker-compose.preview.yml)
and [preview-checkpoint.json](preview-checkpoint.json) at this tag for sanitized
setup, dependencies, effective settings and rollback. Preserve private `/data`,
credentials, corrected host LAN address and one-room/CD configuration. Do not run
a second instance or overwrite existing files with placeholders.

No workflow change, force-push, registry publication or automatic deployment.
Existing publication credentials lack `workflow` scope; no workflow edit is needed
for this manual source prerelease. Privacy review excludes account/runtime data,
tokens, household/device identifiers, raw logs and private backups.

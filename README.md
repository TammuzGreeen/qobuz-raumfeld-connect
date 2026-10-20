# Raumfeld Connect 1.0

> **This branch is an isolated DLNA experiment based on stable `v1.0.0`, not a
> replacement stable release.** See [DLNA renderer setup, architecture, limitations
> and hardware test plan](experiments/dlna/README.md). It hosts one real UPnP
> MediaRenderer per configured logical room (stereo pairs remain together).
> No hardware playback acceptance or deployment is claimed; main, stable tags,
> the running Qobuz installation and the separate Cast experiment are preserved.

**First stable release: [v1.0.0](https://github.com/TammuzGreeen/qobuz-raumfeld-connect/releases/tag/v1.0.0).**
Qobuz Connect for independent ungrouped Raumfeld rooms, with native Spotify source
protection. User reports all six rooms working, with source handoffs, playback
controls, seeking and volume tested. Post-Spotify mid-track starts and the original
room's logical name are now user-confirmed working on deployed `1917f07`.

See [1.0 release scope, setup and limitations](STABLE_RELEASE.md). This is a
source-only release of the accepted upstream-first implementation plus version/
documentation updates; it does not replace the running image or publish a registry
image. Build with `PLAYBACK_PATH=upstream_first` explicitly. Beta/preview tags and
private rollback images remain unchanged. Grouped playback and scrobbling acceptance
are not claimed; broader reconnection/rebinding and detailed per-room regression
coverage remain outstanding.

## Historical preview (not current beta status)

This branch contains a **working hardware-tested preview with seeking outstanding**,
not a fully accepted release. The running deployment is unchanged by publication.

**Later development:** [seeking investigation/candidate](SEEK_REVIEW.md) is separate
from the published preview. Candidate `361e46e` is now separately approved/deployed;
paused-scrub silence and explicit resume from the selected point now pass the user
hardware test, with steady connection. Previous
`d58a5cd` passed playing scrubbing but auto-resumed during paused scrubbing.
Spotify → Qobuz handoff failed before renderer/audio start; Spotify kept playing.
[Boundary trace and approved diagnostic deployment](RENDERER_BOUNDARY_REVIEW.md)
distinguish background maintenance from explicit takeover without claiming its
discarded response is recoverable.
The call-boundary defect is now identified: our companion used the nonexistent
`kernel.getManager()` API. Corrected `d3c3a78` is now approved/deployed, awaiting
user hardware handoff acceptance. Renderer creation now succeeds, but authority
is revoked before Qobuz transport start and handoff still fails. Manual Spotify
pause is an acceptable test policy, not a proven requirement or successful handoff.
Broader acceptance remains incomplete. Image/acceptance details
below identify the preserved working preview, not this newer experimental deployment.
Build the tagged/pinned sources below to preserve the tested preview.

## Tested checkpoint

- Playback implementation: [`560ee29`](https://github.com/TammuzGreeen/qobuz-raumfeld-connect/commit/560ee29e19e242fb336281823aa2caa36d9bcafc).
- Running amd64 image ID (Docker config digest):
  `sha256:4baf7a4f6b3d480a00e8f8abe0f9711a8df3747cee2cbaa8368e0e01d528679f`.
- Publication tag: [`upstream-first-preview-2026-10-12`](https://github.com/TammuzGreeen/qobuz-raumfeld-connect/releases/tag/upstream-first-preview-2026-10-12).
  Its checkpoint adds documentation/configuration; it is not a newly deployed image.
- Scope: Android → actual upstream Qobuz Connect → one ungrouped Raumfeld virtual
  renderer, CD-quality configuration, Linux host networking. User-observed tests
  apply to this exact image, not every device, firmware, platform or ordering.

### Working and hardware-verified

Based on recorded user tests on that image:

- Audible playback.
- Android Next, with audible next track and steady connection.
- Pause/resume.
- Natural queued-track advancement.
- App volume without jump-back.
- Physical volume knobs with feedback to Android.

### Known failure

**Seeking / mid-track playback does not work.** The recorded scrub receives
renderer error 710; a duplicate Seek and optimistic success reporting are also
recorded. No seeking correction is included in this checkpoint.

### Implemented but awaiting verification

These are **not yet separately verified** on the tested preview: real Qobuz
playback reporting/account history and linked Last.fm scrobbling (deferred), idle
Spotify continuity, Spotify → Qobuz and Qobuz → Spotify handoffs, exhausted-queue
restart, and renderer disappearance/endpoint rebinding. Reverse-order Android Next
is covered synthetically, but the accepted live attempt used item → Play ordering.
Unverified does not mean unimplemented. Overall `hardwareVerified` remains false
until the broader acceptance plan passes.

### Intentionally unsupported in this preview

Multiroom/grouped operation, qualities other than the configured CD setting,
gapless arming, upstream UI room add/edit/remove, automatic source reclamation,
room drop/regroup and fallback/recovery transport retries are disabled or rejected.
There is no separate Spotify receiver; Spotify runs natively on the speakers.

## Architecture

```text
Android → upstream discovery/session/WebSocket/command handler
        → upstream queue/player/reporting/audio proxy
        → upstream DLNA + explicit room/source-safety compatibility
        → Raumfeld virtual renderer
Node    → discovery, endpoint/native-source evidence, explicit on-demand zone creation
```

`qobuz-proxy` is pinned to `91db990abeb486005a8315a6b1e722df776c4ba4`
(package version 1.7.2), with one hash-checked, isolated player backport for partial
playback intent. It is **not wholly unmodified upstream**. Node's pinned
`node-raumkernel` handles discovery and on-demand renderer creation, not the
Qobuz playback engine. No custom receiver handshake admission, ownership lease,
session replay registry or timed message-pairing window is used in this path.
Source/topology/session retirement is terminal; fresh explicit Qobuz selection
plus Play is required to replace another source. Passive discovery never creates
a zone. See [current status](CHECKPOINT_STATUS.md),
[backport rationale](upstream-patches/README.md) and
[chronological evidence](UPSTREAM_FIRST_EXPERIMENT.md).

## Install, preserve state and roll back

**No preview container image is published to a registry or attached for download.**
The prerelease provides source archives only. The older `v0.2.0` image is a different
guarded path; it is not this hardware-tested preview. Do not pull `latest` as a
substitute for the tested image.

Use [PREVIEW_SETUP.md](PREVIEW_SETUP.md) for pinned source-build/load, private state
preparation, loopback-only UI access and rollback instructions. The standalone
[docker-compose.preview.yml](docker-compose.preview.yml) reproduces the tested
deployment shape with documented placeholders. It requires an existing private
data volume, host LAN address and API token; neither personal room/account files
nor runtime data are supplied by the repository. There is no automatic deployment.

Host networking is required on a trusted Linux speaker LAN. The tested discovery
and audio-proxy ports are 8790 and 8890; companion/status UI bind to loopback on
8787/8788. Do not expose the interfaces publicly. Preserve `/data`, original
credentials, selected one-room/CD configuration and your corrected `LAN_ADDRESS`.
Leave `RAUMFELD_HOST` empty for discovery. Runtime data and backups are secrets.

## Development and limitations

Runtime dependencies and verified effective settings are recorded in
[preview-checkpoint.json](preview-checkpoint.json); lockfiles and Docker bases
remain pinned. Byte-for-byte rebuild identity is not promised: build-backend
dependencies and image-export metadata can differ. Preserve the tested image for
an exact rollback rather than relying on a rebuild.

The candidate passed 147 project Python tests, four standalone upstream-contribution
tests, six original upstream serialization tests, 104 serial Node tests and offline
supervisor/UI/health/storage/shutdown smoke. Those are synthetic/container results,
not reporting acknowledgements, audible hardware tests or Spotify continuity proof.
Tests requiring the player backport should run in the built image; plain `uv sync`
installs the pinned original player without that build-time backport.

The default `guarded` launcher and historical documents remain for comparison and
rollback. They do not describe the current upstream-first preview. Existing CI and
the older registry-release workflow have not been changed for this publication;
this prerelease does not imply CI validation of the experiment or a merge to main.
Legacy upstream Node advisories remain relevant to trusted-LAN-only deployment.
See [changelog](CHANGELOG.md). MIT for this repository; upstream attribution and
dependency licenses are retained.

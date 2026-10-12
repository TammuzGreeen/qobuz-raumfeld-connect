# Working preview checkpoint — 2026-10-12

**Working preview; seeking outstanding; not complete hardware acceptance.**

Later seeking candidate work is described in [SEEK_REVIEW.md](SEEK_REVIEW.md).
It does not change this published checkpoint's image or hardware evidence.

Publication: `upstream-first-preview-2026-10-12` on
`experiment/upstream-first-one-room`, not main. The tag resolves to the publication
checkpoint; the deployed playback implementation is an earlier, exact revision:
`560ee29e19e242fb336281823aa2caa36d9bcafc`. Subsequent commits record user tests
and add publication documents/configuration, without changing playback behavior.

Running amd64 image ID/config digest:
`sha256:4baf7a4f6b3d480a00e8f8abe0f9711a8df3747cee2cbaa8368e0e01d528679f`.
Local OCI index ID:
`sha256:088c8afa865cf264b5b4be230aa8aaf070694ae8a77207d119c658fb67ae4c56`.
These identify local/imported artifacts; neither is a published registry reference.
The running image's 32 checked application/backport source files match `560ee29`.
Dependencies/effective settings: [preview-checkpoint.json](preview-checkpoint.json).

## Working and hardware-verified

Recorded user tests on this image, one ungrouped Raumfeld room, Android Connect,
configured CD quality, host networking:

| Control | Evidence |
| --- | --- |
| Audible playback | User heard audio; connection steady |
| Android Next | User heard new track; connection steady; item → Play trace completed Stop → URI → Play |
| Pause/resume | User explicitly confirmed pressing Pause and Play works |
| Natural advancement | User let queued track advance; following track audible; Android showed Pause |
| App volume without jump-back | User explicitly confirmed stable changes |
| Physical knobs / Android feedback | User answered yes to indicator following knob; stable volume |

The user's assessment that everything except seeking works is consistent with
these checked controls. It does not supply separate evidence for the features below.

## Known failure: seeking

Mid-track scrub is rejected with definite renderer 710. Duplicate Seek transmission
and optimistic upstream success are recorded. Fix is not included. No further
seeking investigation or playback edits are part of this publication checkpoint.

## Implemented but awaiting verification

- Qobuz playback reporting/history and server-side linked Last.fm scrobbling:
  **not yet separately verified**, explicitly deferred by user.
- Idle/uninterrupted native Spotify: **not yet separately verified**.
- Both source handoffs, Spotify → Qobuz and Qobuz → Spotify:
  **not yet separately verified**, planned after seeking correction.
- Exhausted-queue restart: **not yet separately verified** (upstream queue/player).
- Hardware renderer disappearance/rebinding: **not yet separately verified**;
  compatibility behavior has synthetic coverage.
- Reverse Play → item Next ordering: synthetic pass, no same-image live confirmation.

An outgoing state-send completion is not a playback-report HTTP acknowledgement.
SOAP success, renderer PLAYING, delivered bytes, audible playback and account-visible
scrobbles are distinct evidence. Previous-image/hybrid results do not establish
acceptance of this image. Overall `hardwareVerified: false` remains appropriate.

## Not implemented or intentionally unsupported in this preview

- Grouped/multiroom operation and non-CD runtime configuration are rejected.
- Gapless arming and upstream UI room mutations are disabled.
- Automatic source reclamation, topology repair/drop/regroup and fallback transport
  retries are intentionally disabled. No separate Spotify receiver is implemented
  or required for native speaker Spotify.

## Preservation and publication

The current image is retained privately both locally and on the deployment host as
a rollback. A fresh private checkpoint captures effective Compose, storage hashes
and runtime state. Deployment, container identity, configuration and data stayed
unchanged; no speaker controls were sent during checkpointing. Private state,
account files, raw logs/captures and image backups are excluded from Git/GitHub.

Source-only GitHub prerelease; **no downloadable preview container image or registry
publication**. Older registry images are not this checkpoint. No merge to main,
force-push, workflow edit or automatic deployment. See
[setup/rollback](PREVIEW_SETUP.md), [sanitized Compose](docker-compose.preview.yml),
[changelog](CHANGELOG.md) and [historical evidence](UPSTREAM_FIRST_EXPERIMENT.md).

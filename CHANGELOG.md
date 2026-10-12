# Changelog

## upstream-first-preview-2026-10-12 — working preview, seeking outstanding

- Preserve the tested playback source `560ee29` and current private rollback image;
  publication adds sanitized configuration/status/setup documentation only.
- Hardware-verified by recorded user tests on that amd64 image: audible playback,
  Android Next, Pause/resume, natural advancement, app volume without jump-back,
  physical knobs with Android feedback and steady connection as reported.
- Known failure: mid-track seeking (renderer 710, duplicate Seek/optimistic success);
  no seeking correction included.
- Implemented but not yet separately verified: reporting/history/Last.fm scrobbling,
  Spotify continuity, both source handoffs, exhausted-queue restart and hardware
  endpoint disappearance/rebinding. Reverse-order Next remains synthetic-only.
- Upstream-first sessions, queue, player, reports, audio proxy and DLNA with the
  isolated partial-state player backport; Node discovery/on-demand zone creation.
- One ungrouped room/CD only; grouped playback, gapless arming, UI room mutation,
  automatic source reclaim and recovery/regroup are intentionally unsupported.
- Source-only prerelease on the experiment branch; no merge to main, registry
  publication or downloadable preview container image. Overall hardware acceptance
  remains incomplete. See [checkpoint status](CHECKPOINT_STATUS.md).

## 0.2.0 — historical guarded-path Qobuz Connect integration

- Added Qobuz OAuth login and room/quality setup page on port 8788.
- Added per-room Connect advertisement, upstream session/player/queue wiring,
  guarded play/pause/resume/stop/seek/volume and HTTP audio relay with Range support.
- Added expiring explicit-selection grants, ownership revocation, replay protection,
  source checks and rejection of grouped-room mutations.
- Added persistent settings/tokens, digest-pinned images, amd64/arm64 publishing,
  provenance/SBOM and versioned GitHub releases.
- Added tests for source races, expired/replayed grants, OAuth callbacks, setup
  persistence, upstream interface integration and container lifecycle.

Hardware playback and real-account authentication are unverified. Grouped and
gapless playback are not enabled. Legacy upstream Node dependency advisories
remain documented in the README.

## Upgrade from 0.1.0

Set `LAN_ADDRESS` in `.env`, preserve your `API_TOKEN`, pull/recreate the container,
then open `http://<host-IP>:8788`. A named data volume now stores configuration and
Qobuz tokens. Sign in and select rooms; choose one in the Qobuz app to play.

# 0.2.0 — Qobuz Connect integration

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

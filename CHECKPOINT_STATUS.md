# Source checkpoint status

## Before the observation-order fix

- Hardware has previously confirmed audible Qobuz playback, retained connection,
  pause/resume, discovery and authentication. This does not guarantee every handoff.
- The room-specific volume candidate still produced a renderer-side connection
  reset on hardware. Readback stayed unchanged; fresh source reconciliation retained
  the session. A later Stop reset released it. That candidate was rolled back.
- The previous playback-tested image is currently deployed and healthy. Its next
  handoff released ownership on a physical unexpected-source observation during
  loading. Playback on the phone continued; audible speaker playback was not confirmed.
- Two characterization tests reproduce the ordering defect, including against
  the unchanged earlier source checkpoint: physical-first observations and a
  notification during loading revoke before Play; virtual-first succeeds.
  The 70 passing Node tests at this checkpoint include these bug characterizations,
  not a fix. Previous volume-candidate checks passed 36 Python tests, Docker build,
  smoke and simulated integration tests; they are not hardware acceptance.
- Silence remains independently unexplained. SOAP acknowledgement, reported
  PLAYING, delivery of audio bytes and audible playback are different boundaries.
- Manual next, natural completion, volume setter success and real Spotify takeover
  remain unverified hardware behavior.

This is a source-only checkpoint. Runtime configuration, credentials, `/data`, raw
captures and private audit/rollback material are intentionally excluded. No image
publication, deployment or release is authorized by this source checkpoint.

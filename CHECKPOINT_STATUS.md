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

## Subsequent local fix

The characterization tests have been converted to intended-result regressions.
Exact forwarding can be incomplete during the bounded explicit initial handoff;
Play requires fresh complete matching virtual and physical evidence. New tests
cover deadline, stale/event-only evidence, contradictory sources, revocation and
previous-selection callbacks. Bounded byte/SOAP/lookup diagnostics are prepared to
identify a silence boundary on the next approved hardware test. See
`HANDOFF_REVIEW.md`; source fixes are not hardware or audible acceptance.

Validation: 79 Node and 44 Python tests; correlated simulated metadata/Node/SOAP/
relay byte integration; Docker build, process smoke, Python compilation, Compose
validation, isolated supervisor failure, imports, real discovery initialization
and graceful shutdown. No candidate deployment or real-speaker mutation was run
for this fix. Audible playback, repeated hardware handoffs, volume, manual next,
natural completion and Spotify takeover still require the ordered hardware review.

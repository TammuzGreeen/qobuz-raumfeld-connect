# Initial offline checkpoint — 2026-10-20

Baseline: stable `v1.0.0` / `c5b87d52937fbe62d983da5a4863ff2c8a1e43ba`.
Development branch only. No release/latest tag moves, deployment or speaker mutations.

## Passed offline

- Linux amd64 runtime image and separate disposable test image built successfully.
- **204 Python tests**: stable regressions plus **16 new DLNA tests** using two
  independent synthetic backing renderers and actual bridge HTTP/SOAP/GENA handlers.
- **118 serial Node tests**, including bridge-UDN exclusion from observation.
- Existing Python → Node → synthetic renderer/audio integration.
- Real UDP M-SEARCH received exactly one response per logical target; alive and
  byebye packets verified for both devices on a private Docker network. This is
  network-packet evidence, not Symfonium/BubbleUPnP visibility on the speaker LAN.
- Non-root packaged process-pair startup on a read-only container, separate
  disposable config, discovery disabled, two persistent targets without backing
  endpoints, healthcheck, safe status, unchanged config, categorical/redacted
  milestones and graceful shutdown. No speaker control was possible in that test.
- Original source/ownership regressions retained: explicit single renderer demand,
  stale generation rejection, no uncertain mutation retry, no shutdown Stop,
  Spotify source safety, deferred paused seek and independent room state.

Wire tests cover description/SCPD consistency, stable identity/stereo membership,
metadata preservation, direct media/range fetch by a simulated renderer, prepare
without playback, nonzero starts/seek acknowledgements, silent deferred paused
seeking, play/pause/stop/resume, actual volume/mute readback, physical-volume events,
subscription lifecycle, missing/rebound endpoints, grouped-room limitations,
capability filtering, invalid SOAP/DIDL/media and source changes during a load.

## Not hardware-verified

- Symfonium/BubbleUPnP discovery and control compatibility.
- Audible output, nonzero start timing, paused-seek silence on real speakers,
  duration/position accuracy, media/server codec and range compatibility.
- Physical-knob feedback latency, mute support, screen-off current-track delivery
  and controller-supplied subsequent tracks.
- Real multiroom isolation, Spotify handoffs, renderer disappearance/rediscovery
  without duplicate targets, or Qobuz regression after controlled rollback.
- ARM64 build/runtime, grouping/Whole Home, gapless, relay/transcoding, independent
  bridge queue, account history/scrobbling and Audiobookshelf progress integration.

Known limitations and all eight pending hardware steps: [README](README.md).
Legacy Node dependencies report 3 moderate / 3 high npm advisories; no unrelated
SDK upgrade is mixed into this experiment. Trusted-LAN-only deployment remains
required. Upstream stack emits two ElementTree deprecation warnings in tests; they
do not constitute hardware or interoperability acceptance.

**Deployment approval is still required.** Stable and experimental control engines
are not approved for concurrent operation on the same rooms; follow the documented
stop-stable → test → stop-experiment → restore-stable sequence. Cast stays untouched.

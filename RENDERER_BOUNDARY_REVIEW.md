# Explicit renderer-request boundary review

**Current diagnosis:** the companion used an API absent from the pinned library
(`kernel.getManager()`), so explicit renderer demand failed locally before any
Raumfeld creation request. The correction below is prepared, not deployed.

## Recorded hardware outcome — 2026-10-14

On deployed `361e46e`, user let Spotify play, selected the same room in Qobuz and
pressed Play. Qobuz was not audible and lost its app connection afterward.
**Spotify continued playing.** That confirms this attempt did not interrupt Spotify;
it is a failed handoff, not successful source replacement or complete source-safety
acceptance. Prior audible Qobuz and silent paused-scrub/resume passes remain recorded
for their attempts. No agent transport/provisioning commands or deployment changes
were made during diagnosis.

The different-track paused update loaded (local event 1183); explicit PLAYING
(event 1184) reached `DeferredRoomBackend.play` and its one on-demand
`POST /v1/zone-for-play`. The companion returned non-200 and Python raised
`companion_unavailable` before Qobuz URI/Play transmission. Current read-only
catalog showed the configured single room unassigned, with one physical renderer,
no virtual-renderer endpoint and no catalog-unavailable marker. Healthy/authenticated,
zero restarts does not contradict user-observed Android disconnection.

### Background maintenance is not this takeover

The two earlier dynamic speaker-service `ECONNREFUSED` entries are **subscription
maintenance** failures in the recorded lines, approximately 33 seconds before the
explicit Play. They are not `connectRoomToZone` request errors. No retained evidence
establishes them as the cause of this takeover failure. Node discovery/observations/
Spotify evidence do not invoke the companion's renderer creation method; only the
explicit Play path does. Background read/subscription failure must not create a
zone or authorize source replacement.

### Exact old error cannot be recovered from retained evidence

The deployed error boundary loses information at three points:

1. Python discards the non-200 companion status/body.
2. Companion creation catch collapses library, timeout and post-check failures
   into `creation_pending_or_uncertain`. Preflight guards also respond non-200.
3. Pinned node-raumkernel's `connectRoomToZone` rejects non-200 HTTP using only
   `Error connectRoomToZone: null`, discarding actual status. Network codes are
   similarly buried in a string.

No response body/status was retained for the old request, and no matching library
creation-error log proves a Raumfeld response. Therefore **our preflight/uncertainty
guard versus an actual Raumfeld rejection remains undetermined for that attempt**.
Current successful catalog reads do not prove what the earlier POST checked.
Do not repeat a mutating request merely to reconstruct a missing response, or
clear uncertain creation state as a recovery tactic.

## Prepared correction — error fidelity, not an unproven takeover fix

The new boundary preserves error origin and phase, companion HTTP status,
renderer HTTP status when actually observed, allowlisted network code and whether
the renderer request was attempted (or unknown). It never retains response bodies,
URLs, identifiers, metadata or arbitrary exception messages in diagnostics.

- Local guards: `local_check`, `preflight`/`uncertainty_block`, request not attempted.
- Actual Raumfeld non-200: `raumfeld_response`, `response`, actual status retained.
- Network failure: `network`, code retained, no invented HTTP response.
- Deadline/uninstrumented SDK failure: explicit unknown outcome; no inferred
  rejection, acknowledgement or retry permission.
- Topology change after acknowledgement: local `postflight`, request already
  attempted; uncertain state stays terminal.

`docker/apply-raumkernel-creation-errors.py` hash-checks the exact pinned
node-raumkernel zone-manager source before an isolated `connectRoomToZone` error
adaptation. It preserves successful requests/confirmation logic, stops the
missing-host branch before malformed transmission and prevents a second error
fall-through. No dependency upgrade or discovery/subscription/transport change.
Original node-raumkernel attribution/license remains intact.

Companion emits categorical diagnostics only for explicit renderer demand, not
background catalog/observation maintenance. Python reads a bounded error response
once, preserves allowlisted fields and never retries. The existing assigned-room
path, single ungrouped-room checks, uncertain-creation block, host/membership
post-checks and upstream source/generation authority remain intact. No source
protection relaxation, fallback creation, automatic reclamation, regroup, leases,
timed pairing, alternate volume setting or changes to the player/seek patches.

Synthetic tests distinguish local topology denial, real SDK HTTP rejection,
network refusal, missing host, unknown library failure, timeout and postflight
topology change, plus second-demand uncertainty blocking. Real pinned SDK code is
exercised with only its request boundary replaced. Background reads/Spotify evidence
never invoke creation. Python tests cover bounded, malformed and hostile response
fields without private-message leakage or additional requests.

This preparation does not establish the original hardware error or solve its
underlying cause. Current `361e46e` deployment remains unchanged. Separate approval
is required for deployment and a new controlled user-requested takeover; first
inspect local-check versus actual Raumfeld response evidence before choosing a
behavioral correction. Preserve working Qobuz playback and all rollback images.
Overall `hardwareVerified: false`; scrobbling deferred; immutable preview untouched.

## Prepared image and regression results — not deployed

- Implementation: `29c020decab022d9526a25b699be1831bfc26807`.
- Local amd64 image: `raumfeld-connect:upstream-first-renderer-boundary-review`.
- Local OCI index:
  `sha256:04180facc96bd77a5b875833173d036f0cd2cf5619e58aedc9e6076165919a90`.
- Private exported archive SHA-256:
  `e300a2d5cb324a02d9a4d51545acb4bb21921f42b23ad4da51a43260106145ce`.
- Built candidate passed 182 Python tests (172 project + ten standalone), offline
  supervisor/UI/health/storage/shutdown smoke, six original upstream serialization
  tests and 14 packaged Node boundary tests. Full serial development Node suite
  passed 110 tests, including the additional timeout/postflight-phase regression.
- Exact SDK adaptation output hash verified; installed upstream player hash equals
  deployed `361e46e`, preserving all three player patches unchanged.

No deployment, registry publication, agent speaker control or uncertain-request
retry. A new approved diagnostic deployment/test is required to capture the exact
origin/status of a fresh explicitly requested takeover; the old response cannot
be reconstructed. The failed handoff remains failed, Spotify continuation is only
non-interruption evidence, and known working Qobuz playback/rollback are preserved.

## Approved diagnostic deployment — new takeover capture pending

User separately approved diagnostic deployment and a fresh explicit takeover test.
`29c020decab022d9526a25b699be1831bfc26807` now runs as
`raumfeld-connect:upstream-first-renderer-boundary-review`; imported image ID/config
digest is `sha256:72a17422fa93f74943c397d6ac2138b66d3348c40415cb9995fb75a12f831625`.
Archive checksum matched. Fresh private before-state and current `361e46e` rollback
archive retained, alongside original working-preview rollback. One image/pull-policy-
only app update, no retry. Healthy/authenticated, zero restarts, unchanged config/
credentials/optional cache state, environment/mounts/host networking, room identity/
membership and unrelated containers. Installed player unchanged and SDK adaptation
hash verified. Zero startup transport/provisioning transmissions; no agent speaker
controls. New user-requested takeover evidence pending; no handoff success or
underlying cause is claimed. Published preview/tag untouched, `hardwareVerified: false`.

## Fresh takeover capture and exact call-boundary defect

On diagnostic `29c020d`, user tried Qobuz while Spotify played. Qobuz failed and
Spotify continued. Companion trace reached `library_call`, then immediately failed
as `library_unknown` with companion 409 and no renderer HTTP status/network code.
Subsequent demands were local `uncertainty_block`, requestAttempted false. These
are not successful handoffs or proven Spotify/firmware rejection.

An isolated inspection of the **actual pinned runtime library** confirms
`typeof kernel.getManager === 'undefined'`. Its init constructs
`kernel.managerDisposer`, whose `zoneManager.connectRoomToZone` is the real method.
Our companion called `kernel.getManager().zoneManager.connectRoomToZone(...)`, so
it raised locally before invoking the SDK's request method. The diagnostic SDK
error adapter was never reached. This identifies the exact integration defect;
there was no Raumfeld response to this call to attribute the failure to. The
subsequent uncertainty latch was conservatively set by our pre-call code and catch.

Earlier mocks invented `getManager` and hid the API mismatch. The new regression
constructs the real pinned Raumkernel/ManagerDisposer/zone manager without starting
network/discovery and replaces only the creation operation. It verifies the real
API shape rather than perpetuating the mock-only method.

Correction uses `kernel.managerDisposer.zoneManager`, validating the method before
setting pending/uncertain state. Missing manager now reports local preflight
`renderer_manager_unavailable` without transmission or uncertainty latch. Actual
attempts retain existing one-call behavior, uncertainty blocking, host/membership
checks and explicit-selection authority; there is no fallback method or retry.
Assigned-room playback and installed player/seek patches remain unchanged.

User said manually pausing Spotify before explicit Qobuz selection + Play is an
acceptable policy. That does not establish that firmware requires it. On current
`29c020d`, pausing Spotify cannot cure this API defect or the existing uncertainty
block, so user was asked not to retry. No uncertainty state was cleared on the
live process and no speaker controls or new deployment were performed. A separately
approved corrected deployment and fresh user test remain necessary. Spotify
continuation confirms this failed attempt did not interrupt it—nothing more.

Prepared corrected implementation `d3c3a78870bce634e9224a428b7d5e177da2a83e` is
locally archived as `raumfeld-connect:upstream-first-renderer-manager-review`
(amd64). OCI index:
`sha256:1a208bd614dfed588eb061428a02ff0a341b2a2cde1a2f5bab3864b3f810f7fe`;
private export SHA-256:
`1a12177af88bd72d31c69859562204207b831d606853e9fbf970230a72c90488`.
Passed 182 Python tests (172 project + ten standalone), 112 serial Node tests,
17 packaged Node boundary tests and offline supervisor/UI/health/storage/shutdown
smoke. Both direct Node fixtures and cross-process Python/Node fixture now use the
real SDK manager shape. Installed player byte hash is unchanged. Not deployed,
registry-published or hardware-verified; current diagnostic image remains `29c020d`.

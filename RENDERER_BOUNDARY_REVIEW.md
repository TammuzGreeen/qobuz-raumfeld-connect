# Explicit renderer-request boundary review

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

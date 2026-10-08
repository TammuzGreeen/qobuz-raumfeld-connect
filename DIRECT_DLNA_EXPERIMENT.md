# One-room direct-DLNA migration experiment

This is an isolated opt-in implementation, not a deployed alternative. No live
account calls, speaker commands, zone repairs or deployment updates were performed
for its tests. The current runtime configuration/image were privately backed up
for rollback, without changing the app or `/data`.

## Implemented candidate

`qobuz/dlna_backend.py` subclasses the pinned upstream DLNABackend/DLNAClient:
upstream DIDL, state/position polling and ordinary player/reporting behavior remain.
Node direct mode exposes binding/admission only and disables `/v1/control`.
The default `guarded` path remains available and was not deleted.

- `PLAYBACK_PATH=direct_dlna` is opt-in and requires one configured room at CD
  quality. The candidate image embeds that flag using a Docker build argument,
  allowing an image-only deployment without changing existing runtime settings.
- Advertisement no longer depends on renderer presence in direct mode. Actual
  playback lazily resolves its discovered description and validates exact UDN,
  standalone membership and service origins. No description fallback/redirects.
- Only actual admitted Play may request one unassigned-room zone creation. An
  assigned but missing renderer is awaited boundedly, never dropped/recreated.
- Same-identity rebinding retires the old client before read-only connection; it
  preserves the receiver/player/cloud session. Old read responses cannot revoke
  the current binding or reinstall retired HTTP sessions.
- Every mutation checks the captured selection/client generation, current Node
  authority/binding and exact virtual source at send time. Physical evidence is
  structurally checked independently of virtual-observation order. Missing/empty
  observations cannot confirm ownership; loading reads wait boundedly.
- Initial selection permits replacement of its observed baseline only within a
  10-second window. New native Spotify notifications and other unexpected physical
  sources revoke immediately. A loaded track cannot tolerate its original Spotify
  baseline as ownership or automatically reclaim a released session.
- URI/Play/volume mutations are single shot. No recovery Stop, SOAP retry, delayed
  volume setter, gapless arming, background repair or setter fallback.
- Volume uses upstream virtual-renderer `SetVolume` only for this ungrouped room,
  after read-only SCPD verification of its exact input declaration. This route is
  a comparison variable, not a hardware-accepted substitute for SetRoomVolume.
  Getter success is not setter proof. Uncertain writes require new complete physical
  reads and new virtual proof to retain the session; further volume writes block.
- Active knob readback is currently a modest two-second DLNA fallback; only changed
  values are reported. RenderingControl-event-triggered readback is deferred, not
  falsely claimed implemented. Cloud volume-change echoes never become setters.
- Start/end reporting now has sanitized per-room attempt/result diagnostics and
  API HTTP acknowledgement/rejection codes. No account/track/blob/context values
  or API response bodies are retained. Server-side history/Last.fm is still unverified.

Tests include real local SOAP/description/SCPD HTTP, a two-process Python→Node
binding→direct SOAP→actual relay-byte flow, upstream player natural repeat/manual
next/reporting, takeover, missing renderer, rebinding, stale callbacks, unknown
evidence, declarations, volume uncertainty and no setter echoes.
Latest source suite: **94 Node tests and 73 Python tests passed**. These are
simulation results, not audible playback, real natural completion or scrobbling.
The inherited checkpoint's expiry test passed in these runs; its earlier failing
run is retained below as historical evidence, not rewritten as acceptance.

The sections below record the source review and original design. Where an item
was intentionally deferred or narrowed, the implemented status above takes precedence.

## Baselines and source revisions

The user's observations are the baseline, not inferred root causes: original
direct-DLNA Qobuz playback/pause/knobs worked nearly flawlessly, while its reconciler
made Spotify unusable after approximately 30 seconds. The newer service has had
audible playback/pause and several availability, handoff, volume/session failures.
The user reports that scrobbling has never worked in the newer service.

Source checkpoint before this review:
`c51ef8ca33ffa38a5c62ce67cb8fac4f1564d9dc` on `fix/speaker-availability`.
It preserves unverified next-track work with a truthful 82/83 Node-test result;
the failing expectation is an expiry error-code race, not evidence of acceptance.
The deployed image remains the approved earlier handoff-order candidate. Initial
audible playback was user-confirmed; natural advancement then released ownership.

Inspected references (upstream source stays outside this repository):

- [Reconciler main `ad4e03b`](https://github.com/TammuzGreeen/raumfeld_qobuzproxy_reconciler/tree/ad4e03bada510fc3916ddc656dfb328e83557184)
  and earlier versions `9c093e9`, `a16626d`, `e43e8ac`, `70ac535`.
- [Pinned qobuz-proxy `91db990`](https://github.com/leolobato/qobuz-proxy/tree/91db990abeb486005a8315a6b1e722df776c4ba4),
  the actual dependency in `qobuz/uv.lock` and the deployed image.
- [qobuz-proxy main `10dedc9`](https://github.com/leolobato/qobuz-proxy/tree/10dedc9d2ac0ff2edcad729e22876d2c7f158c8e).
  DLNA backend/client/proxy/player/Speaker differ from the pin; auth/reporting
  implementations inspected here are identical. Do not silently upgrade during A/B.
- [node-raumkernel main `2c79c9a`](https://github.com/ChriD/node-raumkernel/tree/2c79c9a3d2c7dcd2d8bc586540d435808142c116)
  and the locally installed dependency implementation. No reference tree contained
  an AGENTS.md. No upstream source was copied into the experiment; keep existing
  licenses/attribution when implementing adaptations.

## What the reconciler actually does

`reconciler.py:reconcile_once` GETs proxy speaker configuration and GETs the stored
device-description URL. A healthy URL short-circuits repair. A dead/missing URL or
missing running receiver enters `repair_room`; it does not first try a newly
discovered identity-verified binding as the entire repair.

| Operation | Effect |
|---|---|
| GET `getZones`, GET device XML, proxy SSDP discovery | Read-only observations; not evidence they stop Spotify. |
| `dropRoomJob(roomUDN)` | Unassigns the room; can destroy an active zone/source. |
| `connectRoomToZone(roomUDN, zoneUDN="")` | Creates/moves the room into a standalone zone; can displace native playback. |
| PUT proxy `/api/speakers/{id}` | Upstream `_on_edit_speaker` persists config, awaits old `Speaker.stop()`, constructs and starts a new Speaker. Not an in-place endpoint rebind. |

Historical `9c093e9` drops an assigned stale zone and creates a standalone zone
without the current Spotify guards. Earlier inspected versions create standalone
zones without those guards and without `dropRoomJob`. Current `ad4e03b` adds fresh
`getZones` checks before repair/drop/create and a Spotify cooldown, but detects
Spotify only from the topology renderer's `spotifyConnect="active"` attribute.
That is not authoritative source/intent proof when the attribute is absent or
transitional. The file's safety comments do not prove safety on the user's device.

The deployed reconciler revision/configuration at the time of the reported failures
is not established. The concrete operations above can interrupt playback, but no
capture ties one to the reported 30-second cutoff. Changing the polling interval
does not remove these mutations or make missing renderers authorization to repair.

## Current versus proposed path

Current:

```
stable Receiver/LanDiscovery → upstream protocol handlers/QobuzPlayer/PlayReporter
  → RaumfeldBackend → authenticated Node /v1/control → custom Controller
  → node-raumkernel SOAP → virtual renderer
audio: metadata URL → existing AudioRelay → renderer HTTP request
```

Node owns transport mutations, leases, source/forwarding guards, handoff confirmation
and zone creation. Python separately monitors/releases the cloud session. The
reproduced callback-order rejection and later next-track source-confirmation failure
occur in that custom confirmation path. This does not prove all resets/silence share
that cause.

Proposed first experiment:

```
same stable Receiver/admission and upstream player/reporting
  → small RaumfeldDLNABackend adaptation → upstream DLNAClient → virtual renderer
binding: node-raumkernel topology/discovery → identity-verified description URL
audio: same existing AudioRelay (no simultaneous delivery rewrite)
```

Use upstream DLNA playback/state/position/track-end behavior, `_starting_playback`,
source grace, current/armed-URI checks and external-release wiring. Remove the
second physical-forwarding-versus-virtual callback terminal decision for normal
transport ownership. Physical Spotify evidence remains a separate revocation signal;
it must not be inferred from absence of a virtual renderer alone.

Keep the dependency pin and relay for this first comparison. Test upstream
`AudioProxyServer` separately only if delivery/URL-expiry evidence calls for it;
its URL provider/expiry handling and separate server lifecycle differ from ours.

## Concrete smallest viable experiment

Scope: one configured, ungrouped room, CD quality; opt-in `PLAYBACK_PATH=direct_dlna`.
Default/current path remains intact. Do not delete the custom controller yet.

1. Keep `Service` authentication/token-refresh lifecycle and room-derived UUID.
   Retain discovery for configured rooms while a renderer is absent; expose binding
   state as unavailable rather than destroying the receiver. Never advertise a
   non-local LAN binding or silently enable another room/group.
2. Add read-only Node binding lookup using current topology and discovered
   `device.upnpClient.url`; validate zone UDN, room membership and renderer identity
   by fetching XML, not by friendly name/IP alone.
3. Resolve at backend `play`, after admitted selection and metadata lookup but
   before SetAVTransportURI. If the room is genuinely unassigned, permit exactly
   one demand-driven `connectRoomToZone` for that admitted selection. Await topology
   and matching discovery; never drop a zone, regroup, retry uncertain creation or
   repair a missing renderer in a still-assigned zone. Missing assigned renderers
   wait boundedly or return unavailable.
4. For address change with the same verified UDN/membership, connect a new DLNAClient
   read-only, cancel/fence old pending work, atomically swap the client, and close
   the old HTTP session without transport Stop. Retain Receiver/player/queue/WS/
   PlayReporter. Different identities require fresh explicit admission.
5. Retain one receiver admission/revocation generation. Fence at the final SOAP
   send, including deferred volume tasks, not just the player entry point. Reuse
   player command/transition generations and external-release API; do not add a
   second transport lease/state machine. Missing/stale authority blocks commands.
6. Reuse upstream `_starting_playback` and grace behavior for authorized loading.
   Outside an admitted initial load, native Spotify/contradictory source evidence
   revokes; stop further transport/volume/automatic-next actions and clear cloud
   control without Stop. Rebinding/reconnect/metadata prefetch are not selections.
7. Initially disable gapless arming: upstream transient SetNext failures are retried
   on future polls and an already-armed hardware track complicates takeover. Test
   upstream ordinary natural advancement first; enable gapless only in a later
   separately reviewed experiment.

Files for the implementation, after this design review:

| File | Targeted change |
|---|---|
| `qobuz/dlna_backend.py` (new) | Lazy binding, upstream DLNABackend/client subclasses, final-send generation/source gate, single-shot mutation policy, close-only rebinding. |
| `qobuz/receiver.py` | Opt-in backend factory; same UUID/admission/WS/reporting wiring; native external-release callback. |
| `qobuz/service.py` | Keep configured-room discovery alive independently of temporary renderer availability; no automatic repair. |
| `qobuz/client.py` | Read binding/prepare a truly missing zone; no Python→Node playback commands in direct mode. |
| `raumkernel/binding.js` (new), `api.js`, `main.js` | Authenticated binding observation and explicit-only missing-zone preparation; existing observer/store, no drop endpoint. |
| `qobuz/diagnostics.py` | Allowlisted reporting attempt/result/status and physical-volume readback diagnostics. No bodies/blob/context/account IDs. |
| `tests/test_dlna_backend.py`, binding tests, integration | Disappearance, rebinding, old callbacks, takeover, no automatic mutations/retries, knob/no-echo, truthful results. |

Reuse auth/setup, stable identity, relay, safe diagnostics, room configuration,
topology parser/discovery/removal signals and explicit selection admission.
Simplify volume/position/state reporting by using upstream DLNA queries. Bypass
`raumkernel/control.js`, renderer-command dispatch and `RaumfeldBackend` playback
monitor in direct mode; preserve them unchanged as the comparison/fallback path.
Do not replace the service with stock upstream Speaker wholesale.

## Concrete upstream limitations/adaptations

- **No renderer-independent stock startup:** `Speaker.start` connects the backend
  before starting discovery; `BackendFactory.create_dlna` raises if connection
  fails. The existing independently advertised Receiver is useful here.
- **No public hot binding API:** `DLNAClient.reset_session` only rebuilds its HTTP
  session; it does not discover/fetch a new description URL. Calling backend
  `connect()` again overwrites the client/starts another poll task, not an atomic
  receiver-preserving rebind. Stock configuration PUT hot-restarts the Speaker.
- **Unsafe retry policy for this experiment:** client `_soap_action_detailed`
  retries by default. Backend `_play_via_transport` additionally resets the HTTP
  session, sends Stop and retries loading/Play. Override mutation retry count and
  transport recovery; failures must raise to the player, not return as success.
- **Failure acknowledgements:** pinned backend `_play` returns normally after
  transport failure and `set_volume` caches the requested value regardless of the
  client's boolean result. Narrow adapters must report unknown/rejected correctly.
- **Idle volume and queued setters:** stock volume can run before any track is
  owned, and DLNAClient's debounce task later calls the setter directly. Fence
  these at send time; do not let shutdown/rebind revive old queued volume work.
- **Takeover on disappearance:** `_owns_transport` returns false on unavailable/
  empty URI; `check_external_playback` returns the external flag, not that boolean.
  Missing evidence must block advancement/commands rather than be assumed owned.
- **Knobs:** backend `get_volume` reads the device, but its state poll does not
  continuously broadcast hardware volume changes. Player only reports explicit
  set/broadcast operations. Add readback on node RenderingControl events plus a
  modest fallback while active, emit a changed value through the existing report
  callback, never send a setter to "correct" a knob. Ignore/deduplicate server
  echoes: stock VolumeCommandHandler applies volume-change broadcasts as setters
  and does not actually check rendererId.
- **Selection semantics:** stock Speaker treats every discovery connect as explicit,
  calls `prepare_for_selection` and activates WS, including repeated session IDs.
  Do not adopt this wholesale. Retain consumed/revoked-session protections. A same-ID
  network retry is not provably a new user selection; this remains a protocol/UI
  limitation for the desired seamless reselect behavior.

For a single ungrouped zone, upstream SetVolume/GetVolume is logically zone-wide
and may coincide with the room; this must be hardware-tested, not assumed from
successful getters. If the declared room setter is chosen, choose it in advance,
serialize InstanceID/Room correctly, and never try a second action after a reset.
Upstream force-close TCP sessions differ from the Node SOAP transport and are a
plausible comparison variable, not a proven explanation for the resets.

node-raumkernel already long-polls `getZones` with updateId and emits topology/
device events. Prefer those. Modest read-only fallback/freshness checks can recover
missed notifications; neither intervals nor health failures authorize zone mutation.
The final-send gate must also cover every timer/queued callback: authority captured
when work was queued cannot be replaced by a newer selection's authority. Retire
old client generations before connecting/swapping replacements. A native source
event cancels authorization, not merely a cached playing-state label.

## Reporting/scrobbling: actual path and explicit offline verification

Both stock Speaker and our Receiver construct `PlayReporter(api)` and pass it to
QobuzPlayer. The custom path does not intentionally bypass it. Player
`_start_playback` → `_report_playing` → `PlayReporter.note_playing` → API
`report_streaming_start` POSTs `/track/reportStreamingStart` (form `events`).
Natural end/Stop/source release call `_report_stopped` → `note_stopped` → API
`report_streaming_end` POSTs `/track/reportStreamingEndJson` with stream blob,
queue context, start timestamp and played duration. Pause excludes paused time;
resume does not duplicate start. Qobuz's server-side integration, not a direct
Last.fm client here, is intended to produce listening-history/scrobble records.

Important: starts adopted at **5 seconds or later** intentionally suppress the
start report because upstream assumes the controlling app already reported it.
End reports still run. This assumption deserves account-visible verification on
Raumfeld handoffs, especially after rejected resume seeks.

`_post_report` accepts any 2xx (including start's 201), returns false on HTTP/
connection failure and logs/swallow failures. PlayReporter ignores this boolean.
Consequently audible/player success is not reporting success. Current diagnostics
set the API logger to INFO (success logs are DEBUG) and do not match report failure
warnings, so neither positive nor negative report evidence is shown. This is an
observability gap, not proof of the account's failure cause. Missing blob/context,
handoff suppression, HTTP refusal and account integration are separate hypotheses.

Six offline probes pass in `tests/test_direct_dlna_review.py`, using the actual
pinned API and current derived player, a local synthetic HTTP server and no real
credentials/audio/devices:

1. Start and end POSTs reach their intended endpoints, with auth/session headers,
   quality/blob/context and pause-excluded duration; server returns 201.
2. Adopted handoff suppresses start but sends end.
3. Server 403 is swallowed while playback still returns success; current diagnostics
   omit that failure.
4. Stock backend retries URI mutation and sends Stop after failure.
5. Stock Speaker does not reach discovery if backend creation fails.
6. The real report API returns true for 201 and false for 403, independently of
   the player's successful return value.

These prove local code paths/responses only. Real Qobuz acknowledgement, listening
history and Last.fm records remain unverified, consistent with the user's report.
At the design checkpoint the full Python suite passed 50 tests, including these
six probes. The then-inherited Node expiry expectation was 82/83. Current
implementation-suite results are recorded at the top of this document; neither
run constitutes hardware acceptance.
Before hardware comparison add sanitized start/end attempt + accepted/status +
duration/blob-present/context-present fields; never log the values themselves.
Compare a fresh track from zero, a mid-track handoff, pause/resume and natural end.
Verify actual endpoint responses separately from account-visible history/Last.fm,
using the user's linked account and confirmation; do not fabricate test reports.

## Image, rollback and ordered hardware comparison

Build the candidate only after the adaptations/final-send safety tests pass:
`docker build --build-arg PLAYBACK_PATH=direct_dlna --label
org.opencontainers.image.revision=<exact-experiment-commit> -t
raumfeld-connect:direct-dlna-one-room-review .`. Record the exact image digest and
revision separately after build/smoke checks. No registry publication or production
configuration update is part of source/image preparation.

Before requesting deployment approval, retain the actual current runtime image
and full private configuration/rollback image; preserve `/data`, credentials, LAN
assignment, automatic discovery, host networking, pull policy and all other apps.
The working-branch WIP is not the deployed rollback. The proposed deployment delta
is this app's image only; the candidate embeds `PLAYBACK_PATH=direct_dlna`, while
the retained rollback image uses the original guarded path. Verify that existing
runtime environment does not override this flag. Retain all existing settings
and the already selected one-room/CD configuration. Do not replace credential/
config stores or modify another service.
Rollback restores the prior runtime image and private configuration, with no
speaker Stop, regrouping, volume restoration or uncertain mutation retry.

Hardware sequence after explicit approval:

- Fresh selection: audible playback; pause/resume; phone volume; physical knobs
  with phone readback and no setter fight.
- Manual next and natural completion; correlate reports and confirm actual history/
  Last.fm separately. No claim based only on HTTP acknowledgement or audio bytes.
- Bridge idle while Spotify plays continuously, spanning multiple fallback checks
  and tracks: assert zero zone/transport mutations from the bridge.
- Qobuz → Spotify: invalidate old callbacks/auto-next, no forced Stop/recreate.
- Spotify → fresh explicit Qobuz selection: one authorized load, no background reclaim.
- Renderer disappears/changes address: retain room identity; read-only identity-
  verified same-renderer rebind without restarting session; otherwise unavailable
  until authorized demand. No automatic zone reclamation.

Record verified behavior and hypotheses separately. The first implementation
decision is backend-only migration with the existing relay, not another full
rewrite of discovery, authentication, sessions and delivery at once.

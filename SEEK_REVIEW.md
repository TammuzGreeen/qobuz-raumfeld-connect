# Seeking investigation and candidate hardware review

The published `upstream-first-preview-2026-10-12` tag and preserved working image
`560ee29` remain unchanged. After separate user approval, candidate `361e46e` is now
deployed. Paused scrubbing stays silent and explicit Play resumes from the selected
point in the user hardware test. Earlier `d58a5cd` passed playing scrubbing but
auto-resumed during paused scrubbing. Fresh nonzero starts and broader same-image
acceptance remain pending. No agent-driven
alternative Seek has been sent to the real renderer. See deployment/test evidence below.

## Findings

1. The recorded live positive-position update reached the renderer. REL_TIME Seek
   was rejected with definite UPnP 710, without source/activation changes. This is
   not an Android admission/session failure or a stale-load cancellation.
2. Stock pinned DLNA backend discarded the client's false Seek result. The player
   then advanced/reported its requested position anyway. Starting mid-track also
   reported the target before post-start Seek was acknowledged.
3. Stock `apply_remote_state` seeks for position, then `_play_locked(position)`
   seeks again if already playing. Stock therefore duplicates a combined position
   + PLAYING update. Our requested-mode backport extends this to position-only
   updates while playing, reproducing the two live rejected Seeks.
4. Current newer upstream HEAD
   `10dedc9d2ac0ff2edcad729e22876d2c7f158c8e` still reproduces these behaviors. A
   dependency update is not proposed; the pin remains unchanged.

Actual stock protobuf/command handler/player/DLNA and identical deterministic fake
SOAP inputs reproduced the failure without network access:

| Input, initially playing unless stated | Unmodified pinned upstream | Current preview | Candidate |
| --- | --- | --- | --- |
| Position-only scrub | One rejected Seek; false position success | Two rejected Seeks; false position success | One Seek; rejection leaves position unchanged |
| Position + PLAYING | Two rejected Seeks; false position success | Same | One Seek; no second mutation |
| Paused position-only scrub | One rejected Seek; false position success | Same | One Seek; stays paused, no false position |
| New track + nonzero start + PLAYING | Reports target before rejected Seek | Same | One post-start Seek before positive position report; rejection returns false and reports readback |

The backend still really starts audio before a post-start Seek. A definite rejected
offset leaves that actual playback running at its observed position, without
recovery Stop/retry; it does **not** mean the requested mid-track start succeeded.
The candidate preserves legacy non-DLNA post-start behavior via an explicit backend
capability, rather than silently changing the guarded renderer's reconciliation.

## Raumfeld mode hypothesis

Read-only device description/SCPD inspection found ABS_TIME, REL_TIME and TRACK_NR
advertised. GetCurrentTransportActions advertised Seek; current DIDL metadata had
an HTTP resource with no DLNA operation flags. These declarations do not prove
which Seek mode actually works for the proxied FLAC on this firmware.

[PyRaumfeld reference](https://github.com/maierp/PyRaumfeld/blob/d7db2a97e790a8246db54e4a1e0c84cbaa7a0b34/raumfeld/__init__.py)
defaults to ABS_TIME. The candidate chooses **one declared ABS_TIME request** for
the configured Raumfeld virtual renderer. It validates the Seek inputs and mode
in bounded, same-origin SCPD before transmission, never sends REL_TIME then falls
back, and never changes Node into a playback controller. Stock non-room DLNA
continues using REL_TIME. ABS_TIME success remains a **hardware-test hypothesis**,
not a demonstrated resolution of live error 710.

The original upstream audio proxy is unchanged. Its byte-range support is not
treated as proof of renderer time-seek support. No proxy offset/transcoding,
playlist substitution, regroup, timed pairing, blanket autoplay or transport retry
is introduced.

## Candidate changes and tests

- `upstream-patches/seek-acknowledgement.patch`: isolated DLNA result propagation,
  single-Seek player path and acknowledged/readback start-position reporting.
- Hash-checked installation after the existing partial-intent backport; both
  target files verify/compile before either is changed.
- `qobuz/upstream_backend.py`: declared ABS_TIME capability check and return value;
  supersession check immediately before Seek transmission, alongside unchanged
  source/activation/endpoint retirement checks. Definite 710 is not retried;
  other failed/uncertain mutations remain terminal and release authority.
- Synthetic tests: position-only/combined updates, paused scrub/resume, zero and
  nonzero starts, rejection without false position or retry, superseded requests,
  Pause/Stop during loading, takeover before transmission and source retirement
  after acknowledgement, uncertain failure without recovery Stop, missing ABS_TIME
  declaration, and fail-closed patch installation. Both Next orders remain covered.

Prototype overlay passed 162 project Python tests and eight standalone upstream
tests (170 total), including guarded-path regressions. Final built-image checks,
candidate identity and any hardware evidence are recorded separately; no cloud
report/scrobble acceptance is implied. Private real-device logs/SCPD/metadata stay
outside Git; fixtures and positions in tests are synthetic.

## Hardware acceptance still required

Separate approval is required to deploy this candidate or send agent-driven Seek.
Retain the known-working preview as rollback and preserve configuration/credentials,
host networking, exact one-room/CD selection and corrected LAN address.

After approved deployment: establish audible playback, scrub once to a different
point, inspect categorical single-Seek ABS_TIME acknowledgement and confirm audible
position/Android feedback. Then check paused scrub/resume and fresh nonzero start,
plus both Next orders where Android emits them, natural advancement and volume.
If ABS_TIME still receives 710 or does not move the audible position, stop and record
failure; do not probe fallback modes or retry uncertain mutations. Spotify → Qobuz
→ Spotify handoff follows seeking acceptance; scrobbling remains deferred.

## Prepared image — pre-deployment record

- Candidate implementation: `d58a5cd01b2e1c5cde46c4267eccaa71ad5b096b`.
- Local amd64 image: `raumfeld-connect:upstream-first-seek-review`.
- Local OCI index ID:
  `sha256:fbbadf075b846d89275354e0d1842b884b7457ac042bcf40b0f9061c5026e2a3`.
- Private exported archive SHA-256:
  `fde6248dfabb9b9b7fbf971a345217a1ab3f566d604ae88bc48561edb646c312`.
- Built candidate passed 162 project Python tests plus eight standalone upstream
  contribution tests (170 total), and offline supervisor/UI/health/storage/shutdown
  smoke. All six original pinned serialization tests passed on patched upstream;
  all 104 serial Node tests passed in the development-dependency harness.
- Diagnostics are built in and emit the fixed `absolute_time` Seek-mode category,
  not requested positions, metadata or URLs. Runtime credentials/state are not
  part of the image/archive. No registry publication or prerelease replacement.

The working `560ee29` deployment and source-only prerelease are retained unchanged.
This candidate has **no hardware seeking acceptance**. No agent-driven mutating
speaker controls occurred during investigation; only bounded read-only descriptor,
SCPD/metadata/action-list inspection and isolated synthetic playback were used.
The next step requires separate deployment approval, not a claim that ABS_TIME has
already solved the hardware failure.

## Approved deployment — hardware acceptance pending

User separately approved deployment for a controlled seek test. Candidate
`d58a5cd01b2e1c5cde46c4267eccaa71ad5b096b` is deployed as
`raumfeld-connect:upstream-first-seek-review`. Imported image ID/config digest is
`sha256:8855faf4fd553e9ee71124f32ac47154d52d3c8a1958ec8cf5295c9cdcc4081a`
(distinct from the local OCI index above). Private archive checksum matched.

Retained a fresh private before-state, effective Compose and rollback archive of
the working preview, then performed one image/pull-policy-only app update without
retry. Verified healthy/authenticated, zero restarts, unchanged environment mapping,
host networking, mounts, original config/credentials and optional cache state,
same room configuration/identity/membership and available endpoint, unrelated
containers unchanged, diagnostics enabled, exact installed backport byte hashes
and actual upstream launcher argv. No legacy receiver/service entrypoints.
Startup diagnostics showed zero SOAP transmissions; no agent speaker controls
were sent during deployment.

`hardwareVerified: false`. First establish audible playback from the beginning,
then perform one Android scrub and inspect the single ABS_TIME Seek/response and
actual audible position. Do not infer seek success from health or SOAP alone.
The published preview remains the known-working rollback; its control acceptance
does not automatically transfer to this candidate. The prerelease/tag is untouched.

## First playing-track scrub — passed

On deployed `d58a5cd`, user confirmed initial audio/Android progress/connection,
then reported dragging into the middle works and the connection stays strong.
Record audible initial playback and playing-track scrubbing as user-observed passed
for this attempt, not blanket acceptance of every seek/start variant.

Categorical diagnostics show a playing-state position-only positive delta (local
event 9), exactly one ABS_TIME Seek transmission, acknowledged true and
unsupportedSeek false, followed by seek completion true with no selection or
activation-authority change. The formerly duplicated position-only path therefore
completed as one acknowledged Seek on hardware. Container remained healthy with
zero restarts. User observation supplies audible success; SOAP acknowledgement
alone would not. Raw logs/metadata/positions remain private.

Paused scrub/resume and fresh nonzero start still require user confirmation, as do
affected controls/progression on this candidate. Spotify → Qobuz → Spotify handoff
remains next after seeking acceptance; scrobbling stays deferred. Overall
`hardwareVerified: false`; the published preview/tag and rollback are unchanged.

## Paused scrub observation — position works, silent pause not preserved

User paused, selected another point and heard playback start immediately without
pressing Play. A later explicit Play did not disrupt audio; connection stayed steady.
This is not acceptance of remaining silent until an explicit resume.

Diagnostics confirm PAUSED was requested and one Pause was acknowledged (local
event 76), leaving the player paused. The scrub (event 78) contained a positive
position and no playing-state field. Only one acknowledged ABS_TIME Seek was sent;
no Play or extra Pause was transmitted during that update, and selection/activation
authority stayed unchanged. The player's subsequent pause method was a no-op
because its cached state still said paused. The state-only PLAYING and SOAP Play
arrived later (event 80), corresponding to the user's explicit button press.

Together with the user's audible observation this points to renderer auto-resume
as a side effect of an accepted Seek, not an app-requested PLAYING delta or a
generation/source-check failure. No pre-button physical transport-state read was
captured, so the exact device-side transition is not independently measured.

An isolated replay with a fake renderer that resumes on Seek reproduced physical
PLAYING while the player stayed PAUSED, with Seek as the only transmitted mutation,
under both stock DLNA and the room integration. Existing paused-scrub tests assumed
Seek preserves renderer pause and therefore missed this device behavior.

Paused-scrub silence is a known failed acceptance item on `d58a5cd`; playing scrub
and steady connection still pass for the recorded attempts. No new playback code,
deployment, agent-driven transport mutation, fallback Pause/Stop or retry has been
introduced for this observation. Preserve the working preview rollback. Confirm
the desired paused-scrub behavior before choosing deferred seek versus deliberate
paused-state restoration; defer handoff acceptance while this is unresolved.

## Silent paused-scrub correction — preparation record

User confirmed paused scrubbing must stay silent until explicit Play. New candidate
development adds `deferred-paused-seek.patch` after the two existing hash-checked
patches, without changing the upstream dependency pin or replacing its player.
Only the room backend enables deferral; stock non-room DLNA and guarded behavior
remain unchanged.

While paused, a scrub performs a read-only authority/ownership check and stores
the latest desired position, including zero. Android state reports that desired
paused cursor, separately from the unchanged applied renderer position. There is
**no Seek, Play, Pause or Stop** during paused scrubbing. This is accepted pending
intent, not an acknowledged renderer seek. It does not advance listening position.

Explicit Play consumes the target once: acknowledged resume first, then one Seek.
A rejected offset reports actual playback without false target success or retry;
failed resume discards the pending offset without sending Seek. Stop, track load,
changed queue occurrence, authority invalidation/fresh selection, source release,
disconnect and endpoint retirement discard the target. Read-only-check supersession
cannot retain an older target under a newer command. No fallback mutation or timed
pairing is introduced.

Tests now model a renderer that auto-resumes on Seek, repeated paused targets,
target zero, desired versus applied reporting, explicit Play consumption, rejection,
failed resume, Stop/load/selection/source/disconnect/endpoint invalidation, takeover
and supersession during the ownership read. Existing Next, volume, playing-scrub
and guarded regressions remain in the suite. Standalone upstream-only tests cover
the pending-position semantics separately.

Current deployed `d58a5cd` still has the observed paused-scrub failure. This correction
requires a separately approved deployment and user audible-silence/position test;
no transport mutation or deployment is authorized by the behavior confirmation alone.
Overall `hardwareVerified: false`; preserve the working preview and immutable tag.

Prepared implementation `361e46eec4bc745530cc70bfc6ce215410f1bc2a` is archived
locally as `raumfeld-connect:upstream-first-paused-seek-review` (amd64), not deployed
or registry-published. Local OCI index is
`sha256:b50a5d0edc0c649ae97bd96e2fa08ae91e45367ca398ebf4a936f662e3ecb423`;
private export SHA-256 is
`0766c22e32b016013fc53a479a52a3d06e32581c4a331efe0cbe47667d3f0993`.
Built-image verification passed **179 Python tests** (169 project and ten standalone
upstream contribution tests), all six original pinned upstream serialization tests
and offline supervisor/UI/health/storage/shutdown smoke. No Node implementation
changed; prior 104 serial Node test results remain the prior candidate's evidence,
not a newly run hardware/control acceptance. No speaker controls or deployment
were performed during this preparation.

## Approved silent paused-scrub deployment — acceptance pending

User separately approved deployment of `361e46eec4bc745530cc70bfc6ce215410f1bc2a`.
It is now running as `raumfeld-connect:upstream-first-paused-seek-review`. Imported
image ID/config digest is
`sha256:ecb5bbb89c611089530386ca4159cb019ba02a2ab7355c675ace08a7c2083967`,
distinct from the local OCI index above. Archive checksum and installed player/
DLNA backend hashes matched; room-only deferred paused Seek capability is enabled.

Retained fresh private before-state/Compose/storage hashes and an archive of the
previous `d58a5cd` image. The original tested `560ee29` working preview rollback
also remains retained. Performed one image/pull-policy-only app update, without
retry. Verified healthy/authenticated, zero restarts, unchanged credentials/config
and optional cache state, environment, host networking, mounts, room configuration/
identity/membership/endpoint and unrelated containers. Upstream launcher and
diagnostics are present; no legacy receiver/service entrypoints. Startup showed
zero SOAP transmissions; no agent speaker controls were sent.

Hardware acceptance on this image is pending: establish audible playback, Pause,
move the progress bar once and confirm silence continues, then explicitly Play
and confirm audible playback from the chosen position with a steady connection.
The paused cursor is desired intent until Play, not an applied renderer Seek.
No prior-image control or seeking pass automatically transfers to this image.
Overall `hardwareVerified: false`; published preview/tag unchanged, handoff tests
follow seeking acceptance, and scrobbling remains deferred.

## Silent paused scrub and explicit resume — passed on `361e46e`

User completed audible playback → Pause → moving the progress bar while paused →
explicit Play and confirmed everything works. Record initial audible playback,
paused-scrub silence, audible resume from the chosen point and connection stability
as user-observed passed for this attempt on the current image.

Categorical capture shows one acknowledged Pause (local event 58), a positive
position-only update while paused (event 60) with **zero transport transmissions**,
then explicit PLAYING (event 62) with one acknowledged Play followed by one
acknowledged ABS_TIME Seek. No 710, no duplicate Seek, no selection/activation
authority change. The paused seek method's true result means desired intent was
retained, not that a renderer Seek occurred. User observation supplies audible
silence and resume-position evidence; acknowledgements alone do not.

Healthy, zero restarts; no agent controls or deployment changes during evidence
collection. Raw logs stay private. Fresh nonzero starts, current-image playing
scrubbing/control regression and both source handoffs remain distinct outstanding
checks. Proceed to user-driven Spotify → Qobuz → Spotify handoff using fresh explicit
Qobuz selection plus Play; do not infer reclamation authority from connection alone.
Scrobbling remains deferred and overall `hardwareVerified: false`. Published
preview/tag and rollback unchanged.

## Spotify → Qobuz handoff — failed on `361e46e` (2026-10-14)

User let Spotify play, explicitly selected the same room in Qobuz and pressed Play.
Qobuz produced no audible playback on phone or speakers, then lost its app
connection shortly afterward. Record this handoff as failed, not as successful
source replacement or a seek regression. Whether Spotify continued playing after
the attempt is not yet reported; reverse handoff is not tested.

Read-only capture shows a different-track paused/positive-position update (local
event 1183) loaded successfully without transport start, then explicit PLAYING
(event 1184) reached `_start_playback`. Playback failed in
`DeferredRoomBackend.play` → `CompanionClient.create_for_play` → `request`, where
the `/v1/zone-for-play` response was non-200 (`companion_unavailable`). No Qobuz
AVTransport URI or Play transmission appears in this attempt. This is renderer
provisioning failure before audio transmission, not a rejected Seek or stale-command
guard. Exact HTTP status and companion response error were not retained by the
Python exception; do not infer the particular creation-error category from it.

Current read-only companion catalog shows one room/physical renderer, unassigned,
without a virtual-renderer endpoint and not catalog-unavailable. Node logged
ECONNREFUSED to a non-loopback dynamic speaker-service port approximately 33 seconds
before explicit Qobuz Play. This is a potential related service/discovery failure,
not yet a proven cause of the later provisioning response. No request was repeated
to recover its missing error body or probe uncertain renderer creation.

Container remains healthy/authenticated with zero restarts. These do not establish
an active Android session; the user's app-connection loss stands as observed.
Raw logs/status/catalog remain private. No agent controls, renderer creation,
regroup, retry or deployment change during diagnosis. Prior paused-seek hardware
pass remains valid for its recorded attempt; handoff acceptance is failed and full
`hardwareVerified: false`. Scrobbling stays deferred; immutable preview/rollback
unchanged. Further provisioning/error-propagation investigation is required.

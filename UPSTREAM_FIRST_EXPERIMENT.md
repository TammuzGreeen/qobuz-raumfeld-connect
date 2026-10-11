# Upstream-first one-room experiment

## Status and scope

This branch is a separate upstream-first candidate, **deployed with explicit
user approval, not hardware-accepted**. Build with `PLAYBACK_PATH=upstream_first` to activate the new
launcher; default builds retain the old guarded path for rollback/comparison.
No real speaker commands or runtime credential changes are part of preparation.

Implemented playback path:

```text
Qobuz Android
  -> pinned upstream DiscoveryService / Speaker / WsManager
  -> upstream CommandHandler / QobuzPlayer / reporting / AudioProxyServer
  -> upstream DLNABackend (narrow lifecycle compatibility only)
  -> discovered Raumfeld virtual renderer
```

Keep upstream pinned at `91db990abeb486005a8315a6b1e722df776c4ba4`.
Use the original reconciler's user-observed successful Qobuz behavior as the
acceptance baseline, not the custom receiver's admission/replay behavior.

## Node companion

- `raumkernel/companion-main.js` is an independent Node entrypoint; it does not
  import the custom controller, binding service, arbitration or receiver.
- `GET /v1/endpoints` exposes configured room identity and the current virtual
  renderer description URL. Reads never create, drop or regroup a zone.
- `POST /v1/zone-for-play` permits one creation attempt for a truly unassigned
  configured room. It cannot repair an assigned missing renderer. Pending or
  uncertain operations cannot be retried until discovery observes assignment.
- The companion API is authenticated and loopback-only. It has no session,
  lease, replay registry, transport, volume or arbitration endpoint.
- `qobuz/endpoint_catalog.py` supplies immutable endpoint validation and a pure
  reconciliation planner. Actual in-place rebinding lives in `upstream_backend.py`.

The zone API is an internal capability, not proof of an Android Play. Only the
upstream explicit-Play integration invokes it, after stock explicit selection,
only for a truly unassigned room. Background discovery, token refresh, endpoint
retries and automatic progression cannot create a zone. A missing assigned
renderer remains unavailable until rediscovery. Source notifications carry a
monotonic evidence counter, not a lease or a grant of playback authority.

First-checkpoint validation: **8 new Node tests and 4 new Python tests** passed;
the full inherited regression suites passed **103 Node / 85 Python tests**.
Standalone companion startup, authenticated catalog and clean SIGTERM passed
with discovery disabled. Python regressions used a network-isolated container;
Node tests used synthetic services and loopback HTTP. These results do not prove
upstream application integration or hardware acceptance.

Final review-image checks: **104 Node / 109 Python tests passed**, including the
definite unsupported-Seek regression. The new
upstream-first tests include actual stock discovery HTTP (including repeated
valid session IDs), upstream Speaker/WebSocket/queue/player/reporter construction,
deferred zone demand through a real Node companion process, synthetic SOAP,
stock audio-proxy bytes, pause/resume/seek, manual next and natural repeat,
reporting calls, preserved objects/session during address rebinding, source
switch races, unknown transport reads, volume readback/no echo, topology changes
and shutdown without transport mutations. The upstream-first supervisor/UI/health
and original-file preservation smoke passed in a read-only network-isolated
image; the retained guarded process smoke and Compose override validation passed.
The offline image smoke can be run with the mounted `tests/upstream-smoke.py`.
GitHub CI workflow changes are omitted from the published checkpoint because
the available OAuth token lacks workflow scope. The optional CI addition and
original unpublished history are retained locally; no published history was rewritten.

One concurrent full-suite repeat hit an inherited guarded-controller test's
15-ms deadline: it failed closed with `ownership_lost` rather than the asserted
`source_not_confirmed` category. Earlier concurrent full runs passed; the final
image's serial full run passed all 104 Node tests. That old controller is not
loaded by upstream-first mode. No deadline, guard or assertion was weakened to
obtain the serial pass. The final image also passed all 109 Python tests and
the upstream-first supervisor smoke.

## Upstream application and compatibility seams

`qobuz/upstream_app.py` starts a subclass of stock `QobuzProxy`. Authentication,
speaker startup/retry, `Speaker.start`, discovery connect handling, WebSocket
setup/session management, queue, playback command handling, player, reporting
and audio proxy are upstream. No imports of the old receiver, player, relay,
binding client or backend occur on this launcher path.

The launcher installs process-local extension classes into the pinned upstream
factory slots. These are explicit compatibility seams, not unmodified upstream:

- Backend factory supplies a `DLNABackend` subclass that can start without an
  endpoint. Existing renderers are connected read-only; only Play may request
  creation. DIDL and URI/Play, pause/resume/seek, queue progression and reports use
  upstream implementation. Gapless arming is disabled for this candidate.
- Discovery subclass changes only interface selection. Its Android HTTP connect
  handler is the exact upstream handler, including acceptance of repeated valid
  session IDs. No selection-ID hashing, TTL, admission lease or replay registry.
- WebSocket subclass wraps handler dispatch only, capturing generations before
  upstream handlers schedule their tasks. Upstream player command generations
  are carried across URL/metadata awaits; natural callbacks capture them when
  scheduled. Native Spotify or conflicting virtual URI invokes stock external
  playback release. Fresh explicit selection resets that state; maintenance does not.
- Endpoint rebinding closes old HTTP clients without Stop, keeps the same
  `Speaker`, queue, player, UUID and cloud session, and never loads or Plays.
  Late reads cannot bind an older endpoint or revoke newer authority. Address
  changes preserve session; changed physical membership or virtual identity
  cannot inherit old playback authority. Mere disappearance does not erase identity.
- Client XML reads accumulate to EOF with bounds; renderer UDN/service origins
  are checked. Transport recovery Stop/retry and delayed volume setters are
  disabled. Shutdown retires clients without speaker mutations. Unknown state
  reads do not become natural completion. URI loading uses bounded read-only
  virtual confirmation before Play; there is no physical-forwarding admission gate.
  Definite Seek 710 rejection retains upstream's non-applied Seek result without
  revoking source authority, retrying, or sending a replacement transport command.
- Explicit volume commands stay upstream; cloud volume broadcasts never set
  speaker volume. Active changed-value readback reports knob feedback through
  upstream player callbacks. The declared virtual SetVolume action must match;
  missing/mismatched readback releases control, with no retry/fallback setter.

## Configuration and storage

The loader reads the existing one-room/CD `config.json` and credentials without
altering them. Name and discovery port are preserved, and UUID uses the same
room-based derivation as the prior service. Renderer IP/location never enters
Qobuz-facing room identity. The stock audio proxy uses a separate port:
configured discovery port **plus 100**. Check that port before deployment.

Candidate token refresh storage is `/data/upstream-first/credentials.json`,
private by umask; original credentials/config stay intact for rollback. On a
later candidate start the refreshed candidate token takes precedence. Upstream
UI/status listen on loopback port 8788; speaker discovery/audio bind the preserved
`LAN_ADDRESS`. Room add/edit/remove through upstream UI are disabled, avoiding
restart-on-edit. Use an SSH tunnel for UI access; never expose private snapshots.

## Build, deployment and rollback

Prepared local image: `raumfeld-connect:upstream-first-one-room-review`.
Implementation revision: `a1986d719c754a7a38c77092ede9e19ff191a4e1`.
Exported archive config digest:
`sha256:34dd292bf900e9054d37a4bb276156efc95e031c598d0bedc9dd304e362c9cba`.
Archive: `/tmp/opencode/direct-dlna-research/upstream-first-a1986d71.tar` (outside
Git, amd64). Saved config, revision, embedded mode and filesystem layer identities
were verified against the built image. Docker's local OCI image ID differs from
the saved archive config digest; verify the latter when importing the archive.

Read-only pre-deployment checks confirmed the previous live image was unchanged/running,
host networking, automatic host discovery, one-room/CD configuration, a free
candidate audio-proxy port, `pull_policy: never`, and no Compose override of the
embedded playback mode. Those preflight checks performed no app update, image
import on the live host, speaker commands or runtime storage changes.

**Deployment approved and completed; user-driven hardware acceptance pending**, not a claim
of actual Android connection stability, audible playback, hardware volume/knob
behavior, uninterrupted Spotify, real reporting acknowledgement or scrobbling.
The first checkpoint `2c1b5a8` was pushed to the experiment branch and its remote
commit verified before implementation continued. Image archives/private preflight
data are not part of the published source checkpoint.

### Approved deployment verification

After explicit user approval, the saved archive was imported and its config
digest, filesystem layers, architecture, implementation revision and embedded
mode verified. The parsed live Compose configuration changed **only its image**.
An exact private before-state and current-image rollback archive were retained,
alongside the separately retained known-working rollback.

Post-update checks verified healthy/authenticated upstream application startup,
one advertised room/CD quality, preserved name/UUID/discovery port, available
renderer endpoint, unchanged original config/credential file hashes, mounts,
LAN settings, automatic host discovery and host networking. Only the embedded
playback mode changed with the image. Exact process arguments confirmed
`qobuz.upstream_app` and `raumkernel/companion-main.js`, with no legacy service or
binding/controller entrypoint active. There were zero restarts, an idle speaker
and no Android session selected at verification. Checks sent no transport,
volume or zone mutations. Audible playback and real reporting are not claimed.

### Hardware state mismatch and approved atomic-load correction

On the initial upstream-first image the user reported a steady connection and a playing track,
but Android offered only Play rather than Pause; tapping Play restarted the track.
Read-only checks found upstream status idle/no now-playing metadata while the
virtual renderer reported PLAYING on the upstream audio proxy. The session was
still present and the companion endpoint available. Logs recorded three
`stale_upstream_command` failures after an acknowledged SetAVTransportURI, before
the URI/Play sequence and upstream playing/reporting phase could complete. This
is not evidence of a failed Pause SOAP action: no Pause was offered/sent.

The matching failure was reproduced against the deployed source with synthetic
SOAP: a newer same-session player generation arriving during URI acknowledgement
aborted the started load. The precise live triggering packet is not separately
captured, so it is not claimed that this is the only possible failure.

The atomic-load correction distinguishes ordinary queued player intents from stock
command-handler activation/disconnect authority. Player generations still reject
superseded metadata work before the first mutation. Once a URI write starts,
stock URI+Play completes atomically under the same selection/source/activation
authority; a newer same-session snapshot then runs under upstream's playback
lock, without an unnecessary reload. Native Spotify, virtual-source conflict,
deactivation, disconnect/reactivation, endpoint retirement and fresh selection
remain terminal for old work, including during the committed load. It adds no
retry, compensating Stop, admission lease or session-ID replay registry.

The source suite passed **115 Python tests**, including actual queued upstream
`apply_remote_state`, playing/start-report completion, pre-mutation supersession,
and deactivation/disconnect during acknowledged URI loading. Rejection diagnostics
contain only boolean categories, never session IDs, source URLs or credentials.
Prepared-image results are recorded separately below. After new explicit user
approval the correction was deployed at implementation `b7c48a5`. Subsequent
hardware success, Pause/resume and real scrobbling remain unverified.

The read-only URI confirmation window tolerates only an empty observation or the
freshly observed pre-load URI while waiting for the requested URI. A positive
foreign virtual source is terminal immediately, even if an old bridge URI might
reappear later. Prepared correction image tag:
`raumfeld-connect:upstream-first-atomic-load-review`; its image-only deployment
was explicitly approved.

Prepared correction implementation: `b7c48a5b71ee607300712312d84002988c489a46`.
Exported archive config digest:
`sha256:5dacc2777ef4f578913aed0f924987d477fdf8d5aa1b7cc948e49eff40ffba8d`.
Archive: `/tmp/opencode/direct-dlna-research/upstream-first-atomic-b7c48a5b.tar`,
outside Git. The built correction image passed **104 Node tests (serial), 115
Python tests and the isolated upstream-first supervisor smoke**. Saved image
revision, embedded mode and layers were verified. The existing deployed image
tag was not overwritten, and no image import/app update or speaker-changing
commands were performed while preparing this correction.

Approved correction deployment changed only the live app image, after archive
digest/layer/revision verification and private before-state/rollback preservation.
Post-update health/authentication and one-room/CD advertisement passed. Room
name/UUID/port, physical membership, original config/credential file hashes,
optional token-cache state, mounts, host networking and environment values were
unchanged. The optional token cache remained absent; this is valid when original
credentials supply authentication. Docker reordered environment entries, but
every key/value remained identical. Only upstream-first entrypoints were active,
with zero restarts, an idle speaker and no selected Android session at verification.
No agent-driven transport, volume or zone mutations were issued. Both the
previous upstream-first image and the separate known-working rollback are retained.

### Post-completion failure and approved metadata-feedback correction

On atomic-load implementation `b7c48a5`, the user confirmed Android showed Pause,
audible audio stopped and resumed, and the connection remained steady. Read-only
status corroborated playing with now-playing metadata and an active session.
This establishes initial playback and Pause/resume only; volume remains untested.

After ordinary track completion, the user could select a different track but
could not play it. Sanitized logs showed natural end, an attempted automatic
advance, and later requested loads rejected by `stale_upstream_command` before
SetAVTransportURI. All associated rejection flags indicated only a newer player
command generation, not changed selection/activation authority. Read-only status
was idle/no now-playing metadata, with authentication, endpoint and session still
present. Thus both ordinary automatic advancement and manual Play after completion
failed acceptance; a steady session alone is not successful playback.

A local synthetic same-item metadata-only `apply_remote_state` during the
pre-URI media read reproduced failed load, zero transport writes and non-playing
final state against the deployed source. The earlier synthetic PLAYING snapshot
reproduced the rejection but subsequently recovered; that did not reproduce the
persistent failure. Live protobuf contents were not logged, so metadata-only
feedback is a demonstrated candidate cause, not a proven identification of the
exact live packet.

The new candidate defers only same-item snapshots with neither playingState nor
position while `_start_playback` is in flight. Once the load/report phase finishes,
stock `apply_remote_state` handles that metadata feedback, including late context.
Feedback is discarded if a newer genuine intent, selection/source change, or
different loaded track intervened. Explicit Play/Pause/Stop/Seek and different
queue-item selection still register upstream generations immediately. Existing
pre-mutation supersession tests/guards remain unchanged. No transport retry,
recovery Stop, custom queue/player, ownership lease or session replay admission
is added. A second catalog read before each write catches native-source evidence
that arrived during the preceding virtual-source read.

The source suite passes **122 Python tests**, including real protobuf stock-handler
dispatch across initial Play, ordinary natural advance, queue exhaustion, fresh
selection and Play with metadata feedback. Other regressions cover genuine Pause
supersession, different queue occurrences, late context preservation, failed URI
without retry, native takeover during source reads, and new-selection cancellation
with feedback waiting. Safe diagnostic messages contain no identifiers/metadata.
After separate explicit user approval, the metadata-feedback image was deployed
at implementation `7b5d0a0`. Partial hardware acceptance is recorded below.

Prepared candidate implementation: `7b5d0a0df19f5f4bcb32fb3b0f8491b693730ac9`.
Image: `raumfeld-connect:upstream-first-metadata-feedback-review` (amd64).
Exported archive config digest:
`sha256:240a149a7500cb4cc7c5e69f356a13a63b246220454aa66651cbfe37cd6d86fc`.
Archive: `/tmp/opencode/direct-dlna-research/upstream-first-metadata-7b5d0a0d.tar`,
outside Git. The final built image passed **104 Node tests (serial), 122 Python
tests and isolated upstream-first supervisor/UI/health/storage/shutdown smoke**.
Archive revision, embedded mode and layers were verified. During candidate
preparation, no remote image import, app update or agent-driven speaker mutation
was performed.

Subsequent approved deployment changed only the app image, with exact archive
digest/layers/architecture/revision/mode verified on import. Private before-state
and the immediately previous atomic-load image archive were retained; the separate
known-working rollback image is also available. Post-update health/authentication,
one-room/CD discovery, room name/UUID/port and physical membership checks passed.
Original configuration/credential hashes, optional token-cache state, mounts,
host networking and all environment values were unchanged. Only upstream-first
entrypoints were active, with zero restarts, an idle speaker and no selected
Android session at verification. No agent-driven transport, volume or zone
mutations were issued. Hardware acceptance at deployment time was pending.

### Partial user-driven acceptance on metadata-feedback image

The user confirmed initial audible playback from the beginning, Android showing
Pause and a steady connection. One Pause/resume cycle stopped and resumed audio
with the connection steady. The user then queued one song and let the current
track finish without skipping: the queued song started audibly, Android still
showed Pause and the connection remained steady. Subsequent manual selection/Play
started a different song, again with Pause shown and a steady connection. The
last message did not separately specify that the intervening song had fully
finished, so an exhausted-queue restart is not independently claimed.

Read-only corroboration found upstream playing with now-playing metadata, an
authenticated account, available endpoint and selected session. Sanitized logs
since this image started contained three playing events, one Pause/resume cycle,
one natural end and automatic advance, six deferred metadata-feedback events and
three superseded-feedback drops. No failed playback starts were logged. One
same-authority/player-superseded post-load Seek was rejected before its SOAP write;
the traceback is recorded as a remaining diagnostic finding, not hidden as an
error-free run. The container was healthy with zero restarts. Diagnostics sent no
transport, volume or zone commands.

This is partial acceptance, not overall hardware verification. Small explicit
volume/readback, physical knobs/no cloud echo, manual Next-button behavior,
account-visible history/Last.fm, continuous idle Spotify and both handoffs,
renderer disappearance/rebinding and exhausted-queue restart remain unverified.
No real reporting HTTP acknowledgement or account-visible scrobble is claimed by
these playback/state observations.

### Volume observations and failed Android Next diagnosis

The user confirmed a small Android volume adjustment changed audible volume,
stayed at the new level without jumping back and kept the connection steady.
Physical knobs also changed audible volume; Android's indicator followed and the
connection stayed steady. These establish user-observed volume control/feedback,
not proof that cloud feedback never issues another setter under every race.

Android Next-button acceptance then failed: the user reported no audible next
track, while Android still showed Pause and the connection seemed steady.
Read-only status found upstream idle/no now-playing metadata, the renderer
STOPPED at position zero, and an authenticated/present session with an available
endpoint. The renderer URI still identified the previously started upstream
audio-proxy track, not the newly loaded one. Logs showed a new track loading and
successfully loaded metadata, but no subsequent start/playing event, failed start,
new generation rejection or command-handler traceback for that attempt. No
audio HTTP request for the newly loaded track was present in retained logs.
This narrows the failed boundary to loaded-track-to-transport-start, not a
confirmed Play/SOAP acknowledgement followed by silent delivered audio. The
earlier superseded post-load Seek traceback predates this attempt and is not
evidence that it caused Next to fail.

A private offline replay using the actual stock command handler and synthetic
protobuf reproduced this state when the new-item SET_STATE omitted playingState:
the outgoing renderer stopped, the next item loaded, the old URI remained and
no new URI/Play was written. A subsequent explicit PLAYING SET_STATE successfully
sent URI+Play. This demonstrates a compatible load-only path, **not** the exact
live packet: decoded live state fields and supersession decisions are not logged
at current INFO verbosity. A load-only/paused Next snapshot or an unobserved
superseding state remains a hypothesis pending sanitized command-state evidence.
Do not compensate by auto-playing a metadata/selection snapshot or retrying
transport. No live configuration/image changes or speaker mutations were made
during diagnosis. Manual Next remains failed; reporting, source handoffs and
remaining hardware acceptance are still unverified.

### Approved command-state diagnostic image preparation

After the silent Next, the user pressed Pause once: Android changed to Play and
the connection stayed steady. Pressing Play once without selecting another track
or reconnecting then started the already-selected song audibly, showed Pause and
kept the connection steady. Read-only upstream status confirmed playing with
metadata. This confirms explicit Play recovery, not a working Next command.

The user requested a diagnostic image and then explicitly approved its deployment
as soon as preparation checks pass. This image changes logging only: stock
command/session/queue/player and existing safety/feedback behavior remain intact.
It does not turn selection-only updates into playback or add retries. Diagnostics
are opt-in at build time with `UPSTREAM_STATE_DIAGNOSTICS=1`, represented by an
image-only `/app/.upstream-state-diagnostics` marker. Default builds remain off;
existing environment keys/values and app configuration need not change.

Diagnostic INFO records contain fixed categories/booleans for incoming SET_STATE,
target same/different/absent comparisons, position absent/zero/positive, player
entry/completion, generation-change comparisons, source active state, outgoing
state-send results and SOAP intent/send/response. Local sequential diagnostic
event numbers correlate handler tasks; they are not cloud/session/track IDs.
SOAP responses are logged before post-response safety checks so an acknowledgement
followed by rejection is distinguishable from rejection before a transport write.
Outbound WebSocket send success is not a cloud/account reporting acknowledgement.
No protobuf dump, metadata, IDs, URLs, tokens or precise positions are logged.
No additional network calls or awaits beyond forwarding the original operations
are introduced by observers. Stock websocket setup remains inherited unchanged.

Source validation passed **127 Python tests**, including private-field sentinel
checks, real stock load-only/explicit-Play replay, unchanged SOAP mutation counts,
failed-URI/no retry, acknowledged URI followed by deactivation/no Play, preserved
WebSocket return values/task cancellation, and diagnostic-enabled real
Node-companion/stock-Speaker/audio-proxy composition. Final artifact/deployment
checks are recorded below. The metadata-feedback image was unchanged during
preparation; the separately approved diagnostic image is now deployed.

Diagnostic implementation: `69fd3941642e664eee2aaf41f92ec11818fbe48d`.
Image: `raumfeld-connect:upstream-first-state-diagnostic-review` (amd64).
Archive config digest:
`sha256:173d1d7aefbed8b350453dcf1eac18cd57bf2cbf03c1528c2556b7bf355fde6d`.
Archive: `/tmp/opencode/direct-dlna-research/upstream-first-state-diagnostic-69fd3941.tar`,
outside Git. Final image validation passed **104 Node tests (serial), 127 Python
tests and isolated upstream-first supervisor/UI/health/storage/shutdown smoke**.
Diagnostic marker/label, revision, mode, archive layers and imported digest were
verified before the approved image-only app update. Private before-state and
the immediately previous image archive were retained, along with the separate
known-working rollback.

Post-deployment health/authentication, one-room/CD advertisement, name/UUID/port,
physical membership, original file hashes, optional token-cache state, mounts,
host networking and every environment value were preserved. Diagnostic marker
and label were enabled; only upstream-first entrypoints were active. Verification
found an idle speaker, no selected session and zero restarts. No agent-driven
transport, volume or zone mutations were issued. Next reproduction and exact
command-state diagnosis on this image remain pending user-driven testing; this
deployment is not a playback fix and is not overall hardware acceptance.

### Diagnosed split Next/Play race and approved correction

On the diagnostic image, initial playback was user-confirmed audible with Pause
shown and a steady connection. One user-driven Next did not start new audio;
Android returned to the currently running track. Categorical diagnostic records
identified the actual sequence: a different current item arrived without a
playingState or position, followed approximately 42 ms later by state-only
PLAYING with neither current item nor position. The item-selection handler had
entered stock `_load_track_locked` and was reading ownership before Stop. The
following Play registered a newer player generation; our pre-write guard rejected
the selection handler's Stop before `soap_sending`. The load failed without
changing the current track. State-only Play then found the old track still
PLAYING and returned success without URI/Play for the new item. Subsequent state
reports continued to advertise the old item. Selection and activation authority
were unchanged throughout this attempt.

This confirms a split item/Play serialization defect for this diagnostic attempt,
not a missing Play packet, silent acknowledged audio delivery, native source
takeover or network/session failure. The prior non-diagnostic silent Next had
different visible end state; its exact packets were not captured and are not
retrospectively claimed identical. The diagnostic image did not fix playback.

The exact two-message/pre-Stop-read race was reproduced against deployed source
with synthetic protobuf and SOAP: the previous item remained current. The local
correction keeps stock locked item loading and defers only incoming state-only
PLAYING while that load is in flight. It registers the follow-up's upstream
generation after the selected item successfully loads. It never infers Play from
selection metadata. Failed loads, changed selection/activation/source, changed
target and newer real player intents discard the deferred Play. Explicit Pause,
different item selection, deactivation/disconnect and Spotify still supersede;
failed or uncertain transport commands are not retried.

Source validation passed **135 Python tests**, including the exact split Next/Play
race, source takeover, new selection, deactivation, disconnect/reactivation,
genuine Pause, newer item selection and failed Stop while split Play waits.
Existing diagnostics, metadata-feedback, pre-mutation supersession, source safety
and stock connection tests remain intact. After separate explicit user approval,
the split-Next correction is deployed at implementation `78f6f06`. Hardware
acceptance of this playback change remains pending.

Prepared correction implementation: `78f6f06c77b0b99fe89aed55c8bd890f8ba2f349`.
Image: `raumfeld-connect:upstream-first-split-next-review` (amd64), with categorical
diagnostics retained. Archive config digest:
`sha256:99ed19466895ef42ea2919f627ea4a07f1ed8837d280c05df1fd7c7e9d6bfec1`.
Archive: `/tmp/opencode/direct-dlna-research/upstream-first-split-next-78f6f06c.tar`,
outside Git. Final image validation passed **104 Node tests (serial), 135 Python
tests and isolated upstream-first supervisor/UI/health/storage/shutdown smoke**.
Image revision/mode/diagnostic label and archive layers were verified. No remote
import, app update or agent-driven speaker mutation occurred during preparation.

Approved split-Next deployment changed only the app image after exact imported
digest/layers/architecture/revision/mode/diagnostic-label checks. Private
before-state and the immediately previous diagnostic image archive were retained;
the separate known-working rollback is also available. Post-update health and
authentication passed, with one-room/CD discovery and unchanged room name/UUID/port
and physical membership. Original configuration/credential hashes, optional
token-cache state, mounts, host networking and all environment values were
preserved. Diagnostics remained enabled, only upstream-first entrypoints were
active, and verification found an idle speaker, no selected session and zero
restarts. No agent-driven transport, volume or zone mutations were issued.
Fresh playback and the Next-button correction require user-driven hardware
verification; synthetic tests and deployment health do not establish audible
Next playback or overall hardware acceptance.

### Split-Next hardware result: reverse ordering still fails

On split-Next implementation `78f6f06`, the user confirmed initial audible playback,
Pause shown and a steady connection. One Next produced no new audible audio;
Android stayed on the newly selected track and the connection remained steady.
Categorical records showed the reverse order from the earlier diagnostic attempt:
state-only PLAYING arrived while the previous item was already PLAYING and returned
without transport writes. About 24 ms later, the different-current-item update
arrived with playingState and position absent. The item load sent one acknowledged
Stop, successfully loaded the new track in STOPPED state, and completed without
a generation/selection/activation change during loading. No subsequent incoming
PLAYING, new URI or Play was present in the captured sequence. The narrow
selection-then-Play deferral was therefore not exercised by this attempt.

Read-only corroboration found upstream idle/no now-playing metadata, renderer
STOPPED, authentication/session present and the endpoint available. The image
remained healthy with zero restarts. A private offline replay against this image
using actual stock-handler protobuf reproduced PLAYING-then-item-update:
no-op Play of the previous item, then successful load-only Stop with the old URI
unchanged and no new URI/Play. A subsequent explicit PLAYING snapshot succeeded
locally; no additional user recovery test or agent-driven transport command was
issued during this diagnosis.

Manual Next acceptance remains **failed**. The correction and 135-test suite
covered one split ordering, not both live orderings. Further correction must
handle partial playing-state/item updates in both orders within the same active
Qobuz source, while preserving explicit Pause/Stop, newer item intent, source
takeover/disconnect and fresh-selection-plus-Play requirements. It must not add a
timed admission window, source-reclaiming retry or blanket auto-Play of selection
metadata. No further playback code, image update or live configuration change was
made in response to this result; any new candidate deployment needs approval.

```sh
docker build --build-arg PLAYBACK_PATH=upstream_first \
  --build-arg UPSTREAM_STATE_DIAGNOSTICS=1 \
  --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" \
  --label io.raumfeld.upstream-state-diagnostics=1 \
  -t raumfeld-connect:upstream-first-split-next-review .
```

`docker-compose.upstream-first.yml` is an image-only override with
`pull_policy: never`. It is a preparation artifact, not approval to run Compose
or update the live app. No registry publication is needed.

Before an approved hardware deployment:

1. Privately save the live parsed Compose configuration and exact image digest;
   retain its image and the known-working `raumfeld-connect:handoff-order-review`
   rollback archive/configuration. Do not save credentials or snapshots in Git.
2. Verify the candidate revision/digest, free audio-proxy port, unchanged single
   ungrouped room/CD config, mounts, credentials, host networking, `LAN_ADDRESS`,
   and empty `RAUMFELD_HOST`. Ensure no environment override defeats the embedded mode.
3. Change only the image. On TrueNAS pass the parsed Compose **object** through
   `app.update` / `custom_compose_config`, not a JSON string. Never run both
   receivers against the same discovery ports. Check health/auth/discovery;
   this is not verification of audible audio or scrobbling.
4. Roll back by restoring the exact saved pre-deployment configuration/image,
   or the separately retained known-working rollback configuration if requested.
   Do not remove volumes, original credentials/config or candidate token storage.
   Check health and require a new user selection after the restart. Neither
   deployment nor rollback should send agent-driven transport/zone commands.

## Acceptance

Offline: upstream connect semantics including repeated legitimate session IDs;
initial Play/seek, pause/resume, next/natural completion/reporting; deferred zone
creation from explicit Play only; unchanged/address-changed/missing endpoint;
source release and stale callbacks; no maintenance/shutdown Stop after Spotify;
no uncertain mutation retries or fallback volume setters. Use synthetic fixtures.

Hardware (after explicit deployment approval): audible initial play and steady
connection; pause/resume; small volume and knob/no-echo tests; manual next and
ordinary natural completion; real report responses and user-visible history;
continuous idle Spotify; both source handoffs; disappearance/rebinding. SOAP
acknowledgement, reported PLAYING, audio bytes, audible playback and visible
scrobbles are separate outcomes. The deployed hybrid's failures do not validate
or invalidate this distinct architecture. Automated reporting uses a synthetic
API: it does not establish real HTTP acknowledgement, history or Last.fm visibility.
# Partial-state comparative review and candidate (not deployed)

Reviewed both captured Android Next orders against unmodified pinned upstream
`91db990abeb486005a8315a6b1e722df776c4ba4` and deployed integration `78f6f06`,
using actual stock protobuf/command handler/player and the same deterministic fake
DLNA SOAP transport. Field absence is preserved; event gates reproduce the
pre-Stop ownership-read interleaving without message-pairing timing windows.

| Implementation | Item → Play at pre-Stop read | Play → item (state/position absent) |
| --- | --- | --- |
| Unmodified pinned upstream | New URI + Play | Stop/load only; no new URI/Play |
| Current deployed integration | New URI + Play | Stop/load only; no new URI/Play |
| Deployed integration with its ordering deferral removed | Cancelled before Stop; old item retained | Same upstream load-only defect |
| Unmodified newer upstream HEAD `10dedc9d2ac0ff2edcad729e22876d2c7f158c8e` | New URI + Play | Same load-only defect |
| Isolated player patch + simplified integration candidate | New URI + Play | New URI + Play |

The earlier selection → Play cancellation was introduced by our mid-command
generation checks; the reversed failure originates in upstream's treatment of
omitted playingState. These are distinct causes. Current upstream's manual-skip
fix `ab51375` (#33) handles queue-only feedback, not the reversed item/state delta.
No dependency upgrade is proposed; pin and stock sessions/queue/reporting remain.

The candidate separates requested mode from observed renderer state, merges
partial outstanding intent, and treats identical transport intent as one generation.
It removes both load deferrals and the committed-load generation exception.
Source/activation authority remains captured across awaits, and supersession is
checked before URI transmission. Explicit fresh selection clears inherited mode.
Node remains discovery/on-demand renderer creation only.

Isolated source backport and standalone upstream-facing tests are under
`upstream-patches/`; Docker applies only to hash-verified pinned source. No
contribution has been submitted. Synthetic coverage includes both orders,
startup/paused selections, explicit Pause/Stop during loading, rapid newer targets,
queue-only feedback, takeover, disconnect/reactivation, failed writes and terminal
source/topology conflict. Internal Stop does not erase admitted Play; paused or
revoked selection never invents it. After URI transmission begins, stock's locked
URI+Play transaction completes under unchanged source authority, then queued Pause
applies; this is explicitly tested, not claimed as instantaneous cancellation.

Pre-image regression results: 146 project Python tests plus four standalone
contribution tests passed, as did all six original pinned upstream serialization
tests. Image checks and hardware acceptance are recorded separately below.
The local Compose override names the prepared review candidate only; it does not
change the running deployment. Hardware verification remains false. No speaker
mutation or new deployment has been performed for this review. Next, Pause/resume,
natural advancement, Android volume and physical knob feedback must all be retested
on this same candidate after separate deployment approval. Reporting/scrobbling,
uninterrupted Spotify and outstanding handoff/rebinding acceptance remain separate.

## Prepared partial-state image checks

Implementation: `560ee29e19e242fb336281823aa2caa36d9bcafc`.
Local image: `raumfeld-connect:upstream-first-partial-state-review`, amd64,
ID `sha256:088c8afa865cf264b5b4be230aa8aaf070694ae8a77207d119c658fb67ae4c56`.
The exported rollback/test archive is retained privately with SHA-256
`eb0fdd43e2ea4887a7486b35bd930e2cf91f30c5e43c3347abdb4283e786fbd7`.
Diagnostic build marker/label is enabled; no runtime environment changes are needed.

The built candidate passed 147 project Python tests and four standalone upstream
contribution tests (151 total), including fail-closed patch installation. All six
original pinned upstream serialization tests passed with the patched source.
All 104 serial Node tests passed in the isolated Node 24 development-dependency
harness; the initial slim-runtime test attempt lacked test-only `ajv`, not a runtime
regression. Offline candidate supervisor/UI/health/storage/shutdown smoke passed.
Fresh replay against unmodified pinned upstream, unmodified newer HEAD and the
built candidate reconfirmed the table above with explicit method-entry event gates.
Tests use synthetic credentials/metadata/URLs; private stderr and image archives
remain outside Git. No registry publication or dependency update occurred.

Candidate is **prepared, not deployed; `hardwareVerified: false`**. Running image
remains `78f6f06` with failed Next acceptance. Separate approval is required before
deployment or agent-driven speaker-changing tests. After approval, establish audible
initial playback, test Next once and inspect categorical diagnostics, then repeat
Pause/resume, natural advancement, Android volume and physical knobs on this exact
image. Renderer PLAYING, SOAP acknowledgements and synthetic success do not establish
audibility, real reporting acknowledgement or account-visible scrobbles.

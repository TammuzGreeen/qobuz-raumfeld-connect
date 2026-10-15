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

## Approved partial-state deployment

User separately approved deployment and same-image control acceptance. Candidate
implementation `560ee29e19e242fb336281823aa2caa36d9bcafc` is now deployed as
`raumfeld-connect:upstream-first-partial-state-review`. The privately transferred
archive checksum matched; imported image ID is
`sha256:4baf7a4f6b3d480a00e8f8abe0f9711a8df3747cee2cbaa8368e0e01d528679f`
(distinct from the local OCI index ID above).

Deployment changed only the image/pull policy, retained a private rollback archive
and complete before-state, and completed the app update once without retry.
Verified healthy/authenticated, zero restarts, unchanged environment mapping,
host networking, mounts, original configuration/credential file hashes and optional
credential-cache state, same room configuration/identity/membership and available
virtual endpoint, unchanged unrelated running containers, diagnostics enabled and
no legacy receiver/service entrypoints. An initial process check expected a script
path; the actual exact argv pair is `-m`, `qobuz.upstream_app`, which passed the
corrected read-only check. No transport commands were sent by the agent; startup
diagnostics contained zero SOAP transmissions.

**Deployed, not hardware-accepted; `hardwareVerified: false`.** Initial audible
playback and Next remain pending user observation on this image, followed by
Pause/resume, natural advancement, Android volume and physical knob feedback.
Reporting/scrobbling and uninterrupted Spotify remain separate outstanding tests.

## First partial-state hardware Next result

On deployed implementation `560ee29`, user reported audio, connection and a
"pause check" before Next and again after Next. This establishes user-observed
audible playback and a steady connection through the manual Next attempt.
The Pause control check is recorded as reported, not as completed Pause/resume:
the captured sequence contains no explicit PAUSED update or SOAP Pause, and the
read-only status remained playing. Explicit audible stop/resume confirmation is
still requested.

Categorical diagnostics show a different-item update with playingState/position
absent (local event 9), followed by state-only PLAYING (event 10) during the load.
The selected-item transaction sent and acknowledged one Stop, one new URI and
one Play, completed in playing state, and the state-only Play then completed
without an extra transport write. Queue-only feedback during loading did not
supersede it. Selection/activation authority stayed unchanged. This hardware
attempt exercises selection → Play, not the reversed live order; reverse-order
coverage on this candidate remains synthetic. No metadata, device identities,
URLs, cloud IDs or private captures are committed.

Read-only check confirmed healthy/authenticated, zero restarts. An initial-event
Seek received definite 710 and was not acknowledged; this is separate from the
successful Next transaction and does not establish a fully error-free session.
State-report send completion is not cloud playback-report acknowledgement or an
account-visible scrobble. No speaker commands were sent by the agent.

Initial audible playback and this manual Next attempt pass. Explicit Pause/resume,
natural advancement, Android volume and physical knob feedback on this same image
remain pending; overall `hardwareVerified: false`. Reporting/scrobbling,
uninterrupted Spotify, handoffs and hardware rebinding remain separate outstanding
acceptance tests.

## Partial-state explicit Pause/resume confirmation

User subsequently confirmed pressing Play and Pause works with a steady connection
on the same deployed implementation `560ee29`. Explicit Pause/resume is therefore
recorded as user-observed passed, superseding the pending confirmation above.
Initial audible playback and manual Next remain passed for the observed attempt.
Natural advancement, Android volume and physical knob feedback still need same-image
acceptance. Overall `hardwareVerified: false`; reporting/scrobbling, uninterrupted
Spotify, handoffs and hardware rebinding remain separate outstanding tests. This
confirmation does not claim a new diagnostic capture or cloud acknowledgement.

## Partial-state natural advancement confirmation

Following the request to let a queued track finish without pressing Next, user
confirmed the queued track started normally, was audible, the connection stayed
steady and Android showed the Pause symbol on deployed implementation `560ee29`.
Natural queued advancement is recorded as user-observed passed on this same image.
Android volume and physical knob feedback remain pending. Overall
`hardwareVerified: false`; reporting/scrobbling, uninterrupted Spotify, handoffs,
exhausted-queue restart and hardware rebinding remain separate outstanding tests.
This confirmation does not establish new diagnostic or cloud-report acknowledgements.

## Partial-state volume confirmation

User confirmed volume changes work both in Android and physically, with a steady
connection and Android showing Pause, on deployed implementation `560ee29`.
App volume control and physical volume adjustment are recorded as user-observed
passed. The report does not explicitly confirm Android's volume indicator follows
the physical knob or that app changes never jump back; those feedback details
remain pending confirmation rather than inferred from successful loudness changes.

Same-image user-observed passes now include audible initial playback, manual Next,
explicit Pause/resume, natural queued advancement and app/physical volume changes.
Overall `hardwareVerified: false`. Reporting/scrobbling, uninterrupted Spotify,
source handoffs, exhausted-queue restart and hardware disappearance/rebinding
remain separate outstanding acceptance tests. No new speaker command, diagnostic
capture or cloud acknowledgement is claimed from this user confirmation.

## Partial-state volume feedback confirmation

Asked specifically whether Android's volume indicator follows the physical knob
and whether app volume changes stay put without jumping back, user answered yes
and confirmed steady volume changes on deployed implementation `560ee29`.
Both feedback details are now recorded as user-observed passed, superseding the
pending confirmation above. The requested same-image control checks have passed:
initial audible playback, manual Next, Pause/resume, natural queued advancement,
Android volume and physical knob feedback, with steady connection as reported.
Overall `hardwareVerified: false`; reporting/scrobbling, uninterrupted Spotify,
source handoffs, exhausted-queue restart and hardware disappearance/rebinding
remain outstanding. Reverse-order Next coverage on this image remains synthetic.

## Next priority: mid-track playback / seeking

User deferred scrobbling and requested mid-track playback be fixed next, followed
by Spotify → Qobuz → Spotify handoff acceptance. No handoff test or new deployment
has been performed as part of this request.

Read-only current-image diagnostics captured a state-only positive-position delta
while playing (local event 318). Two Seek transmissions followed, both rejected
with definite 710 (`unsupportedSeek=True`); upstream seek wrappers nevertheless
finished true. Source/activation authority did not change and the container
remained healthy with zero restarts. Pinned upstream's DLNA client uses REL_TIME.
The partial-state backport preserves playing mode, so the stock apply sequence
seeks once for position then again through its already-playing `_play_locked`
path. This redundant mutation must be addressed rather than used as a retry.

Read-only virtual-renderer SCPD advertises ABS_TIME, REL_TIME and TRACK_NR. The
PyRaumfeld reference implementation defaults to ABS_TIME. These are investigation
leads, not evidence ABS_TIME succeeds with the current upstream audio proxy.
No alternative Seek, transport mutation, fallback, regroup or source replacement
was attempted. Private captures and SCPD remain outside Git.

Correction planning must cover starting at a nonzero position, scrubbing while
playing and paused, explicit failure propagation without optimistic position
success, one intentional Seek per update, newer selection/Pause and source
takeover. Preserve upstream player/queue/reporting and all existing control passes;
no arbitrary pairing window or retry on uncertain mutations. A new candidate and
speaker-changing validation still require separate approval. Overall
`hardwareVerified: false`; seeking is now a known failed acceptance item.

## Working-preview publication checkpoint

User explicitly authorized preserving/publishing the current working state and a
GitHub prerelease before any further seeking investigation. Publication changes
only documentation and a sanitized standalone Compose/manifest; playback sources
remain the tested `560ee29` implementation. Thirty-two application/backport files
read from the running image match that revision. Running imported image ID/config
digest is `sha256:4baf7a4f6b3d480a00e8f8abe0f9711a8df3747cee2cbaa8368e0e01d528679f`;
the local OCI index ID differs as already recorded.

Fresh private runtime/Compose/storage-hash snapshots and an exact running-image
rollback archive were retained on the deployment host. The existing local tested
image archive checksum was verified too. Running container identity, image,
restart count, effective Compose and storage hashes stayed unchanged; no app
update, playback edit or speaker control occurred during this checkpoint.

README/current status/changelog reconcile the user's successful control tests
with the broader acceptance record. Audible playback, Android Next, Pause/resume,
natural advancement, app volume without jump-back and physical knobs with Android
feedback are hardware-verified in the recorded one-room/CD scope. Seeking is a
known failure. Reporting/scrobbling, Spotify continuity, both handoffs,
exhausted-queue restart and hardware rebinding are implemented but not yet
separately verified; no missing-feature claim is inferred. Unsupported preview
features are identified from explicit code restrictions.

Publication tag is `upstream-first-preview-2026-10-12` on the experiment branch,
not main. Source-only prerelease: no preview registry image or downloadable OCI
asset is published; older release images are not this tested preview. Credentials,
account/runtime data, real LAN/room/device identifiers, raw logs and private image
backups remain outside Git/GitHub. Standalone Compose was validated offline with
synthetic placeholders and no deployment. Private rollback is preserved, not
attached as a release asset. See `CHECKPOINT_STATUS.md` and `PREVIEW_SETUP.md`.

## Seeking investigation and prepared candidate after publication

The user requested seeking investigation after completing the working-preview
publication. Unmodified pinned upstream, unchanged current upstream HEAD and
current preview were replayed against the same deterministic 710-rejecting fake
DLNA transport with real protobuf field absence. Upstream discarded failed Seek
results and falsely advanced its position; combined position/PLAYING messages sent
two Seeks. The partial-intent backport additionally duplicates position-only scrub
while playing. Newer upstream does not resolve these defects.

Candidate `d58a5cd01b2e1c5cde46c4267eccaa71ad5b096b` prepares an isolated
acknowledgement/player backport, single-Seek intent, truthful failure/start-offset
reporting and declared Raumfeld ABS_TIME handling. No fallback probe/retry, custom
player/queue, proxy offset/transcoding or source-safety relaxation was introduced.
The legacy non-DLNA path retains post-start seek reconciliation; its existing
regressions pass. Read-only real descriptor/SCPD/action-list inspection supports
ABS_TIME as a hypothesis, not hardware proof. See `SEEK_REVIEW.md` for the comparison,
scope, candidate image identities, tests and required hardware plan.

Built local amd64 image `raumfeld-connect:upstream-first-seek-review` passed 170
Python tests (162 project + eight standalone upstream) and offline smoke; six
original upstream serialization and 104 serial Node tests also passed. Exported
archive and raw private investigation outputs remain outside Git. Candidate is
not deployed, not registry-published and not hardware-verified. The working
`560ee29` image and immutable `upstream-first-preview-2026-10-12` publication tag
are unchanged. Only read-only speaker diagnostics were performed; no mutating
speaker controls. Separate approval is required for candidate deployment/testing.

## Approved seeking-candidate deployment

User separately approved the controlled seeking-candidate deployment. Implementation
`d58a5cd01b2e1c5cde46c4267eccaa71ad5b096b` is now deployed as
`raumfeld-connect:upstream-first-seek-review`; imported ID/config digest is
`sha256:8855faf4fd553e9ee71124f32ac47154d52d3c8a1958ec8cf5295c9cdcc4081a`.
Private archive checksum matched and working preview `560ee29` was archived as
rollback with complete before-state. The app update changed only image/pull policy,
was executed once and was not retried.

Verified healthy/authenticated, zero restarts, original config/credential hashes
and optional cache state preserved, unchanged environment/mounts/host networking,
same room identity/configuration/membership/endpoint, unrelated running containers
preserved, diagnostics enabled, both installed backport hashes exact and upstream
launcher present without legacy receiver/service entrypoints. Zero startup SOAP
transmissions and zero agent speaker controls during deployment. The working
preview prerelease/tag is unchanged. `hardwareVerified: false`; initial audible
playback and a single Android scrub on this candidate await user observation.

## First seeking-candidate playing scrub acceptance

On the same deployed `d58a5cd` image, user confirmed initial audio, Android progress
and strong connection, then reported dragging into the middle works and connection
remains strong. Initial audible playback and this playing-track scrub are recorded
as user-observed passed. Diagnostics captured a position-only positive update while
playing (local event 9), one absolute-time Seek sent and acknowledged, no 710,
seek completion true and unchanged selection/activation authority. Healthy, zero
restarts. No agent controls were sent; raw captures stay private.

This is actual hardware evidence for the declared ABS_TIME correction on the
observed playing scrub, not merely the earlier mode hypothesis. Paused scrub/resume,
fresh nonzero start and affected controls/progression still need acceptance on this
candidate. Full `hardwareVerified: false`; source handoff testing follows seeking,
scrobbling remains deferred, and the published working-preview tag is unchanged.

## Paused seeking observation on current candidate

User reported Pause → selecting another point immediately started audible playback
without pressing Play; later Play did not harm audio and connection stayed steady.
Paused-scrub silence is not accepted. Live categorical sequence: acknowledged Pause
and player paused (event 76), position-only positive update (event 78), one
acknowledged ABS_TIME Seek, no Play during the scrub, player still internally
paused, then the user's later state-only PLAYING/acknowledged Play (event 80).
Source/activation authority did not change. This points to renderer Seek-induced
resume, rather than app PLAYING intent or a wrapper-generated Play. Exact physical
state before the later button press was not independently read.

A network-disabled replay whose renderer resumes on accepted Seek reproduced
physical PLAYING / player PAUSED with Seek as the sole mutation for both stock
DLNA and room backend. Existing paused-scrub tests did not model that side effect.
No new playback implementation/deployment or agent mutating control was performed.
The playing-scrub pass stands; full `hardwareVerified: false`, paused silence is
failed/unresolved, and desired paused-scrub behavior needs confirmation before
correction selection. Working-preview tag/rollback remain preserved; handoff tests
are deferred while this seeking behavior is unresolved.

## Prepared silent paused-scrub candidate — not deployed

User confirmed scrubbing while paused must stay silent until explicit Play.
Candidate `361e46eec4bc745530cc70bfc6ce215410f1bc2a` adds a third isolated,
hash-checked upstream player patch. Room-only capability retains the desired paused
cursor after read-only ownership validation; no transport mutation is sent. Desired
UI position is distinct from applied renderer position. Explicit Play resumes then
applies the latest target once, including zero. Failure does not fake position
success; failed resume or invalidation discards the pending target. Stop, track/
queue-occurrence changes, fresh selection, source loss, disconnect and endpoint
retirement clear it; superseded ownership reads cannot retain old intent.

Auto-resume fake-renderer coverage now tests actual silence before Play, not merely
cached player PAUSED. Built local image
`raumfeld-connect:upstream-first-paused-seek-review` passed 179 Python tests
(169 project + ten standalone), six original upstream serialization tests and
offline supervisor/UI/health/storage/shutdown smoke. Image/archive identities and
acceptance scope are in `SEEK_REVIEW.md`; private archive/metadata stay outside Git.
No Node implementation changed and no speaker mutation/deployment was performed.
Running `d58a5cd` remains unchanged, including its known paused-scrub auto-resume
failure; new candidate requires separate deployment approval and user hardware
silence/resume-position acceptance. Working-preview tag/rollback stay unchanged.

## Approved silent paused-scrub deployment

User separately approved deploying `361e46eec4bc745530cc70bfc6ce215410f1bc2a`.
Running image is now `raumfeld-connect:upstream-first-paused-seek-review`; imported
ID/config digest is
`sha256:ecb5bbb89c611089530386ca4159cb019ba02a2ab7355c675ace08a7c2083967`.
Archive checksum and installed backport hashes matched; paused Seek deferral is
enabled. Retained private fresh before-state/Compose and previous `d58a5cd` image
archive alongside original working-preview rollback. One image/pull-policy-only
update, no retry. Healthy/authenticated, zero restarts, config/credentials/optional
cache state, environment/mounts/host networking, room configuration/identity/
membership/endpoint and unrelated containers preserved. Upstream launcher present,
legacy entrypoints absent, diagnostics enabled and zero startup SOAP transmissions.
No agent speaker controls were sent. User hardware paused silence and explicit
resume-position test is pending; full `hardwareVerified: false`. Published preview
tag/rollback unchanged; source handoffs follow seeking, scrobbling remains deferred.

## Silent paused scrub and explicit resume acceptance on current image

User completed the requested initial playback, Pause, paused progress-bar change
and explicit Play steps and confirmed everything works on deployed `361e46e`.
Record initial audible playback, paused-scrub silence, audible resume from the
chosen point and steady connection as passed for this attempt. Capture confirms
acknowledged Pause (event 58), position-only positive delta while paused (event 60)
without any transport transmission, then explicit PLAYING (event 62), acknowledged
Play followed by exactly one acknowledged ABS_TIME Seek. No 710 or selection/
activation authority change. Deferred seek true is pending-intent acceptance, not
a physical Seek acknowledgement. Audible evidence comes from the user.

Healthy, zero restarts; read-only evidence collection, no agent controls or deployment
change. Private raw captures stay outside Git. Both source handoffs are next as
user-driven acceptance; fresh nonzero starts and current-image control/playing-scrub
regression remain separately outstanding. Full `hardwareVerified: false`, scrobbling
deferred, preserved preview/tag/rollback unchanged.

## Spotify → Qobuz handoff failure on current candidate — 2026-10-14

User let Spotify play, explicitly selected the room in Qobuz and pressed Play;
Qobuz produced no phone/speaker audio and lost app connection shortly afterward.
Read-only diagnostics show different-track paused state loaded (event 1183), then
explicit PLAYING (event 1184) failed during on-demand companion renderer creation:
`create_for_play` received a non-200 response, raised `companion_unavailable`, and
player entered ERROR before Qobuz URI/Play transmission. No Seek failure or stale
command rejection in this attempt. Exact response status/body was not captured by
the exception. Current room is unassigned with no virtual-renderer endpoint.

Node's non-loopback dynamic speaker-service ECONNREFUSED occurred approximately
33 seconds before explicit Play; possible relation, not proven causal attribution.
Healthy/authenticated, zero restarts; Android connection loss remains user-observed,
not contradicted by backend health. No agent transport/provisioning mutations,
retry or deployment change. Private raw logs retained. Spotify continuation outcome
awaits clarification; reverse handoff not tested. Current paused-seek pass remains
recorded; Spotify → Qobuz acceptance failed, full `hardwareVerified: false`,
scrobbling deferred, preview/tag/rollback preserved. See `SEEK_REVIEW.md`.

## Renderer-request boundary trace and undeployed diagnostic correction

User confirmed Spotify continued playing throughout the failed takeover, then
requested exact renderer-error attribution, background/explicit-demand separation
and preservation of working Qobuz playback. Recorded subscription ECONNREFUSED
entries belong to background maintenance, not the explicit creation request. The
old POST's exact status/body was discarded across Python/companion/SDK boundaries,
so our check versus Raumfeld response cannot be determined retrospectively. No
mutating request was repeated to recover missing evidence. Spotify continuation
is non-interruption evidence, not handoff success.

Candidate `29c020decab022d9526a25b699be1831bfc26807` repairs categorical error
fidelity with bounded Python response parsing, explicit-only companion traces and
a hash-checked pinned SDK creation-error adaptation. Local preflight/uncertainty
block, remote HTTP status, network code, deadline and post-ack topology denial are
distinct. No retry, source guard relaxation, regroup or player/seek modification.
The SDK change is scoped to creation error paths; background subscriptions and
successful-request behavior are unchanged. Installed player hash matches `361e46e`.

Built local image `raumfeld-connect:upstream-first-renderer-boundary-review` passed
182 Python tests, 14 packaged Node boundary tests and offline smoke. Full serial
Node suite passed 110 tests and six original upstream serialization tests passed.
Private archive retained; image identities and limitations in
`RENDERER_BOUNDARY_REVIEW.md`. Not deployed or hardware-verified; current deployment
unchanged. Error visibility is repaired, underlying handoff cause/fix not proven.
Separate approval required for diagnostic deployment/new user-requested takeover.

## Approved renderer-boundary diagnostic deployment

User approved deploying `29c020decab022d9526a25b699be1831bfc26807` for one fresh
explicit user takeover capture. Imported ID/config digest:
`sha256:72a17422fa93f74943c397d6ac2138b66d3348c40415cb9995fb75a12f831625`.
Checksum verified; fresh private before-state/Compose and `361e46e` rollback archive
retained with original preview rollback. Image/pull-policy-only app update, once,
no retry. Healthy/authenticated, zero restarts, unchanged credentials/config/optional
cache state/environment/mounts/host network, same room/membership and unrelated
containers. Player byte hash unchanged, exact SDK error adaptation verified.
No startup transport/provisioning transmissions, no agent speaker controls.
User instructed to perform one Spotify-playing → explicit Qobuz selection + Play.
Outcome pending; handoff remains unaccepted, `hardwareVerified: false`, preview
tag unchanged and scrobbling deferred.

## Exact renderer-boundary defect identified on diagnostic image

User reported Qobuz still failed while Spotify continued and proposed manual
Spotify Pause before Qobuz as acceptable. Capture on `29c020d`: explicit demand
reached library_call, failed as library_unknown; subsequent demands hit our local
uncertainty block. Actual pinned SDK inspected offline: `kernel.getManager` is
undefined; init exposes `kernel.managerDisposer.zoneManager.connectRoomToZone`.
Our companion used the nonexistent getManager method and therefore failed before
any Raumfeld creation request. Earlier mocks invented that API and missed the bug.
This is our integration failure, not evidence that Spotify/firmware blocked it.
Background subscription refusals remain distinct.

Prepared correction uses the real manager path and validates it before setting
mutation uncertainty; actual attempts remain single-call and uncertain failures
remain terminal. Real pinned SDK-shape regression added, no fallback API or player/
seek changes. Manual Spotify pause is accepted as a future test policy, not proven
necessary. User advised not to retry the current defective/blocked deployment.
No new deployment or agent speaker controls; separate correction approval needed.

## Approved corrected-manager deployment

User explicitly approved deploying `d3c3a78870bce634e9224a428b7d5e177da2a83e`.
Imported ID/config digest:
`sha256:d0ed8401c46e8dd7bc487a83a95ded2164428c1107015c4680d7199595126df3`.
Private checksum matched; before-state/Compose/storage hashes and prior diagnostic
image rollback retained with original working preview. One image/pull-policy-only
update, no retry. Healthy/authenticated, zero restarts, configuration/credentials/
optional cache/environment/mounts/host network, room/membership and unrelated
containers preserved. Player unchanged; SDK error adaptation hash and corrected
manager path verified. Actual upstream launcher, diagnostics, no legacy entrypoints,
no startup transport/provisioning transmissions or agent speaker controls.
Fresh user Pause Spotify → explicit Qobuz selection + Play test pending. Manual
pause accepted policy, not a proven requirement. Full `hardwareVerified: false`,
immutable preview untouched and scrobbling deferred.

## Corrected-manager attempt — post-creation authority abort

User reported Qobuz disconnects immediately and plays on the phone after the
manual Spotify-pause handoff test. On `d3c3a78`, explicit creation now reaches
library_call and Raumfeld acknowledgement; read-only catalog shows assigned room
and renderer endpoint with unchanged identity/physical membership. Prior API defect
is resolved for the observed creation, not a successful handoff.

Track loaded (event 1); explicit Play entered loading (event 2). After renderer
acknowledgement, selection/activation authority became inactive and start failed
with stale_upstream_command before Qobuz URI/Play. Healthy, zero restarts. Spotify
evidence counter differs from before deployment; unconditional version-change
release is a candidate trigger, but precise increment/source freshness is not yet
proven. Need distinguish fresh takeover from existing paused-source re-observation;
no blanket source-guard relaxation. No agent controls/deployment change, no retry
or drop/recreate. Phone fallback user-observed; full hardwareVerified false,
reverse handoff untested, scrobbling deferred. See RENDERER_BOUNDARY_REVIEW.md.

## All-room source-evidence correction prepared with explicit authorization

User authorized implementation, all supported rooms, test deployment and GitHub
push without main merge/tag replacement. Read-only inventory found six ungrouped
logical rooms. Private topology/identity mapping retained, no household identifiers
in public docs/config examples. Candidate
`1df99d269ecd1d44e2f6759e2ae5227ad20a02a3` replaces cached-event Spotify counter
inflation with deduplicated complete-read transitions and pre-URI fresh physical
source confirmation. Initial paused-source/virtual projections do not release
Qobuz; genuine fresh subsequent Spotify still cancels. Physical markers after start
are correlated with actual virtual URI. Every release path retains source safety
and categorical tracing through session deactivation. No player/seek patch change.

Actual Node companion → upstream Speaker replay reproduces the old failure chain
and passes corrected code plus later genuine takeover. Per-room factory/Ws binding,
queue/session/backend identity and operation contexts isolated; port collisions
rejected. Six-room offline smoke sends no creation/transport commands. Built image
passed 188 Python tests, 117 serial Node, 34 packaged Node boundary tests and six
original upstream serialization tests. See ALL_ROOMS_HANDOFF_REVIEW.md for source/
image identity, evidence limits and pending user-operated per-room hardware plan.

## Authorized all-room candidate deployment and readiness verification

Deployed `1df99d269ecd1d44e2f6759e2ae5227ad20a02a3` as
`raumfeld-connect:upstream-first-all-rooms-review`; imported ID/config digest
`sha256:e11255790134dc6018265cb308df3e60b4dd7274a0ace193a25973bdb0efc9d9`.
Private archive checksum matched. Fresh private before-state/Compose/full persistent
storage backup and current `d3c3a78` rollback archive retained with original preview.
User-authorized private config now includes all six ungrouped supported logical
rooms, original selected-room identity/name/ports unchanged. New ports verified
free. One image/pull-policy update, no retry; credentials/cache/env/mounts/host network,
logical room membership and unrelated apps preserved. Player/SDK hashes verified.

Healthy/authenticated, zero restarts, six speaker configs/mDNS registrations,
all six advertisements independently observed over LAN. Each physical renderer
discovered, discovery HTTP/audio listener available and fresh source/transport
read successful. No grouped/offline/unsupported rooms currently present. Zero
startup zone-creation/transport commands, unchanged assigned/unassigned topology;
verification uses read-only source requests, no agent speaker-changing controls.
Source/test/config-example/docs privacy-reviewed and pushed with remote verification;
main/tag untouched. Every candidate per-room audible/control/handoff hardware check
remains pending. See ALL_ROOMS_HANDOFF_REVIEW.md for plan and private rollback steps.

## Post-beta correction candidate — not deployed

After user all-room beta acceptance and immutable beta publication, user requested
fixes for automatic nonzero start after Spotify and the original room's apparent
release suffix. Read-only diagnostics establish PAUSED positive-position snapshot
while STOPPED, ignored Seek, then position-less PLAYING start at zero. Actual
pinned handler/player/DLNA replay reproduces the missing start Seek on beta code.
Fourth hash-checked player patch retains desired stopped/load-only offsets until
explicit Play, clearing on source/session/selection/endpoint/track/occurrence/Stop
invalidation. Existing one acknowledged start Seek applies it once; no early
position success, arbitrary timing, transport retry or authority bypass.

Naming cause is old setup's last-six-room-ID suffix, not a release tag. Normalize
only that exact generated suffix in runtime config; stable IDs/UUIDs/ports, custom
names, stored config and credentials stay unchanged. Future setup omits ID suffix.
Private household names/IDs/logs remain excluded from publication.

Implementation `1917f07001545ba01bb655a496af29eafaa4f9ba`, local candidate
`raumfeld-connect:upstream-first-beta-fixes-review`. 200 Python, 117 serial Node,
34 packaged Node and six original upstream serialization tests plus six-room
offline startup/label/storage smoke pass. See POST_BETA_FIX_REVIEW.md for hashes,
evidence and candidate acceptance plan. Running beta/rollback/tag/main unchanged;
correction deployment and audible nonzero-start acceptance await separate approval.

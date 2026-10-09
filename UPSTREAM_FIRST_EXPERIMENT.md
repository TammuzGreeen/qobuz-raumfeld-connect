# Upstream-first one-room experiment

## Status and scope

This branch is a separate upstream-first candidate, **not deployed or
hardware-accepted**. Build with `PLAYBACK_PATH=upstream_first` to activate the new
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

Read-only deployment preflight confirmed the live image was unchanged/running,
host networking, automatic host discovery, one-room/CD configuration, a free
candidate audio-proxy port, `pull_policy: never`, and no Compose override of the
embedded playback mode. No app update, image import on the live host, speaker
commands or runtime storage changes occurred.

**Ready for separately approved deployment and hardware testing**, not a claim
of actual Android connection stability, audible playback, hardware volume/knob
behavior, uninterrupted Spotify, real reporting acknowledgement or scrobbling.
The first checkpoint `2c1b5a8` was pushed to the experiment branch and its remote
commit verified before implementation continued. Image archives/private preflight
data are not part of the published source checkpoint.

```sh
docker build --build-arg PLAYBACK_PATH=upstream_first \
  --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" \
  -t raumfeld-connect:upstream-first-one-room-review .
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

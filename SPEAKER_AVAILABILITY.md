# Speaker availability: diagnosis and review deployment

## Distinguish source, freshness and advertisement

Qobuz login does not establish renderer freshness. The observer discovers the
Raumfeld host, refreshes `/getZones`, and tracks physical and virtual renderer
discovery/removal events. A room requires a fresh observation for every physical
renderer and, if assigned, its zone renderer. Topology and observations expire
after 30 seconds. Multi-room zones are excluded independently of source.

Spotify events retain protective evidence but cannot renew complete observation
freshness. Enabling a fresh, single room advertises it; it does not stop or switch
Spotify. Only explicit Qobuz app selection followed by playback permits handoff.

Some physical Raumfeld firmware exposes no GetMediaInfo action. GetPositionInfo
returns time fields, not a source URI; reads specify the declared instance 0.
Requiring all
three original reads therefore prevented any complete observation. The supported
read-only QueryStateVariable(LastChange) response supplies a new AVTransportURI
snapshot. This fallback is used only for ENOACTION; other failures remain closed.
Neither cached event data nor transport state alone establishes source freshness.

Other reasons for zero advertisements include missing renderer discovery events,
failed/expired topology reads, missing/stale zone observations, grouped zones,
no selected rooms, absent login/LAN configuration, or receiver startup failure.
LAN_ADDRESS must be the Docker host's speaker-facing, locally assigned IPv4
address. A syntactically valid but non-local address is not a usable receiver
address. Some kernels even permit non-local binds, so bind success alone is not
proof; setup checks actual interface assignment.

## Read-only diagnostics

Verify the actual container image, digest and OCI revision before interpreting
status. Host networking does not prove that LAN_ADDRESS is correctly configured.
Never inspect or share the full environment, credentials, raw signed audio URLs,
or unredacted upstream logs. Do not assume local Docker targets TrueNAS.

Authenticated `/api/status` includes topology timestamps, renderer observations,
per-room `unavailableReasons`, bounded `observationErrors`, and `receiverErrors`.
Compare two samples more than 30 seconds apart. Join physical `rendererIds` and
`zoneId` to renderer observations. `observedAt: 0` means events supplied protective
source evidence but no complete read succeeded. Errors include the failing action,
bounded code and timestamp. IDs/names/addresses must be redacted before sharing.
Receiver failures are independent of login and do not discard other successful
receiver advertisements. All failures remain visible in setup.

Advertisement, an accepted app handshake, cloud connection, cloud activation and
speaker ownership are separate stages. `receiverConnections` reports only stage
codes and booleans; `connectEvents` keeps at most 50 recognized event codes and
numeric cloud/close codes. Raw upstream messages, JWTs, session IDs, endpoints,
account details and exception text are never returned or forwarded to logs.
These diagnostics do not create selections, renew permissions or replay requests.
Per-receiver counters show recognized incoming message types; protocol errors
retain only numeric codes. Playback diagnostics distinguish track/stream loading
failure from a guarded Node control failure without retaining track identifiers,
metadata, signed URLs or cloud error text.

Node state rooms expose `lastOwnershipLoss`: a bounded latest reason, timestamp,
lease phase, and optional physical/virtual renderer role and URI category. No
raw URI, renderer ID or token is retained in that entry. Subsequent release and
rejected retries preserve the original loss; a new accepted selection clears it.
Command failures include only an allowlisted action and error code, plus a
three-digit UPnP fault code when supplied by the client. The local command deadline
is distinguished from network timeouts. Fault text and command arguments are never
retained, and uncertain commands are never retried.
These diagnostics do not relax source guards, extend leases or bypass replay
protection. They distinguish source changes, topology loss, expiry and uncertain
commands before another hardware test is attempted.

The initial explicit Play allows up to four seconds of additional read-only
confirmation after loading the relay URI for the virtual renderer to report the
expected URI and physical renderers to stop reporting the selected Spotify source.
It does not repeat the load or extend the selection deadline. Every round requires
fresh room evidence; only the selected baseline source is tolerated while waiting.
New unexpected source evidence still revokes the lease. Missing target confirmation
or persistent Spotify fails closed without Play or Stop. Polling alone never
enters this handoff path.

Renderer commands use the pinned client's supported `callAction` API rather than
convenience methods that omit declared arguments. Transport and volume commands
send `InstanceID: 0`; Play also sends `Speed: '1'`. This corrects the SOAP contract
without changing dependency internals or retrying uncertain mutations. Actual
outgoing command bodies are covered against a local fake HTTP renderer. Whether
the omitted arguments caused hardware connection resets requires a new explicit
hardware test; a reset alone does not establish that cause.

A definite Seek UPnP fault 710 is returned as not applied, without revoking the
lease or retrying. All uncertain failures and other SOAP faults remain fatal.
The backend reports the seek as unsupported. An initial phone-position seek that
fails after successful Play is handled by a narrow player adapter: ownership is
rechecked and fresh renderer position is reported, not the requested position.
Interactive rejected seeks remain unsuccessful. No source/freshness checks are
bypassed, and seeking support is not claimed for a renderer that rejects it.

Upstream discovery stores handshake tokens before the receiver validates replay.
When a rejected handshake arrives after ownership release, the receiver clears
that reinstalled session so discovery does not advertise a dead session as current.
The seen-session guard is not cleared and no selection or control command is sent.
A duplicate handshake does not clear a still-valid selected backend. The LAN HTTP
adapter awaits admission and returns 409 for a rejected selection instead of
claiming success. Tokens are installed only by successful receiver composition;
errors return fixed codes, never exception text or credentials. This change alone
does not guarantee Android will generate a different session ID on its next
explicit selection.

The forwarding candidate recognizes only HTTP from the discovered Raumfeld
host on a dynamic port, with three distinct exact path identifiers in the observed
zone / room / physical-renderer order, a bounded stream-name segment and a single
numeric `punch` parameter. It rejects credentials, fragments, ambiguous paths and
unrecognized query parameters. The room must remain
fresh and ungrouped, membership and lease must be unchanged, and all observed
virtual source URIs must match the expected Qobuz relay. Spotify still revokes
immediately. Synthetic regressions verify these constraints. Read-only hardware
inspection matched the structural rule while the virtual renderer retained a
fresh Qobuz relay URI. Continued ownership and controls with this rule remain
unverified until an approved deployment and explicit app playback test.

Qobuz login and metadata request authentication must also be distinguished. The
pinned client's context manager opens a persistent HTTP session without a user
token, while login uses a separate session and does not update those headers.
Setup follows upstream app.py by not entering the context manager: each metadata
request uses an authenticated temporary session with the current token, including
after refresh. Cleanup/logout calls remain safe. With the old persistent session,
metadata requests can return 401 despite successful login and cloud activation;
upstream treats missing metadata as unavailable tracks and skips them before any
speaker Play command. Diagnostic HTTP events retain only status codes.

## Candidate deployment — requires approval first

No release image for this change has been published. Do not reuse the released
0.2.0 tag for changed code. After approval, build this branch as a distinct local
review image on a machine with the same architecture as TrueNAS:

```sh
git switch fix/speaker-availability
docker build --label org.opencontainers.image.revision=working-tree-review \
  -t raumfeld-connect:speaker-availability-review .
docker save -o /tmp/raumfeld-connect-speaker-availability-review.tar \
  raumfeld-connect:speaker-availability-review
```

Transfer that image archive to TrueNAS using your authorized access method, then
load it there with `docker load -i <archive-path>`. In the existing TrueNAS Custom
App configuration, replace only the image with
`raumfeld-connect:speaker-availability-review` and use the existing locally loaded
image (do not require pulling it from a registry). Correct LAN_ADDRESS to the
TrueNAS interface that speakers can reach. Preserve host networking, the setup
token, other configuration and **the existing persistent mount at /data**.
Recreate only this application's container; do not delete/reinitialize the app
storage or touch other applications. Verify the new image ID/revision label and
health afterward. Record the prior image digest for rollback using the same /data.
These instructions assume compatible architecture and support for local images;
otherwise prepare an approved private review image, not a public release.

## Hardware acceptance sequence

1. In Teufel, choose one already ungrouped room. Save only that room in setup,
   initially at CD-quality FLAC (quality 6).
2. Confirm topology and its required observations keep renewing for at least a
   minute. The room should be fresh and exactly one receiver advertised. An
   unassigned room needs no virtual renderer until explicit first playback.
3. Play Spotify there first. Start/restart this app or save its selected room.
   Verify Spotify continues; advertisement alone must not change its source.
4. Explicitly select the advertised target in Qobuz and start a CD-quality track.
   Check the intended handoff, audible playback, metadata, position and ownership.
   Creation of a missing single-room zone is allowed only at this explicit step.
5. Check pause/resume, seek, volume and track advance. Verify physical and newly
   created virtual observations remain fresh. Capture only redacted diagnostics
   if loading, URI confirmation or virtual observation fails; do not force Stop,
   retry/regroup automatically, or weaken freshness to get past a failure.
6. Select Spotify again. Qobuz must release ownership; Qobuz updates/reconnects
   must not reclaim it. A new explicit selection is required for another handoff.
7. Verify stale/offline state removes advertisement/control without sending Stop
   to Spotify. Restore connectivity and confirm observation recovery is read-only.

Read-only source queries on physical hardware do not prove audio playback,
virtual renderer behavior, source handoff, seeking or queue advancement. Those
require the above hardware tests after an approved deployment.

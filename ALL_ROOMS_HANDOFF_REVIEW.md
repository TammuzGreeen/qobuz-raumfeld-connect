# All-room upstream-first handoff test

This is later experimental work, not replacement of the preserved preview tag.
No account scrobbling acceptance is implied; overall `hardwareVerified: false`.
Household IDs/names, topology/source snapshots, credentials and deployment config
remain private. Public room aliases below are config-order correlation only.

## Root cause: evidence and limits

Confirmed on hardware: fixed manager API creates the renderer; afterward authority
was revoked before Qobuz URI/Play. Spotify was paused throughout. Earlier SDK
subscription refusals were background maintenance, not the explicit request.

Confirmed by identical synthetic replay with real Node companion, upstream Speaker,
player/handler and fake SOAP boundary: reviewed old companion increments its Spotify
counter for cached nativeSpotify notifications and a newly discovered virtual
renderer's projection of the same paused Spotify source. Refresh interprets that
as takeover, calls release_source, invalidates player work and leaves the cloud
session. Start then fails stale_upstream_command before URI. Corrected replay
completes URI/Play/audio-proxy delivery and still releases on a later genuine
Spotify transition. Both physical/virtual notification orders and duplicate
notifications are covered. This proves the defect and matches the hardware failure;
the exact historical live increment was not recorded, so that last attribution
remains inference until new categorical hardware diagnostics confirm it.

Other revocation paths audited and retained: membership change, renderer identity
replacement, mismatching virtual URI (including post-URI loading), failed/uncertain
SOAP mutation, mismatching volume readback, upstream activation/deactivation,
disconnect and stale room/selection/endpoint callbacks. Diagnostics now correlate
room, release reason, observation origin/read type, cached/fresh status, source/
transport category, current selection pending and explicit creation outstanding,
through intent invalidation, activation changes and cloud-session release.

## Correction and source safety

Cached state/URI notifications are hints only, never fresh takeover proof. Complete
reads establish a per-device baseline; equivalent source/transport observations and
initial virtual projection do not increment. Fresh Spotify source replacement or
resume transitions increment room-local evidence; equivalent transitions deduplicate.
No timestamp pairing window, periodic counter reset or blanket Spotify disable.

Immediately before URI replacement, explicit demand obtains current physical
source/transport reads, rejects superseded or unavailable results, refreshes room
evidence and checks captured authority. This read-only source-snapshot endpoint
never creates zones or sends playback commands. QueryLastChange reads are labeled
separately from GetMediaInfo; event receipt never counts as a complete read. After
Qobuz starts, physical Spotify-marker evidence is corroborated against a fresh
virtual URI before classifying it as takeover. Changed virtual URI still releases.
New genuine Spotify during loading cancels pending work; no automatic reclamation.

The existing three player/seek patches are unchanged. Stock upstream still owns
sessions, per-speaker queues/players/reporting and audio proxy delivery. Node only
observes source/discovery evidence and creates a renderer on explicit demand.
Mutation failures remain terminal; no retries, regroup, drop/recreate, fallback
setter, leases or replay registry. Missing/uncertain fresh evidence fails closed.

## Room isolation and inventory

The previous single captured room/shared active backend is replaced with an exact
configuration-keyed registry. Each WsManager captures its own backend permanently;
each upstream Speaker owns its queue/player, session, diagnostics, endpoint and
source state. Operation capture includes backend-instance identity, so equal
numeric generations in different rooms cannot confer authority. Old managers
cannot inherit a replacement backend. Stable UUIDs remain derived from logical
room IDs. All discovery/audio ports are checked for collisions including local UI/
companion ports. Existing room 01 identity/name/ports are retained.

Read-only topology inventory found six ungrouped logical rooms, each with one
physical-renderer identity. Logical stereo pairs, if represented by that identity,
are not split. No grouped or empty rooms were present. Physical online/discovery
status is independently reported by the new read-only inventory endpoint rather
than inferred from topology alone. Offline supported rooms remain configured but
are labeled unavailable; grouped/missing-renderer rooms would be listed with their
exact limitation and not silently split or regrouped.

| Alias | Previously selected | Discovery port | Audio port | Hardware on candidate |
| --- | --- | --- | --- | --- |
| room_01 | Yes | 8790 | 8890 | Pending |
| room_02 | No | 8791 | 8891 | Pending |
| room_03 | No | 8792 | 8892 | Pending |
| room_04 | No | 8793 | 8893 | Pending |
| room_05 | No | 8794 | 8894 | Pending |
| room_06 | No | 8795 | 8895 | Pending |

Real configuration and alias mapping are private. `examples/all-rooms-test.json`
contains placeholders only. All-room availability does not authorize simultaneous
household playback. Startup/poll/reconnect cannot create every zone; zones are
activated only on explicit Play. Unsupported grouped topology remains immutable.

## Validation and hardware plan

Regressions exercise actual pinned SDK interfaces, paused Spotify projection,
duplicate/both observation orders, fresh subsequent Spotify in loading and after
start, virtual-URI corroboration, stale callbacks/reselection, room A evidence
while room B remains active, concurrent upstream Speaker discovery, per-room
sessions/queues/managers, endpoint rebinding and port collision refusal. Existing
Next, seek, pause/resume, volume and guarded-path suites remain required. Six-room
offline supervisor smoke verifies unchanged storage and no startup transport/
creation commands. Synthetic audio bytes are not proof of real audible playback.

After authorized deployment, user-operated tests start with room_01, one room at a
time: paused Spotify → fresh explicit Qobuz selection + Play; active Spotify case
separately; Qobuz → Spotify with no reclamation; Spotify continuing in another
room while this room starts Qobuz. Then audible playback, Next/natural progression,
pause/resume, playing/paused seek, app volume/physical-knob feedback, reconnection
and practical endpoint rebinding. Repeat applicable cases on remaining supported
rooms. Label every unfinished observation pending, not inferred from SOAP/PLAYING.

## Rollback

Before deployment retain private effective Compose, complete persistent-volume
backup and both current/working-preview image archives. Stop the test app before
restoring its saved config.json (only room selection changes); preserve credentials
and other persistent state. Restore saved Compose with the matching saved image
using the supported app.update dictionary operation once; do not force-pull or
blindly retry uncertain transport/provisioning requests. Verify auth, original room
identity, networking and unrelated apps. The image's pinned player hashes distinguish
the current seeking-capable rollback from the older published preview. No merge to
main or existing release-tag replacement is part of this test.

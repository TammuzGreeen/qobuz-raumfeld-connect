# Upstream-first beta — 2026-10-15

Tag: `upstream-first-beta-2026-10-15` (GitHub prerelease).
Playback implementation: `1df99d269ecd1d44e2f6759e2ae5227ad20a02a3`.
This publication adds acceptance documentation, not new playback code or deployment.
Existing preview tags and main remain unchanged. Source-only release: no registry
image or downloadable image asset is published.

## User-observed acceptance on the deployed candidate

- Original failing room: paused Spotify → Qobuz, audible music and steady connection.
- Qobuz → Spotify without pausing Qobuz: Spotify audible, steady Spotify connection.
- Actively playing Spotify → Qobuz without pausing Spotify: Qobuz audible, steady connection.
- Next, pause/resume, playing/paused seeking, app volume and speaker volume work.
- Playback in different rooms works; user subsequently reports all rooms work for now.

The all-room report is general acceptance, not a separately captured full handoff/
control matrix for each room. No SOAP acknowledgement or state report substitutes
for these user observations. Six supported logical rooms were discovered and
advertised, each with successful fresh source reads. No grouping/stereo splitting
or startup transport/zone-creation commands. See ALL_ROOMS_HANDOFF_REVIEW.md for
architecture, source-evidence correction, deployment readiness and test provenance.

## Known bug: post-Spotify nonzero start restarts at beginning

User reports that after a Spotify handoff, Qobuz cannot automatically start in the
middle of the track: playback begins at zero instead. Manual seeking afterward
works. This is distinct from the already tested playing/paused scrub behavior.
Root cause is not yet confirmed; do not call this fixed by the source-evidence
correction or infer it from successful Seek acknowledgements.

Correction must preserve explicit-selection/source authority and room isolation.
Next investigation: reproduce with the pinned actual upstream handler/player;
trace requested nonzero position across fresh session selection, merged intents,
load/Play and acknowledged seek application. Cover position-before-Play and
Play-before-position orders, zero targets, stale prior-session callbacks and both
Spotify handoff directions. Confirm automatic audible nonzero start on a separately
approved candidate; do not infer it from subsequent manual seek success.

## Remaining limitations

Beta, not full hardware acceptance (`hardwareVerified: false`). Reconnection and
endpoint retirement/rebinding, detailed remaining-room matrices and automatic
nonzero starts remain outstanding. Reporting/account history/Last.fm scrobbling
remain deferred. Independent rooms are supported, not arbitrary grouped playback.

## Exact deployed image and rollback

Tag: `raumfeld-connect:upstream-first-all-rooms-review`.
Imported Docker image ID/config digest:
`sha256:e11255790134dc6018265cb308df3e60b4dd7274a0ace193a25973bdb0efc9d9`.
Local OCI index:
`sha256:ab7e774653019d63aa5f372b4e78a0e6d45e4fd8b6e33aa434ac8eed6a0fbbcf`.
Installed player SHA-256:
`7bc008e7b3251456c4c55fb2f8bd7ab45a0b5b44fce243f64e05b4b0ee598b91`.

Publication does not alter the deployment, credentials or persistent storage.
Private config/Compose/storage backups, seeking-capable `d3c3a78` rollback and
original `560ee29` working preview remain retained. Restore the corresponding saved
room config and Compose/image with the app stopped; preserve credentials/cache,
do not blindly restore the entire volume. Details: ALL_ROOMS_HANDOFF_REVIEW.md.

# Isolated partial playback-state backport

Later candidate builds also apply `seek-acknowledgement.patch`, reviewed separately
in [SEEK_REVIEW.md](../SEEK_REVIEW.md). The published preview applied only the
partial-state patch below. Neither contribution is submitted/accepted upstream.

Later builds apply `deferred-paused-seek.patch` after the seek
acknowledgement patch. It stores a desired paused cursor until explicit Play only
for backends opting into deferral; see `SEEK_REVIEW.md` for source safety and tests.

The post-beta candidate additionally applies `deferred-start-position.patch` to
the exact paused-seek player hash. A stopped/load-only SET_STATE position is a
desired start offset, not an applied clock or transport Seek. Retain it across
separate state-only Play; consume it once via the existing acknowledged post-start
Seek. Stop, track/queue-occurrence change, authority/session invalidation and room
endpoint retirement discard it. There is no timer, old-session restoration or
retry. This fixes the split paused snapshot → Play sequence observed after Spotify
handoff. Existing paused-scrub semantics are unchanged. See
`POST_BETA_FIX_REVIEW.md` for the exact evidence, naming fix and candidate checks.

Base: qobuz-proxy `91db990abeb486005a8315a6b1e722df776c4ba4` (unchanged dependency pin).
Only `qobuz_proxy/playback/player.py` is patched. Attribution and the installed
upstream package remain intact. The Docker build verifies the original source
SHA-256 before applying exact-context hunks; unexpected source fails the build.

`partial-playback-state.patch` and `test_partial_playback_state.py` are a proposed
upstream contribution, not a submitted or accepted upstream fix. Apply the patch
to that base and run `python -m unittest discover -s upstream-patches` (or copy
the standalone test into upstream's tests directory). Tests import only upstream.
The room-specific comparative transport matrix lives in `tests/test_partial_state.py`.

## Verified hypothesis

`playingState` is optional in the protobuf and stock handler preserves omission as
`None`. The stock player instead used `playing_state == 2` for loading each item,
so Play → item-without-state stopped/loaded but did not start the selected item.
This reproduced with unmodified pinned upstream and current upstream HEAD
`10dedc9d2ac0ff2edcad729e22876d2c7f158c8e`, using identical inputs and fake DLNA.
Upstream `ab5137533971d5ae417d4dded6b2f461cd321614` (#33) prevents queue-only
updates cancelling loads, but does not address this reversed sequence. Its
no-playback-fields guard is included here; a broad dependency upgrade is not.

Requested mode persists only within admitted playback authority, independently of
observed renderer state. Partial fields merge into an outstanding intent before
upstream serialization; identical transport intents share a generation rather
than cancelling one another. A new item resets an old pending seek/context.
Explicit Pause/Stop or a newer target supersedes the pending intent. Source/session
invalidation clears requested mode and outstanding intent. Startup, paused,
disconnected and externally revoked selections never gain an implicit Play.
Natural queue exhaustion and externally observed pause/stop update requested mode.

No message-pairing deadline, sleep-based admission, retry, custom queue/player,
or metadata-only unconditional autoplay is added. A valid playing-mode delta
means a subsequent item-only update continues that admitted mode, not that every
track selection starts playback. This protocol interpretation remains subject to
same-image Android/hardware acceptance.

Before URI transmission a changed transport intent cancels the obsolete start.
Once URI transmission begins, stock's serialized URI+Play sequence completes
under unchanged source authority; a queued Pause then applies. Source takeover,
deactivation, disconnect and topology retirement remain terminal even in that
transaction. This boundary is tested and must not be described as instantaneous
Pause cancellation after transmission.

Replay established that selection → Play succeeds in stock at the captured
pre-Stop-read boundary. Removing the previous room ordering workaround reproduces
the historical cancellation: our per-command mid-SOAP generation guard caused it.
The new integration removes committed-load exceptions and both load deferrals;
source/activation checks remain, with supersession checked before URI transmission.

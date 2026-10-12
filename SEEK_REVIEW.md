# Seeking investigation and undeployed candidate

The published `upstream-first-preview-2026-10-12` tag and running implementation
`560ee29` remain unchanged. This document describes later candidate development,
not a seeking fix already accepted on hardware. No transport mutation or alternative
Seek mode has been tested against the real renderer during this investigation.

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

## Prepared image — not deployed

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

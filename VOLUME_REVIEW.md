# Focused volume routing and session recovery review

## Checkpoint and evidence

The playback/availability implementation was checkpointed locally before this
review. Audible playback, retained Qobuz ownership, pause and resume were reported
by the hardware tester. Volume, natural advancement, manual skip and real Spotify
takeover are not accepted until separately tested. No deployment configuration,
account data, hardware identifiers or raw deployment logs belong in this document.

The existing volume incident recorded Node's renderer command `SetVolume` with
`ECONNRESET`. That record is created inside `Controller.action` around the SOAP
promise; it is not an aiohttp reset of Python's Node API connection. The existing
code then revokes the lease and returns an API error. Python releases its backend
and cloud session; a subsequent handshake with the same seen session ID is rejected
by both the receiver and Node selection policy. The original mutation elapsed time
and historical HTTP response were not retained; they must not be invented.
Read-only checks after the incident found the container healthy with no restarts
and both Node state and setup APIs responding HTTP 200.

## Volume route

Reference integration inspected at
[`ulilicht/ha-raumkernel` revision `f457b70b8af21c5423b74def4add0f2764336e8b`](https://github.com/ulilicht/ha-raumkernel/tree/f457b70b8af21c5423b74def4add0f2764336e8b):

- `ha-raumkernel-addon/rootfs/app/RaumkernelHelper.js`, `_getRendererForRoom`
  (lines 755–785), resolves the room's zone virtual renderer first.
- Its `setVolume` (lines 1337–1345) uses `renderer.setRoomVolume(room.roomUdn,
  volume)` for a room in a zone; physical `setVolume` is the standalone fallback.
- `node-raumkernel/lib/lib.device.upnp.mediaRenderer.raumfeldVirtual.js`,
  `setRoomVolume`, sends RenderingControl `SetRoomVolume` with `Room` and
  `DesiredVolume`. Its convenience method omits `InstanceID`.
- The selected renderer's live SCPD declares `SetRoomVolume` inputs
  `InstanceID`, `Room`, `DesiredVolume`; `GetRoomVolume` declares `InstanceID`
  and `Room`. Read-only getters succeed. Neither declaration nor getter success
  proves the setter succeeds on this device.

The proposed route for our selected ungrouped room already owned through a zone:

```
virtual zone renderer / RenderingControl / SetRoomVolume
{ InstanceID: 0, Room: selectedRoom.id, DesiredVolume: integer 0..100 }
```

`Room` is the exact topology room UDN, including its prefix; it is not the zone
UDN, physical-renderer UDN, display name or a normalized UUID. Readback uses the
same virtual renderer and `GetRoomVolume { InstanceID: 0, Room: selectedRoom.id }`.
The proposed controller never sends both setters and never falls back to another
renderer. Grouped or unowned rooms cannot reach this route.

## Failure handling

- One mutation only; no automatic retry or setter fallback.
- Independent sanitized records distinguish Python → Node `/v1/control` HTTP
  status/elapsed time from Node → virtual RenderingControl SOAP action,
  elapsed time, HTTP fault status when available, allowlisted exception code and
  numeric UPnP fault. No URLs, IDs, arguments or exception text are retained.
- A successful setter is reported applied only after matching room-volume
  readback. A reset/timeout remains unconfirmed even if readback happens to match.
- After the mutation, complete new observations are required for every leased
  renderer. They must show unchanged single-room membership, an unexpired identical
  lease, no Spotify, and the expected virtual Qobuz relay/validated physical
  forwarding. Cached pre-command state and transport `PLAYING` alone are insufficient.
- If these observations prove ownership, volume uncertainty is reported without
  clearing that lease or the cloud session. Subsequent volume writes on the same
  uncertain lease are inhibited, including queued cloud echoes. Pause/resume still
  pass ordinary ownership guards. A new admitted selection is required to clear
  the volume write barrier; it is never generated automatically.
- Missing observations, changed source/membership, expired ownership or inability
  to reach Node fail closed. Independent source events can revoke at any point,
  including during readback. No Stop or automatic regrouping is sent.
- The player reports observed readback, not the requested level, and preserves
  the uncertain operation as a failed volume result.

Session causes are kept distinct: command failure, unavailable control API,
unavailable observations, and lease/source loss. The Node ownership-loss record
further distinguishes actual unexpected source evidence from expiration or topology
loss. A seen handshake ID remains a replay even if the user says they reselected;
the protocol has no independently verified new-intent signal for that same ID.
A valid different session ID arriving at the explicit handshake endpoint may
establish a new pending selection, but still cannot mutate until Play. No guard
reset, silent reselect, lease extension or automatic reclaim is part of this change.

## Controlled hardware test — approval required

1. Retain the current image/configuration privately for rollback. Proposed deployment
   changes only this app's image. Preserve `/data`, credentials, selected room,
   CD quality, host networking, corrected LAN address and automatic discovery.
2. After explicit approval, verify image, health, authentication, freshness and
   advertisement. Do not start audio from deployment or inspection code.
3. The user selects the same room in Android and starts playback. Confirm retained
   ownership and choose one small volume increase (one percentage point).
4. Read the room volume before and after. Record the one setter attempt, readback,
   both connection diagnostics, ownership and process health. No fallback/retry.
5. If acknowledged and readback matches, the user may restore the original volume
   with one separate explicit adjustment. If uncertain, stop volume testing: do not
   resend or restore automatically. Check whether playback and session survive;
   source evidence must remain fresh. If missing, release control without Stop.
6. Roll back to the privately retained prior image/configuration if preservation,
   health or safety verification fails. Restoring the app does not retry a volume
   mutation or force-stop the speaker.

Only after volume acceptance: natural next-track playback, manual skip, then real
Spotify takeover. These remain unverified regardless of simulated test results.

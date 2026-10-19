# Offsite setup/advertisement diagnostic candidate

Branch: `fix/setup-advertisement-diagnostics`, based on stable `v1.0.0`
(`c5b87d52937fbe62d983da5a4863ff2c8a1e43ba`). Chromecast development is paused
and none of its experimental code is included here. This is a **test image**, not
a stable upgrade, proven offsite playback fix, or deployment to an existing host.

## Changes

- Storage failures report `storage_permission_denied`, `storage_read_only`,
  `storage_full`, `storage_invalid_json` or `storage_io_error` plus a safe filename
  and read/write operation. No raw OS error, credential contents or path is exposed.
- Config changes enter memory only after an atomic, mode-0600 disk replacement.
  Failed writes preserve the old file, selection and running receiver sessions.
  Callback/logout credential writes also precede replacing the active session state.
- A failed refreshed-token write no longer leaves a closed API client installed as
  connected. Credential-read failures are storage errors, not speaker-state errors.
- Container `setup_state` events show actual configured/discovered/advertised counts,
  authentication, playback path, per-room categorical blockers and safe renderer
  read-error codes. Only changes are logged; no periodic log flood, household names,
  IDs, addresses, account details, media URLs or tokens.
- Setup displays the same advertisement blockers, including saved room IDs absent
  from discovery, missing/nonlocal LAN address, grouped/unresolved zones, incomplete
  observations, authentication/persistence and receiver bind failures. Save/signout
  errors display the actionable message, not the old generic failure.
- Old hard-coded UI version/footer and unconditional sign-in startup instruction
  are replaced by runtime version/path and state-change information.

No speaker-readiness/ownership checks are weakened. No automatic zone creation,
source takeover, regrouping or mutation retry is added. Existing upstream-first
playback/player/backends and default launcher selection are unchanged. The tested
default is **guarded**, matching the reported installation: this diagnostic does
not silently migrate it to upstream_first. Choosing upstream_first requires the
existing accepted-build setup and is not a workaround for unexplained readiness.

## Download and install offsite (Linux amd64)

Test-image prerelease: `setup-diagnostics-2026-10-19`.
Local image tag after load: `raumfeld-connect:setup-diagnostics-2026-10-19`.
GitHub credentials available to the publisher lack GHCR package-write/workflow scope;
therefore delivery is a GitHub **prerelease image archive**, not a registry push.
Stable/latest registry tags and existing release tags are not changed.

Download `raumfeld-connect-setup-diagnostics-linux-amd64.tar.gz` and `SHA256SUMS`
from the prerelease Assets. On the offsite Docker host:

```sh
sha256sum -c SHA256SUMS
sudo docker load -i raumfeld-connect-setup-diagnostics-linux-amd64.tar.gz
sudo docker image inspect raumfeld-connect:setup-diagnostics-2026-10-19 \
  --format '{{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

Before replacing the offsite container, privately back up its Compose/app settings
and entire data directory, including credentials; retain its current local image.
Stop the old app first. Change **only the image** in the offsite app settings to the
local tag above and set `pull_policy: never` / disable forced image pulling in the
platform's UI. Preserve API_TOKEN, LAN_ADDRESS, RAUMFELD_HOST, volume mapping,
permissions, networking and playback-path settings. Do not start two instances
against one writable volume or competing discovery ports. Never switch to registry
latest when the local image cannot be found. Do not use this amd64 archive on arm64.

The image does not repair mount permissions automatically. Its default user is
UID/GID 10001:10001; verify your app's actual override before changing ACLs. Grant
that runtime identity read access to existing files and directory traverse/create/
replace permission **only on the app's dedicated data storage**. Do not recursively
change a pool/dataset hierarchy, grant world write, delete config, or post credentials.

After startup, refresh the existing setup UI. Look for `setupDiagnosticsVersion: 1`
in authenticated status or the new `setup_state` events in logs. These events are
safe to share after checking for unrelated log output. Report the per-room alias,
blockers and read-error codes. A displayed "unknown" source is not itself an
advertisement blocker; the complete-observation/read errors explain readiness.

The advertiser count describes **local registration**, not actual discovery by a
Qobuz phone, cloud session activation, delivered audio, or audible playback. A count
of zero should now have an actionable prerequisite/error; a positive count with no
phone-visible target is a separate multicast/network investigation.

## Validation and rollback

- 199 Python tests (including real unprivileged filesystem-permission failures).
- 120 serial Node tests, including UI rendering/actionable errors.
- Offline Python → Node → synthetic renderer/audio integration passes; no speakers
  or accounts were used. No offsite deployment or audible playback is claimed.
- Image startup/storage/API smoke results and exact artifact digest are recorded
  in the prerelease notes after packaging.

Rollback: stop this candidate, restore the previous image/app settings with the
same data mount, then start only that app. No config schema migration is introduced.
Keep the private backup; restore storage only if separately necessary, not merely
because the image changes. Stable production and the separate Cast diagnostic
installation are not modified by building or publishing this candidate.

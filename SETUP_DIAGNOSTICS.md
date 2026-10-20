# Offsite setup/advertisement diagnostic candidate

Diagnostics and their setup GUI are included on **`main`**, merged through PR #4.
They originated on `fix/setup-advertisement-diagnostics`, based on stable `v1.0.0`
(`c5b87d52937fbe62d983da5a4863ff2c8a1e43ba`). Chromecast development is paused;
the experiment, separate GUI and disabled grouping stub have been removed from
`main`. The separately downloadable image below remains a **test image**, not a
stable upgrade, proven offsite playback fix, or deployment to an existing host.

## Setup GUI

Use the existing setup page on port **8788**, with the installation's setup token.
The diagnostics are part of this page, not a separate Cast service or port 8799.
The page shows runtime version/playback path, authentication state, local advertiser
count, global/per-room blockers and storage/read errors. Save and signout failures
retain their actionable server message. No Chromecast or nonfunctional grouping
control is presented. Native grouping is not implemented by this diagnostic change.

Updating source on main does not replace a running container. Rebuild from main or
load the pinned test image below; restarting an older image does not add the GUI.
No main push or cleanup here changes a running Qobuz or Cast deployment remotely.

## Publishing / pulling registry latest

Repository maintainers can explicitly rebuild `main` and publish `latest` through
the test-gated GitHub Actions workflow:

```sh
gh workflow run ci.yml --ref main -f publish_latest=true
```

This builds linux/amd64 and linux/arm64, updates only registry `latest` and the
source-SHA image tag, and creates no version tag or GitHub release. It refuses to
publish a source revision that is no longer main. Existing versioned images/releases
remain untouched. Ordinary source pushes do not automatically publish latest.
Check the workflow result and the registry revision label before claiming delivery.

After a successful publication, users can pull and recreate their existing app:

```sh
docker pull ghcr.io/tammuzgreeen/qobuz-raumfeld-connect:latest
```

Retain the previous image/digest and private app/data backup for rollback. Keep the
same persistent data, API token, networking and other environment settings; do not
start a second competing instance. Registry builds now default to **upstream_first**,
matching the accepted local playback integration. Set `PLAYBACK_PATH=guarded`
explicitly to use the legacy setup/advertisement diagnostics GUI described here.
See [playback-path selection and upgrade notice](README.md#playback-path-selection).
The setup diagnostics do not themselves
establish the cause of an offsite readiness failure or guarantee phone visibility.

Clean source tests now apply the same hash-checked SDK/player patches as Docker,
before importing/testing those dependencies. Node tests run serially, matching the
validated local command, without removing or weakening source/ownership assertions.

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
playback/player/backends are unchanged. The original diagnostic archive defaults
to **guarded**, matching the reported installation at that time; current registry
`latest` defaults to **upstream_first**. Upstream-first requires the existing
accepted-build setup and is not a proven fix for unexplained readiness or API errors.

## Download and install offsite (Linux amd64)

Test-image prerelease: `setup-diagnostics-2026-10-19`.
Local image tag after load: `raumfeld-connect:setup-diagnostics-2026-10-19`.
The original diagnostic delivery was a GitHub **prerelease image archive**, because
the publisher then lacked workflow scope. That pinned archive remains available
independently of later registry-latest rebuilds; pulling registry latest does not
mean pulling this exact archive. Existing versioned release tags are unchanged.

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

# Setup and rollback — upstream-first preview

This is an opt-in one-room/CD preview, not the older registry release. These
instructions are for a deliberate installation/recreation; publication itself
does **not** change the running deployment. Do not run a second instance against
an in-use room/volume, and do not change a working system just to match this template.

## Preserve the tested deployment first

Record the running image ID, source label, private effective Compose and volume
mount. Keep these records outside Git, with owner-only permissions: Docker inspect
and resolved Compose can reveal your API token, network and household identifiers.
Never publish `/data`, account files, raw logs, signed URLs or private backups.

The tested source revision is `560ee29e19e242fb336281823aa2caa36d9bcafc`.
Its running image ID/config digest is
`sha256:4baf7a4f6b3d480a00e8f8abe0f9711a8df3747cee2cbaa8368e0e01d528679f`.
It is not a registry manifest digest. Preserve your exact running image locally:

```sh
umask 077
# CONTAINER_NAME is your private existing container name.
TESTED_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$CONTAINER_NAME")
docker image tag "$TESTED_IMAGE_ID" raumfeld-connect:tested-preview-rollback
docker image save --output tested-preview-rollback.tar raumfeld-connect:tested-preview-rollback
sha256sum tested-preview-rollback.tar > tested-preview-rollback.tar.sha256
```

Tagging/saving does not restart a container. Also retain your existing configuration
and a consistent private backup/snapshot of its data volume. Never use `down -v`,
delete credentials, overwrite the corrected LAN address or drop/regroup rooms.

## Obtain or rebuild an image

**No downloadable preview container image is available.** The GitHub prerelease
has source archives, not OCI archives or registry images. If you already possess
the private tested archive, verify its supplied checksum, `docker image load --input
<PRIVATE_ARCHIVE>` and inspect the image ID before use. Do not substitute the older
`v0.2.0` or `latest` registry image.

To build the tested playback sources separately from the publication documents:

```sh
git clone https://github.com/TammuzGreeen/qobuz-raumfeld-connect.git
cd qobuz-raumfeld-connect
git fetch origin refs/tags/upstream-first-preview-2026-10-12:refs/tags/upstream-first-preview-2026-10-12
git checkout --detach upstream-first-preview-2026-10-12
git worktree add --detach ../raumfeld-preview-tested-source 560ee29e19e242fb336281823aa2caa36d9bcafc
docker build --platform linux/amd64 \
  --build-arg PLAYBACK_PATH=upstream_first \
  --build-arg UPSTREAM_STATE_DIAGNOSTICS=1 \
  --label org.opencontainers.image.revision=560ee29e19e242fb336281823aa2caa36d9bcafc \
  --label io.raumfeld.upstream-state-diagnostics=1 \
  --tag raumfeld-connect:upstream-first-partial-state-review \
  ../raumfeld-preview-tested-source
```

Preserve the Dockerfile's digest-pinned bases, Node tarball pin/lockfile, qobuz-proxy
pin/uv lockfile and hash-checked player patch. Rebuilds may have different image IDs
due to build/export metadata and build-backend resolution; they are **not the exact
already-tested artifact**. Only amd64 is hardware-tested here; no arm64 preview
acceptance is claimed. Diagnostics emit fixed categories, but other upstream logs
can still include private metadata and must not be posted publicly.

## Required private state and placeholders

Use `docker-compose.preview.yml` **alone**, not merged into `docker-compose.yml`.
It preserves the tested shape: host networking, init, read-only root, `/tmp` tmpfs,
existing volume at `/data`, all capabilities dropped, no-new-privileges,
unless-stopped restart and 12-second shutdown grace. `PLAYBACK_PATH=upstream_first`
and diagnostics are embedded in the tested image; the template needs no playback
environment override. The unchanged Dockerfile default is otherwise `guarded`.

Keep a private `.env` beside the template (already Git-ignored):

```dotenv
# LOCAL image tag after load/build; never use a registry tag for this preview.
PREVIEW_IMAGE=raumfeld-connect:upstream-first-partial-state-review
# EXISTING prepared named Docker volume; use your actual private name.
RAUMFELD_DATA_VOLUME=<EXISTING_PRIVATE_VOLUME_NAME>
# Docker HOST's LAN IPv4, not the speaker IP; preserve the corrected value.
LAN_ADDRESS=<DOCKER_HOST_LAN_IPV4>
# Preserve the existing token; for a NEW installation generate openssl rand -hex 32.
API_TOKEN=<EXISTING_PRIVATE_TOKEN_AT_LEAST_32_CHARACTERS>
```

Replace every angle-bracket placeholder privately. Leave `RAUMFELD_HOST` empty;
the template enforces this. Do not post resolved `docker compose config` output.

The existing volume must contain private `config.json` with exactly one real
discovered room ID/name and quality `6`. For the tested port shape its room has
discovery port `8790`, and upstream audio proxy uses `8890` (discovery + 100).
Schematic configuration only—**do not replace existing files with placeholders**:

```json
{"rooms":[{"id":"<REAL_DISCOVERED_ROOM_ID>","name":"<PRIVATE_DISPLAY_NAME>","port":8790}],"quality":6}
```

Preserve the original private `credentials.json`. The preview prefers optional
`/data/upstream-first/credentials.json` if token refresh creates it, otherwise uses
the original; absence of the optional file is valid. Keep UID 10001 write access
to the data directory, with restrictive permissions, not world-readable files.

For a **new installation only**, first provision/login/select the single room/CD
configuration using the existing guarded setup flow on a **new** volume, then stop
that instance before deliberately switching to the upstream-first image. This is
a bootstrap prerequisite, not hardware acceptance of the old path. The preview's
upstream UI cannot add/edit/remove rooms; do not fabricate IDs or copy somebody
else's credentials. Existing prepared installations need no bootstrap or login
reset. Room must already be ungrouped; this service will not drop/regroup it.

```sh
# Validate without printing expanded secrets; this command does not deploy.
docker compose --file docker-compose.preview.yml config --quiet
# ONLY after deliberate installation approval, with the prepared volume/image:
docker compose --file docker-compose.preview.yml up --detach --no-build --pull never
```

Host networking requires trusted native Linux LAN access. Allow mDNS/SSDP and
speaker access to configured discovery/audio ports. Companion and upstream UI are
loopback-only on 8787/8788, not browser-accessible directly from another machine.
For private administration, an SSH tunnel from your workstation can expose the UI:

```sh
ssh -N -L 8788:127.0.0.1:8788 <PRIVATE_USER>@<DOCKER_HOST>
```

Open `http://127.0.0.1:8788` locally. Status contains account/track information;
never post it raw. Do not forward these ports publicly. Select the room explicitly
in Android and press Play to take over; discovery alone must not replace Spotify.

## Rollback without losing state

Do not execute rollback while the current image is working merely because a new
checkpoint exists. If rollback is deliberately needed, verify/load the privately
saved image archive; set `PREVIEW_IMAGE=raumfeld-connect:tested-preview-rollback`
in your private configuration and recreate **the same existing app/container**
with its unchanged data volume/environment. With this standalone template:

```sh
sha256sum --check tested-preview-rollback.tar.sha256
docker image load --input tested-preview-rollback.tar
docker compose --file docker-compose.preview.yml up --detach --no-build --pull never
```

For TrueNAS, retain the existing private Custom App Compose dictionary and change
only its image/pull policy; do not create a competing app/volume. Reverting to a
historical guarded-path image instead requires its own saved configuration and
launcher settings, not the preview defaults. Never restore data over a running
container; if necessary, use your consistent private snapshot during approved
downtime. Check health/authentication and exact room configuration afterward;
health alone is not audible or handoff acceptance.

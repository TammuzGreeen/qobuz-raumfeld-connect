# Raumfeld Connect

Qobuz Connect for Raumfeld speakers, with native Spotify source arbitration.
One Docker image bundles an unmodified, pinned `node-raumkernel` and
`qobuz-proxy`, connected by a small guarded backend.

## Version 0.2.0

- Qobuz OAuth login through a setup page; no password is stored.
- Select individual rooms to advertise in the Qobuz app, with stable identities.
- Play, pause, resume, stop, seek and volume; upstream queue handling provides
  next/previous, metadata, state reporting and automatic track advance.
- HTTP audio relay with byte-range support for speakers and seeking.
- Explicit app selection authorizes takeover. Spotify/native source changes,
  host loss, stale state and topology changes revoke Qobuz control.
- Published Linux amd64/arm64 images, persistent configuration and credentials,
  process supervision, health checks and CI.

**Experimental: actual Qobuz account login and playback on Raumfeld hardware
have not been verified.** Tests cover simulated playback, ownership races,
upstream interface integration and container lifecycle. Grouped playback and
gapless transitions are not enabled. No separate Spotify receiver is installed.

## Install

**TrueNAS:** use the [Custom App GUI guide](TRUENAS.md). Enter the published image
and environment variables directly; no terminal or Compose file is needed.
Host networking and persistent storage are configured in the TrueNAS form.

Use a native Linux Docker host on the speaker LAN.

```sh
git clone https://github.com/TammuzGreeen/qobuz-raumfeld-connect.git
cd qobuz-raumfeld-connect
python3 docker/configure.py
docker compose pull
docker compose up -d
```

The configuration helper asks for the **Docker host's LAN IPv4 address**, creates
`.env` with a random setup token, and does not overwrite an existing file.
Alternatively, copy `.env.example` to `.env`, fill `LAN_ADDRESS`, and set
`API_TOKEN` to a token from `openssl rand -hex 32`. Set `RAUMFELD_HOST` only if
automatic host discovery fails.

Open `http://<Docker-host-IP>:8788`, unlock with `API_TOKEN` from `.env`, sign in
with Qobuz, and select rooms and quality. Click Refresh after discovery/login.
Selected available single rooms appear in the Qobuz app. Choose one in the app
to explicitly switch that room from its current source. Playback does not start
merely because the container starts or a room is configured.

Image: `ghcr.io/tammuzgreeen/qobuz-raumfeld-connect:0.2.0`. If the GitHub package is
private, authenticate with `docker login ghcr.io` using a GitHub token permitted
to read the package. Package visibility can be managed in GitHub. Source builds:

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d
```

## Networking and storage

Host networking enables multicast discovery and speaker callbacks. Allow LAN
traffic for setup TCP 8788, configured receiver/audio TCP ports 8790–8839, mDNS
UDP 5353, SSDP UDP 1900 and UPnP callback ports chosen by upstream. Node's control
API stays on localhost:8787. Bind `LAN_ADDRESS` to the interface speakers can
reach. The setup/Connect interfaces are for a trusted LAN; use a private network
or TLS reverse proxy for remote administration. Do not forward them publicly.
For a reverse proxy, set `PUBLIC_URL` to the browser-facing setup URL.

The `raumfeld-data` named volume persists room configuration, stable ports and
Qobuz tokens. Files are written atomically with owner-only permissions by UID
10001. Treat backups as credentials. Signing out clears the stored login and
releases sessions without sending Stop to another source. Do not delete the
volume when upgrading. Recreate the container after changing `.env`.

## Source protection

- Only a new Qobuz app discovery handshake creates a 30-second, one-use selection.
- First playback consumes it and starts a renewable 15-second ownership lease.
- Heartbeats cannot extend unused selections. Replayed handshakes, cloud
  reconnects and queue updates cannot reclaim a lost lease.
- Single-room commands refresh observations and verify ownership before each
  action. Explicit first playback may create a missing single-room zone once.
- New Spotify/external URI evidence or loss of state revokes the lease. No
  automatic repair, regrouping or forced retry follows.
- Multiroom zones are rejected, even if only one member is configured. Ungroup
  using the Teufel app before selecting a room here.

There is an unavoidable interval between a final state check and a network
command reaching a speaker. Already-sent commands cannot be recalled. On an
uncertain timeout the session is revoked, never retried automatically. These
guards need real-device evaluation before claiming uninterrupted switching on
every firmware. If a speaker refuses to leave Spotify mode, the command fails;
the service does not escalate to repeated Stop/repair attempts.

## Quality, updates and troubleshooting

CD-quality FLAC is the default. Higher quality can be selected when your speaker
supports it; the configured quality is a ceiling for app requests. Actual format
support and high-resolution playback need hardware validation.

```sh
docker compose logs --tail 100
docker compose pull
docker compose up -d
```

Use a version tag or the published image digest for a fixed deployment. `latest`
tracks releases; `sha-<commit>` identifies each published source revision.
Rollback by changing the image tag and recreating the container with the same
volume. Changing selected rooms or quality releases sessions: reselect in the
Qobuz app afterward. After Spotify takes over, select another Qobuz target and
then this room to establish a new session.

If rooms are missing, check host networking, firewall, `LAN_ADDRESS` and optional
`RAUMFELD_HOST`. If login says retrying, the saved token is retained while Qobuz
is unreachable. If authentication is rejected, sign in again. Credentials, signed
stream URLs and detailed upstream account logs are not exposed in setup status.

Source labels such as Spotify do not make a room unavailable. Advertisement
requires fresh physical/zone renderer observations and single-room topology.
Setup explains observation and receiver startup failures. `LAN_ADDRESS` must be
assigned to the Docker host, not to a speaker or another machine. See the
[speaker availability diagnostic and review deployment guide](SPEAKER_AVAILABILITY.md).

## Development and CI

Node 24, Python 3.12–3.13, uv 0.8.22:

```sh
npm ci --ignore-scripts
npm test
npm run smoke
uv sync --project qobuz --frozen --no-dev
qobuz/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
docker build -t raumfeld-connect:local .
```

CI tests the actual pinned upstream imports/player wiring, source protection,
HTTP APIs, OAuth state handling, setup persistence and container startup/failure/
shutdown. A main commit named `Release <version>` publishes multi-architecture
images and creates a GitHub release only after validation passes. Bump package,
Compose, UI and OCI version fields together for a new release; never reuse a
released version for changed code.

Runtime versions and integrity hashes are in `package-lock.json` and
`qobuz/uv.lock`; Docker bases and actions are digest/commit pinned. Upstream Node
still brings advisories in `request`, `ip`, `tough-cookie` and `uuid`. Reviewed
`form-data` and `qs` overrides remove additional vulnerable versions without
patching upstream source. These remaining advisories make trusted-LAN-only use
especially important. Build backend dependencies follow upstream metadata, so
byte-for-byte reproducibility is not claimed.

See [architecture](ARCHITECTURE.md), [API](shared/README.md), and
[release notes](CHANGELOG.md). MIT for this repository; dependencies retain
their original licenses.

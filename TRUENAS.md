# TrueNAS Custom App installation

Use the published image directly in **Apps > Discover Apps > Custom App**.
No Git clone, terminal, Compose file, configuration helper, or custom startup
command is needed. These instructions target Docker-based TrueNAS SCALE
(24.10 and later). Version 0.2.0 already contains both services and their startup
supervisor; there is no separate Node or Python app to install.

## Image fields

| Field | Value |
| --- | --- |
| Application name | `qobuz-raumkernel` |
| Image repository (full image name, without tag) | `ghcr.io/tammuzgreeen/qobuz-raumfeld-connect` |
| Tag | `0.2.0` |
| If the form has a separate Registry field | `ghcr.io` (then Repository is `tammuzgreeen/qobuz-raumfeld-connect`) |
| Entrypoint / command / arguments | Leave defaults |

If image pull reports unauthorized, configure a GHCR registry credential in
TrueNAS using your GitHub username and a token with permission to read the
package. Do not put registry credentials in application environment variables:
the registry login happens before the container starts. Package visibility is
controlled on GitHub; this guide does not assume anonymous image access.

## Environment variables

| Name | Value |
| --- | --- |
| `LAN_ADDRESS` | The TrueNAS IPv4 address on the same LAN as the speakers, e.g. `192.0.2.20 (example only; replace with your actual LAN address)` |
| `API_TOKEN` | Your own random secret of at least 32 characters; generate and save it with a password manager |
| `RAUMFELD_HOST` (optional) | The Raumfeld system host IP, only if automatic discovery fails |

Keep the default data directory `/data` and internal API address. Do not set
`API_BIND`, `API_PORT`, `CONFIG_PATH`, `RAUMFELD_API`, or `DISCOVERY_MODE`.
Qobuz account login and room selection happen in the app's web page after install.

## Required network setting

Enable **Host Network** in the Custom App's Network Configuration section.
Do not add port mappings with host networking. The setup page uses TCP 8788;
per-room Connect/audio endpoints use TCP 8790–8839, and discovery needs LAN
multicast (mDNS/SSDP) and UPnP callbacks. Those ports must be available on the host.

An image or environment variable cannot enable Docker host networking or publish
ports. If your form genuinely exposes only image and environment fields, it
cannot deploy this receiver correctly: find the Network Configuration section
or use a deployment form that exposes it. Setting `LAN_ADDRESS` alone does not
make a bridge-network container reachable by speakers.

## Keep login and settings across app updates

Add persistent storage in the GUI with **mount path `/data`**. Use an ixVolume
or a dedicated dataset/host path. Give container UID/GID **10001:10001** write
access to that storage using TrueNAS storage permissions. Keep the image's
default user; privileged mode is not required. A mount replaces the image's
existing `/data` directory, so its permissions must be correct.

Without this storage the app can start using its writable container directory,
but login and selected rooms can be lost when TrueNAS recreates the container.
Environment variables cannot create a persistent host mount.

## Open the app

After installation, open `http://<TrueNAS-LAN-IP>:8788` in your browser. Unlock
with the `API_TOKEN` you entered, sign in to Qobuz, select individual rooms,
and choose one of those receivers in the Qobuz app. Optionally add a TrueNAS web
portal pointing to HTTP port 8788 for a GUI shortcut.

Startup and room configuration do not start playback or authorize takeover.
A new explicit selection in the Qobuz app is required. Native source changes
revoke Qobuz control. Grouped playback is disabled in this release.

The image is published for Linux amd64 and arm64, with passing automated tests
and container startup checks. Real Qobuz account login, TrueNAS deployment and
Raumfeld hardware playback remain unverified.

References: [TrueNAS Custom App installation](https://apps.truenas.com/managing-apps/installing-custom-apps/)
and [Custom App form fields](https://www.truenas.com/docs/scale/apps/installcustomappscreens/).

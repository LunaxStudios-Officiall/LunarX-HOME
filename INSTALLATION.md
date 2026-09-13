# LunarX Home 3.1.2 installation

## Native Ubuntu

Supported native targets are Ubuntu 24.04 and 26.04 on `x86_64` or `aarch64`:

```sh
sudo ./install.sh install
```

The installer validates the release, installs required runtime packages unless `--skip-packages` is used, creates/updates LunarX service integration, keeps persistent state outside the application tree, stages code under `/opt/lunarx-home`, starts the service, and verifies `/healthz` against version 3.1.2 unless `--no-start` is requested.

Persistent native roots are `/etc/lunarx-home`, `/srv/lunarx-data`, `/var/lib/lunarx-home`, `/run/lunarx-home`, and `/var/backups/lunarx-home`.

## Android + Termux + Ubuntu/PRoot

Enter the Ubuntu PRoot guest and run as its root user:

```sh
./install.sh install
```

The PRoot path does not require or assume systemd. Unless `--skip-packages` is supplied, it installs the required Python/media packages plus XFCE/TigerVNC/noVNC support. The default persistent root is `$HOME/.local/share/lunarx-home`; set `LUNARX_PERSISTENT_ROOT` to choose another persistent root. The controller is linked narrowly at `$HOME/.local/bin/lunarxctl`.

## Successful output and first run

After a successful started installation, the installer emits the same information it actually uses:

```text
[LunarX] LunarX Home is ready
[LunarX] Listening: <bind>:<port>
[LunarX] Local URL: http://<local-host>:<port>
```

If the listener permits remote access, LunarX attempts to print a LAN URL and, on native hosts, a Tailscale URL when a valid address is discoverable. If an address cannot be determined, the installer says it is unavailable instead of inventing one. For Android/PRoot, Tailscale remains owned by the Android host app, not by the PRoot guest.

When no administrator exists, the output also contains one `FIRST_RUN_SETUP_TOKEN=...`. Open the web interface, choose **Primeira configuração**, and create the first administrator. Once an admin exists, install/update/repair does not generate a replacement token. Passwords and persistent secrets are never printed.

## Listener configuration

The effective listener is read from persistent `config.json` (`bind` and `port`). Both the server and `lunarxctl` honor the configured port; they do not assume 8787 when another valid port is configured.

## Storage behavior

A writable directory is a valid primary storage root; it does not need to be a separate mount. Native XFS project quota is used only when a compatible existing mount is positively detected. Android/PRoot uses logical/application-level quota accounting. LunarX does not automatically format or partition disks.

## Service control

- Native Ubuntu: systemd owns `lunarx-home.service`.
- Android/PRoot: `lunarxctl` owns the portable process/PID/log path.

Useful checks:

```sh
lunarxctl version
lunarxctl paths
lunarxctl status
lunarxctl doctor
```

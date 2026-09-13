# LunarX Home 3.1.3 troubleshooting

Start with the access information printed by the installer. The web application normally listens on the configured `bind`/`port` (default loopback port 8787). `lunarxctl status` uses the persisted configured port rather than assuming the default.

```sh
lunarxctl status
lunarxctl doctor
lunarxctl paths
lunarxctl logs
```

## Cannot open the UI

- If `Listening` is `127.0.0.1:<port>`, the service is intentionally loopback-only; LAN/Tailscale access is not available until the listener is configured for host-network access.
- On Android/PRoot, Tailscale is owned by the Android host. Do not install/control the Android VPN from inside the guest as if the guest owned the tunnel.
- Confirm `/healthz` on the effective local port and inspect `lunarxctl logs`.

## First-run token

The token is only for an installation with no administrator. It is consumed after the first administrator is created. An existing installation should not receive a new token during update/repair.

## Update failure

For an existing installation, 3.1.3 attempts automatic rollback to the timestamped pre-update application/config snapshot. If the installer reports that rollback also failed, do not delete persistent data. Keep the backup and follow `ROLLBACK_GUIDE.md`.

## Storage unavailable

Android/PRoot primary storage is a persistent writable directory and does not need to be a separate mount. Check permissions/path availability rather than forcing native XFS quota semantics. Files and Photos should surface a structured error if the selected root is genuinely unavailable.

## Application unavailable

An Android/PRoot APT provider is conditional until the guest's `apt-cache` reports a candidate. Web/PWA entries are explicitly marked as web. Source-only or unsupported entries intentionally have no fake Install action.

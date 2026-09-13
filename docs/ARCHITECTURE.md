# Architecture

LunarX Home is a dependency-light Python HTTP service with a static browser client. The browser talks to a session-authenticated API; privileged or platform-specific actions cross a small allow-listed broker boundary.

## Provider boundary

`lunarx_core/paths.py` resolves one canonical `PathConfig`. `platform.py` detects platform, architecture and capabilities. `providers.py` exposes storage, service, Desktop and app provider status. `quota.py` selects native XFS project quotas only when the filesystem and mount options prove support; otherwise it selects `LogicalQuotaProvider`.

The web process uses the direct broker on Android/PRoot and when `LUNARX_BROKER_MODE=direct`. Native Ubuntu can use `/usr/local/libexec/lunarx-admin` through `sudo -n`. Both paths return structured JSON with stable error codes. No UI code parses command stderr.

## Data flow

```text
Browser → session/CSRF API → path and quota checks → broker/provider → durable state or user data
                                      ↘ catalog compatibility → app provider
```

User data remains below the configured data root. Profile/account metadata lives in the configuration state file; local passwords are scrypt/PBKDF2 hashes only. Runtime tickets and activity are kept below the runtime root and never enter the release archive.

## Compatibility strategy

The old native roots remain defaults for migration compatibility. Android/PRoot derives persistent `config`, `data`, `state`, `runtime` and `backups` directories from `$LUNARX_PERSISTENT_ROOT` or `$HOME/.local/share/lunarx-home`. Code, config, data, state and runtime can each be overridden by `LUNARX_*_ROOT` values for tests and managed deployments.

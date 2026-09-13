# Provider contracts — LunarX Home 3.1.2

Providers are capability-first and must not fabricate success.

| Provider | Native Ubuntu | Android/PRoot | Gate/scope |
| --- | --- | --- | --- |
| `flatpak-user` | audited x86_64/aarch64 where available | not the standard PRoot path | per user / Flatpak capability |
| `apt-system` | provider-specific | yes, only after `apt-cache` candidate | system / admin |
| `vendor-apt` | provider-specific | allow-listed Firefox/Brave/DBeaver paths | system / admin / broker allow-list |
| `pinned-deb` | provider-specific | audited RustDesk ARM64 package | system / admin / expected SHA-256 |
| `web-pwa` | optional | official audited fallback | per user |
| `admin-apt-group` | supported where packages exist | supported where packages exist | system / admin |
| `verified-source` | informational unless an automatic provider exists | informational | no fake Install |
| `unsupported` | explicit decision | explicit decision | no action; reason required |

Browser input never supplies an arbitrary repository, package or launch command that bypasses the packaged provider definition. Privileged provider IDs are allow-listed in broker/vendor code; unknown privileged IDs are rejected.

Android/PRoot remains a separate target from native Ubuntu: logical quotas, portable supervision, TigerVNC/noVNC desktop integration and Android-host-owned Tailscale behavior remain intact.

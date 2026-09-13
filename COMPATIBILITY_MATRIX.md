# Compatibility matrix — LunarX Home 3.1.2

| Target | Validation in this release | Storage/quota | Desktop | Application path |
| --- | --- | --- | --- | --- |
| Ubuntu 24.04 x86_64 | simulated native preflight + static systemd files; physical host not available | native quota only when detected; logical fallback | capability-gated native path | audited x86_64 Flatpak + provider-specific system paths |
| Ubuntu 24.04 aarch64 | simulated native platform/catalog checks; physical host not available | same | capability-gated native path | audited aarch64 Flatpak where upstream provides it |
| Ubuntu 26.04 x86_64/aarch64 | installer target guard/static validation; physical host not available | capability-gated | capability-gated | provider availability resolved at runtime |
| Android + Termux + Ubuntu/PRoot aarch64 | first-class provider/path logic; PRoot-mode install/update/rollback executed in disposable filesystem; no physical handset | persistent directory + logical quota | TigerVNC/noVNC when installed | 56 actionable / 1 source-only / 97 unavailable |

## Android/PRoot final catalog counts

Catalog: **154 apps / 306 provider records**. With `aarch64` Android/PRoot and candidate APT packages available, **56 apps are actionable**: 36 runtime-gated `apt-system`, 15 official `web-pwa`, 3 allow-listed `vendor-apt`, 1 SHA-256-pinned ARM64 `pinned-deb`, and 1 `admin-apt-group`. One Hermes entry is source-only informational; 97 apps are explicitly unavailable with a source/reason.

The 36 APT apps are not universally promised: their Install action depends on the running Ubuntu guest reporting a real package candidate.

Native Ubuntu and PRoot are intentionally separate provider targets. See `CATALOG_ARM64_AUDIT.md`.

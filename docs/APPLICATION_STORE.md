# Application store — LunarX Home 3.1.2

The packaged catalog has **154 applications and 306 provider records**. It does not treat an app name as proof of platform compatibility. Each entry retains explicit provider/platform/architecture decisions, action methods, source, scope and an evidence-based unsupported reason where needed.

## Provider model

- `flatpak-user`: native Ubuntu only when the audited architecture matches and Flatpak is available.
- `apt-system`: Android/PRoot candidate mapping; the actual guest must report an installable candidate through `apt-cache` before Install is enabled.
- `vendor-apt`: allow-listed official upstream repository providers for Firefox, Brave and DBeaver Community. Arbitrary catalog repository identifiers are rejected by the broker.
- `pinned-deb`: audited ARM64 RustDesk release with a fixed expected SHA-256 before installation.
- `web-pwa`: official browser experiences, explicitly labeled Web/PWA and persisted per LunarX user.
- `admin-apt-group`: administrator-managed Ubuntu package group for the curated multimedia-codec entry.
- `verified-source`: informational source metadata only; it does not expose a fake automatic Install action.
- `unsupported`: no Install action and a specific upstream/evidence-based reason.

## Android/PRoot final result

With an aarch64 PRoot target and an available package probe, 56 apps are actionable: 36 conditional APT, 15 official Web/PWA, 3 allow-listed vendor APT, 1 pinned ARM64 DEB and 1 admin APT group. One entry is source-only; 97 are unavailable. Conditional APT availability can be lower on a real device because the running guest decides whether a candidate exists.

Installed-state detection remains provider-scoped. Open, Update and Uninstall are offered only when the chosen provider exposes a meaningful corresponding action. System-managed resources remain distinct from per-user Web/PWA/Flatpak state.

See `CATALOG_ARM64_AUDIT.md`.

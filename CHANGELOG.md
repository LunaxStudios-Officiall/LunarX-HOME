# Changelog

## 3.1.2 — final release hardening

- Fixed the release-blocking sidebar navigation selector bug: destination guards now interpolate the requested view correctly and retain safe invalid-destination rejection.
- Fixed the browser-only async login defect caused by reading `event.currentTarget` after an `await`; the submitted form node is captured before the asynchronous boundary.
- Added permanent real-Chromium click-through coverage for every personal/admin route, the mobile drawer cycle, invalid destinations, first-run/login, logout, application details, reduced motion, focus/touch targets, dialog containment and horizontal overflow.
- Improved installer access output: successful activation prints the effective bind/port, local URL, and LAN/Tailscale URLs only when they can be determined truthfully. A first-run token is emitted only while first administrator setup is still required.
- Added automatic rollback for existing installations when activation or post-update health verification fails. The previous application/config/install-state snapshot is restored and re-health-checked; persistent user data is never replaced by the code switch.
- Hardened Android/PRoot uninstall to stop the portable runtime and remove only the LunarX-owned `~/.local/bin/lunarxctl` symlink while preserving configuration, data, accounts and backups.
- Re-audited ARM64/PRoot providers. Final catalog: 154 apps / 306 provider records; 56 actionable on Android/PRoot, 1 source-only informational entry and 97 unavailable entries with explicit reasons. Actionable providers are 36 runtime-gated Ubuntu APT, 15 official Web/PWA, 3 allow-listed vendor APT, 1 SHA-256-pinned ARM64 DEB and 1 administrative APT group.
- Added/retained official providers for Firefox, Brave, DBeaver Community and RustDesk plus official Web/PWA fallbacks such as LocalSend and Slack. Privileged providers are allow-listed by broker code; arbitrary repository/provider identifiers are rejected.
- Refined drawer accessibility without changing the approved navigation model: drawer-range icon buttons now keep a 44×44 touch target, focus state/return behavior remains explicit, and body scroll is locked while the drawer is open.
- QA Review 1: 356/356 Chromium checks passed across all 11 required viewports. QA Review 2: 38/38 platform/install/update/rollback checks passed in the available disposable environment. The final frozen ZIP is released only after independent QA Review 3 passes.

## 3.1.1 — ARM64 compatibility and visual polish

- Promoted the application catalog to provider schema 3.0 with explicit per-platform/per-architecture installation, launch, update, uninstall, source, scope and unsupported-reason metadata.
- Audited all 154 catalog records for Android/Ubuntu-in-PRoot ARM64 decisions. The current audit records 117 Flathub apps with an aarch64 native-Ubuntu build, 34 Flathub entries that remain x86_64-only, 36 conditional Ubuntu/APT mappings for PRoot, and 13 official web/PWA fallbacks.
- PRoot APT actions are runtime-gated with `apt-cache`; an Install button is not enabled unless the current Ubuntu guest reports a real candidate. No x86 emulation is used to inflate compatibility.
- Added persistent per-user web-app registration plus managed system-package installed-state reporting, with admin gating for system-scoped packages.
- Expanded application details with larger icons, publisher, license, provider/type, platform/architecture, installed/available versions, storage information when known, source link, compatibility explanation and capability-correct actions.
- Replaced weak navigation glyphs with a coherent local SVG icon set and added a local application fallback icon so broken remote icons do not become broken-image placeholders.
- Refined card density, modal composition, focus states, mobile/tablet spacing and drawer polish while preserving the approved sidebar + hamburger/sliding-drawer navigation model.
- Fixed a broker state-isolation defect where a shallow default-state copy could leak nested in-memory values between isolated broker lifetimes/tests.
- Added 16 focused 3.1.1 tests. The complete suite now contains 45 tests, including prior 3.1 regression coverage.
- Added deterministic provider/catalog validation and Chromium responsive rendering QA across all requested phone, tablet and desktop viewport sizes.

## 3.1.0 — fusion rebuild

- Rebuilt the responsive UI/design system for phone, tablet and desktop.
- Fused the stronger 2.1 reliability behaviors with the 3.0 provider/path architecture.
- Added recoverable first-run local-admin bootstrap for portable/PRoot installations.
- Restored authenticated localhost TigerVNC/noVNC desktop delivery and retained native RDP fallback capability.
- Fixed installed-app provider errors being represented as empty success.
- Added compatibility gating, richer catalog state and graceful provider degradation.
- Improved directory storage initialization, logical-quota fallback and storage diagnostics.
- Added platform-aware Tailscale status/guidance.
- Added Android/PRoot desktop/runtime package installation and default service start/health verification.
- Expanded automated coverage with 3.1 fusion and runtime integration tests.

## 3.0.0 — provider architecture baseline

Introduced canonical paths, portable local authentication/broker, provider/capability models and staged installer/update structure.

## 2.1.0 — reliability baseline

Provided the stronger failure transparency, PRoot VNC/noVNC path, Tailscale diagnostics and compatibility-aware application behavior used as reference by 3.1.

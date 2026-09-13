# Execution log — LunarX Home 3.1.2

- Continued from the existing 3.1.2 working tree produced from the supplied LunarX Home 3.1.1 canonical baseline; no restart from 3.1.1 and no 2.1/3.0 re-merge was performed during final QA continuation.
- Preserved the 3.1 architecture, persistent path model, first-run/auth/session flow, storage abstraction, Desktop providers, Tailscale integration, installer and updater.
- Fixed the navigation destination interpolation defect and added real Chromium click-through regression coverage for normal/admin routes and the mobile drawer.
- Fixed the async login event-lifecycle defect by retaining the form reference across the authentication await boundary.
- Finalized truthful Android/PRoot ARM64 catalog resolution: 154 apps / 306 providers; 56 actionable, 1 source-only informational, 97 unavailable.
- Kept conditional APT availability runtime-gated and privileged vendor providers broker allow-listed; pinned downloadable provider integrity remains verified.
- Improved installer access output and configured bind/port handling without emitting a new first-run token for already-configured installations.
- Added automatic rollback for failed existing-install update activation/health checks, preserving persistent data.
- Hardened portable uninstall so the runtime is stopped and only LunarX-owned control linkage/code is removed; persistent data/configuration are preserved.
- Refined drawer focus, body scroll locking and touch targets while preserving the approved desktop sidebar and mobile hamburger/sliding drawer.
- QA Review 1: 356/356 Chromium checks across all 11 required viewports, with no captured console/page errors.
- QA Review 2: 38/38 disposable platform/install/update/rollback checks, including a real 3.1.1→3.1.2 preservation test and injected failed-update rollback.
- Physical Android hardware and a dedicated Ubuntu systemd host were not available; those field-only behaviors are explicitly documented as untested rather than marked PASS.

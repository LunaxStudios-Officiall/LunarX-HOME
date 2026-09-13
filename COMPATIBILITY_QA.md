# Compatibility QA — LunarX Home 3.1.2

**Decision: PASS for implementable checks; physical-host limitations are explicit.**

QA Review 2 completed **38/38** platform/install/update/rollback checks. It exercised an isolated Android/PRoot-mode install of 3.1.1, first administrator creation/login, a real update to the 3.1.2 working candidate with a non-default persisted port, health verification, user/config/profile/theme/file preservation, `lunarxctl` endpoint resolution, an intentionally broken staged update with successful automatic rollback, portable uninstall with data preservation, and a fresh 3.1.2 installation/access summary.

Catalog/provider validation measured 154 apps: 56 actionable on Android/PRoot with an available APT probe, 1 source-only informational entry and 97 unavailable entries. Conditional APT entries become unavailable when the package probe reports no candidate. Native x86_64 Ubuntu provider resolution is evaluated independently.

Native Ubuntu 24.04 preflight/systemd files were validated with a simulated Ubuntu platform because the execution host is Debian 13. No physical Android ARM64 handset or live Android-host Tailscale daemon was available.

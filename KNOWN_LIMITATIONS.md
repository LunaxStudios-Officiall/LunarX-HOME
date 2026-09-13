# Known limitations — LunarX Home 3.1.2

- No physical Android/Termux handset was controlled during this release build. Android background-process policies, vendor battery restrictions, GPU acceleration and Android permission prompts remain field-validation items.
- The QA host is Debian 13, not a disposable Ubuntu 24.04/26.04 systemd machine. Native Ubuntu preflight/service files were validated by simulation/static checks; real systemd/PAM/package installation remains target-host validation.
- Android/PRoot install/update/rollback was executed with isolated PRoot-mode filesystem/runtime semantics on the available Linux host, not on ARM64 handset hardware.
- Standard Flatpak is intentionally not presented as a universal PRoot installer. Android/PRoot uses runtime-confirmed Ubuntu packages, allow-listed vendor packages, a pinned ARM64 package where audited, official Web/PWA, or explicit unavailability.
- The 36 Ubuntu APT mappings are conditional. A catalog mapping does not prove that every Ubuntu release/repository exposes that package; installation remains disabled until the current guest reports a candidate.
- Web/PWA fallbacks can offer less functionality than desktop-native applications and are labeled as Web/PWA.
- Real graphical desktop behavior still depends on TigerVNC/noVNC/XFCE in PRoot and the native desktop backend on Ubuntu. Physical GUI latency/graphics behavior was not measured here.
- Tailscale is optional. In Android/PRoot the Android host app owns the VPN boundary; no live Android Tailscale daemon was available in this QA environment.
- The available Chromium environment administratively blocks direct local-HTTP navigation. Frontend QA therefore executes the exact packaged HTML/CSS/JS in real Chromium at an intercepted HTTPS origin with deterministic API responses; backend HTTP/install/login behavior is exercised separately by integration/platform tests.

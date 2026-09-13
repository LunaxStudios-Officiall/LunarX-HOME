# LunarX Home 3.1 fusion report

## Source decision

The 2.1 line was treated as the reliability baseline where its behavior was more truthful or field-oriented: provider failures stayed visible, catalog actions were compatibility-aware, Tailscale diagnostics were concrete, and Android/PRoot desktop used localhost TigerVNC + noVNC rather than assuming RDP.

The 3.0 line contributed the stronger structural pieces: canonical persistent paths, local password hashing for portable accounts, a structured direct broker, capability/provider objects, staged installer/updater behavior, and broader administration/UI surfaces.

3.1 does not copy either UI. The visual layer was rebuilt around a new tokenized responsive system while retaining stable endpoint contracts.

## Major regressions corrected

- Restored an authenticated VNC/noVNC browser bridge for the PRoot desktop path.
- Added portable first-user creation with one-time setup token generation on fresh runtime startup.
- Unified storage roots and directory creation so a valid writable directory does not become a false “storage unavailable” state.
- Changed installed-app handling so provider errors cannot masquerade as an empty successful list.
- Added runtime compatibility gating before app actions.
- Added Android/PRoot APT dependencies for XFCE/TigerVNC/noVNC rather than only declaring desktop capability.
- Replaced shallow Tailscale “unknown” reporting with platform-aware diagnostics.
- Made service activation the default install behavior and added a post-install health/version check.
- Added new mobile/tablet/desktop design rules and reduced-motion/accessibility behavior.

## Validation performed in this build environment

- Complete byte-level read/hash inventory of both supplied archives (`SOURCE_INSPECTION.json`).
- Original source test suites checked before fusion.
- Final Python compilation and JavaScript syntax checks.
- Final test suite including a live temporary server bootstrap/login/bootstrap-API flow.
- Release validator, deterministic package builder and extracted-archive verifier.

Hardware-dependent claims are intentionally separated into `KNOWN_LIMITATIONS.md` instead of being marked as verified.

# LunarX Home 3.1.2

LunarX Home 3.1.2 is the final hardening patch built directly on the 3.1.1 codebase. It preserves the approved desktop left sidebar and mobile hamburger/sliding drawer, fixes two browser defects that escaped source-only testing, hardens install/update rollback behavior, and finalizes truthful ARM64/Android-PRoot provider coverage.

## What changed in 3.1.2

- **Navigation is fixed and browser-tested.** Every visible personal/admin destination is exercised by actual Chromium clicks. Invalid destinations remain a safe no-op.
- **Async login is fixed.** The login form is captured before the authentication `await`, preventing the `event.currentTarget === null` failure observed in Chromium.
- **Installer output is actionable.** After a successful start, the installer prints the effective listener, local URL, and LAN/Tailscale URLs only when they can be determined safely. The first-run token appears only when setup is genuinely required.
- **Updates can roll back automatically.** Existing installs are backed up before activation. If activation/health verification fails, the previous application/config/install-state is restored and checked again. User files remain outside the application tree.
- **Android/PRoot catalog remains truthful.** The final 154-app catalog contains 306 provider records. On Android/PRoot aarch64, 56 apps have an actionable provider, 1 is source-only informational, and 97 are explicitly unavailable.
- **Small visual/accessibility polish.** The approved navigation is unchanged; drawer-range icon controls use 44×44 touch targets, focus return/`aria-current` are explicit, and the application details dialog remains viewport-contained.

See `CATALOG_ARM64_AUDIT.md`, `UI_QA.md`, `INSTALLER_QA.md`, `UPDATER_QA.md`, `QA_REVIEW_1.json` and `QA_REVIEW_2.json` for release evidence.

## Install

From the extracted release directory:

```sh
# Native Ubuntu 24.04 / 26.04
sudo ./install.sh install

# Ubuntu inside Termux/PRoot
./install.sh install
```

A successful started installation prints a `LunarX Home is ready` summary, the effective bind/port and a local URL. LAN/Tailscale URLs are shown only when the listener allows remote access and an address can be determined. On a fresh installation with no administrator, the one-time `FIRST_RUN_SETUP_TOKEN` is printed for **Primeira configuração**.

## Update from 3.1.1

Run the installer from the newly extracted **3.1.2** release:

```sh
sudo ./install.sh update   # native Ubuntu
./install.sh update        # Android/PRoot Ubuntu
```

Application code is staged separately from persistent state. Existing users, files, photos, profile/theme preferences, quotas, app registry and configuration are preserved. If an existing installation fails activation or post-update health verification, 3.1.2 attempts automatic restoration of the timestamped pre-update application/config snapshot.

## Runtime commands

```sh
lunarxctl status
lunarxctl doctor
lunarxctl paths
lunarxctl logs
lunarxctl restart
lunarxctl version
```

`lunarxctl update` requires an explicitly extracted new release (`--source /path/to/LunarX-Home-NEW`) and refuses to treat the currently installed code tree as a new update source.

## Primary environment matrix

| Environment | Architecture | Services | Storage/quota | Desktop | Application providers |
| --- | --- | --- | --- | --- | --- |
| Ubuntu native | x86_64 / aarch64 | systemd | native quota only when genuinely supported; logical fallback | native capability-gated path | audited Flatpak + provider-specific system paths |
| Ubuntu in Termux/PRoot | aarch64 first-class | portable `lunarxctl` supervisor; no systemd assumption | persistent directory + logical quota | TigerVNC + noVNC when installed | runtime-gated APT, allow-listed vendor APT, pinned DEB, official Web/PWA, or explicit unavailable |

The release does not bundle third-party application binaries except metadata for a provider whose download remains upstream and integrity-checked at install time. Standard Flatpak is not used inside PRoot merely to inflate compatibility.

## Validation scope

The final source gate contains **60 automated Python tests** before packaging plus Python/JavaScript/shell/JSON/catalog validation. QA Review 1 recorded **356/356** real-Chromium frontend checks across 11 requested phone/tablet/desktop viewports. QA Review 2 recorded **38/38** disposable platform/install/update/rollback checks, including a real 3.1.1→3.1.2 update with user-data/config preservation and a deliberately broken staged update that automatically rolled back.

Physical Android lifecycle/GPU behavior and a dedicated Ubuntu 24.04/26.04 systemd host were not available in this execution environment and are not falsely marked as physically tested.

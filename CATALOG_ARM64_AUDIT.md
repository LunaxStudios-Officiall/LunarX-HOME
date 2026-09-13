# LunarX Home 3.1.2 — ARM64 application catalog audit

Audit date: 2026-09-12  
Catalog entries: **154**  
Provider records: **306**

## Policy

Android/Ubuntu-in-PRoot ARM64 is a first-class but distinct platform target. Standard Flatpak is not used inside PRoot merely to claim compatibility. A phone provider is enabled only when LunarX has a legitimate platform-specific action: a runtime-confirmed Ubuntu package, an allow-listed official vendor repository, an integrity-pinned ARM64 package, an official Web/PWA experience, or the curated administrator package group. Source-only records remain informational and unsupported records expose no Install action. No x86 emulation subsystem is used.

## Final Android/PRoot classification

| Class | Apps | Meaning |
| --- | ---: | --- |
| Actionable | **56** | At least one provider can act when its runtime gate passes |
| Source-only informational | **1** | Upstream source/reference only; no automatic Install |
| Unavailable | **97** | No audited PRoot ARM64 action; explicit reason/source retained |
| **Total** | **154** | Complete partition, no overlap |

Actionable provider types: **36 `apt-system` + 15 `web-pwa` + 3 `vendor-apt` + 1 `pinned-deb` + 1 `admin-apt-group` = 56**.

The 36 APT entries are conditional candidates. The running Ubuntu guest must report the package through `apt-cache`; otherwise the app is not installable on that device/repository configuration.

## Native Ubuntu architecture audit

The catalog retains 151 native-Ubuntu `flatpak-user` records. The prior Flathub audit records 117 entries with an aarch64 build and 34 x86_64-only entries. A PRoot provider is evaluated separately; a native-Ubuntu aarch64 Flatpak record does not automatically become a PRoot provider.

## Official Web/PWA providers (15)

| Application | Catalog ID | Official URL |
| --- | --- | --- |
| Visual Studio Code | `com.visualstudio.code` | https://vscode.dev/ |
| Discord | `com.discordapp.Discord` | https://discord.com/app |
| Spotify | `com.spotify.Client` | https://open.spotify.com/ |
| Telegram | `org.telegram.desktop` | https://web.telegram.org/a/ |
| LocalSend | `org.localsend.localsend_app` | https://web.localsend.org/ |
| Bitwarden | `com.bitwarden.desktop` | https://vault.bitwarden.com/ |
| Stremio | `com.stremio.Stremio` | https://web.stremio.com/ |
| Whatsie | `com.ktechpit.whatsie` | https://web.whatsapp.com/ |
| ZapZap | `com.rtosta.zapzap` | https://web.whatsapp.com/ |
| Zoom | `us.zoom.Zoom` | https://app.zoom.us/wc |
| Slack | `com.slack.Slack` | https://app.slack.com/client |
| Proton Mail | `me.proton.Mail` | https://mail.proton.me/ |
| Arduino IDE v2 | `cc.arduino.IDE2` | https://app.arduino.cc/sketches |
| Postman | `com.getpostman.Postman` | https://web.postman.co/ |
| GeoGebra | `org.geogebra.GeoGebra` | https://www.geogebra.org/calculator |

## Official privileged ARM64 providers added/retained

- Firefox — `mozilla.apt-arm64`, official Mozilla Linux repository path.
- Brave — `brave.apt-arm64`, official Brave Linux repository path.
- DBeaver Community — `dbeaver.apt-arm64`, official DBeaver Debian repository path.
- RustDesk — `rustdesk.deb-arm64-1.4.9`, official ARM64 DEB with packaged expected SHA-256.
- Multimedia codecs — `ubuntu.apt-group`, administrator-managed Ubuntu package group.

Privileged providers are allow-listed in code; changing a catalog ID to an arbitrary repository/provider does not authorize execution.

## Installed-state/action rules

- Web/PWA state is per LunarX user.
- APT/vendor/pinned system packages require administrator authorization and system-level installed-state checks.
- `Open` appears only when a valid launch URL/command exists.
- `Update` appears only when the provider can report/perform a meaningful update.
- `Uninstall` only targets resources owned by that provider/scope.
- Unsupported/source-only entries never expose a misleading automatic Install action.

`catalog/apps.json` remains the machine-readable authority for each provider's source, methods, architecture/platform gates and unsupported reason.

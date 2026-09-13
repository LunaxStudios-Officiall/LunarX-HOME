# LunarX Home 3.1.3 build report

3.1.3 is a field-reliability patch based directly on 3.1.2. It preserves the validated architecture and visual navigation while integrating fixes discovered on the real Android/Termux + Ubuntu PRoot deployment.

## Included fixes

- browser login controls now serialize username/password correctly;
- login-specific HTTP 401 messaging no longer masquerades as session expiry;
- legacy/migrated users receive usable logical quota defaults and shared quota accounting;
- malformed legacy quota rows no longer break metrics;
- PRoot service status is capability-aware instead of reporting fake systemd units as unknown;
- TigerVNC tooling includes `tigervnc-tools`, accepts `tigervncpasswd`, and starts XFCE through DBus when available;
- updater backups use a buffered PRoot-safe copy path and exclude repository/build artifacts;
- APT progress is visible during install/update;
- `termux-update.sh` performs the Android/PRoot update and installs durable Termux-owned supervision/Termux:Boot integration.

## Validation

The release passed 67/67 Python tests, Python/JavaScript/shell syntax checks, release validation, package verification, source checksum verification, and 406/406 Chromium checks across the 11 required phone/tablet/desktop viewports. Physical-device confirmation of the final 3.1.3 Desktop launch still occurs after installation on the Moto G82; no hardware result is claimed before that test.

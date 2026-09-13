# Privacy

LunarX is designed for a private LAN. Passwords are verified through PAM and are not written to the source tree, browser storage, logs, catalog, or release archives. Sessions use an HttpOnly cookie and a CSRF token.

The application catalog is metadata only. Application binaries, user files, photos, videos, backups, and additional-disk contents are not included in releases. Remote catalog icons are optional browser references to Flathub and can be disabled by deployment policy.

# LunarX Home 3.1.3

Field-reliability patch for Android/Termux + Ubuntu PRoot.

## Fixed

- browser login form credential serialization and login-specific 401 messaging;
- migrated personal/shared logical quota defaults and legacy quota parsing;
- TigerVNC password tooling detection and XFCE DBus startup;
- capability-aware portable service status instead of fake systemd unknowns;
- PRoot-safe filtered backups that avoid `sendfile` failures on Git pack objects;
- visible APT progress during dependency installation;
- durable Termux-owned PRoot supervisor and Termux:Boot integration.

## Android / Termux update

```sh
curl -fsSL https://raw.githubusercontent.com/LunaxStudios-Officiall/LunarX-HOME/3.1.3/termux-update.sh | sh
```

Persistent user data and configuration remain under `/opt/lunarx/runtime`.

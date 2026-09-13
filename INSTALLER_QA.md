# Installer QA — LunarX Home 3.1.2

**QA Review 2 result: PASS for the available disposable environment.**

Validated:

- clean Android/PRoot-mode 3.1.2 install starts the real server and passes `/healthz`;
- fresh install prints `LunarX Home is ready`, effective listener and local URL, plus one first-run token;
- an existing configured installation does not receive a new first-run token during update;
- a non-default persisted port survives update and is used by both the server and `lunarxctl`;
- persistent configuration/data roots remain outside the application tree;
- PRoot path does not assume systemd;
- native Ubuntu 24.04 preflight/systemd integration was simulated/static-checked on this non-Ubuntu host;
- portable uninstall stops the managed runtime, removes only the LunarX controller symlink/code tree and preserves data/config/backups.

Not physically exercised: Android handset lifecycle/GPU/vendor background restrictions and a real Ubuntu 24.04/26.04 systemd/PAM/apt host.

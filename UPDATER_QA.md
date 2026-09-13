# Updater / rollback QA — LunarX Home 3.1.2

**QA Review 2 result: PASS.**

A disposable **3.1.1 → 3.1.2** update was executed using the unmodified supplied 3.1.1 release as the installed baseline. Before update, the fixture created an administrator, profile/theme/preferences, a user file and a non-default listener port. After update, all markers remained intact and `/healthz` reported 3.1.2 on the persisted port.

The updater creates a timestamped application/config/install-state backup before changing an existing installation. A controlled broken candidate with an invalid `app.py` was then applied. Compilation failed after the staged code switch; the 3.1.2 rollback path restored the previous application/config/install-state snapshot, restarted it, passed the restored health check and left the persistent user file unchanged.

`lunarxctl update` still requires an explicitly extracted new source and refuses an in-place self-update. `--no-start` remains available for deliberate offline staging and therefore intentionally skips runtime health verification.

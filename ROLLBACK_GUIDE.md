# LunarX Home 3.1.2 rollback guide

For an **existing installation**, the 3.1.2 updater automatically attempts rollback if compile/start/activation/health verification fails. The rollback restores the timestamped pre-update `application`, `config` and `install-state.json`, restarts the previous runtime path and checks the previous version's health endpoint when possible. Persistent user data is not replaced by rollback.

If the installer reports `automatic rollback failed`, keep the failed tree and the printed backup directory. Do not delete the persistent data root. Operator recovery should:

1. stop LunarX;
2. preserve the current failed application/config for incident analysis;
3. inspect the selected timestamped backup for `application/`, `config/` and `install-state.json`;
4. restore through a sibling staging path rather than copying over a running tree;
5. restore service/controller files from the restored application;
6. start LunarX and verify `/healthz`, login, Files/Photos and provider status.

Do not format storage, remove user data or invent a successful rollback when the restored runtime cannot pass health verification.

# Updating to LunarX Home 3.1.2

Use the installer from the **newly extracted 3.1.2 release**:

```sh
sudo ./install.sh update   # native Ubuntu
./install.sh update        # Ubuntu/PRoot
```

Before changing an existing installation, the updater creates a timestamped backup containing the current application tree, persistent configuration and install-state evidence. User files/photos remain in the persistent data root and are never replaced by the code switch.

The new code is copied through a sibling staging directory and atomically promoted. Critical Python entry points are compiled, platform integration is refreshed, LunarX is restarted, and `/healthz` must report 3.1.2 unless `--no-start` is used for deliberate offline staging.

## Automatic recovery

For an existing installation, an activation/compile/start/health failure triggers automatic rollback from the pre-update backup. 3.1.2 restores the previous application, configuration and install-state snapshot, restarts the previous runtime path and checks the restored version's health endpoint when possible. Persistent user data is not rolled back or deleted.

If automatic rollback itself fails, the installer returns a failure and preserves the backup path for operator recovery; it does not claim a successful update. See `ROLLBACK_GUIDE.md`.

## Preserved state

The tested 3.1.1→3.1.2 update preserved:

- administrator/user state;
- profile/theme/preferences;
- persistent configuration including non-default bind/port;
- user file data;
- app/provider state stored outside the application tree;
- backup history.

A configured installation does not receive a new first-run token merely because it was updated.

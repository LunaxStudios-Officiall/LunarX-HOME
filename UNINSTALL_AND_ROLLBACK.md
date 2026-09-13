# Uninstall and rollback — LunarX Home 3.1.2

Uninstall removes the LunarX application code and platform service/controller registration while preserving persistent configuration, user data, account/provider state and backups.

```sh
sudo ./install.sh uninstall   # native Ubuntu
./install.sh uninstall        # Android/PRoot
```

On Android/PRoot, 3.1.2 stops the portable runtime before deleting the application tree and removes `$HOME/.local/bin/lunarxctl` only when it is the symlink owned by this LunarX installation. It does not remove the persistent data/config roots.

Updates automatically attempt rollback for existing installations when activation fails. See `ROLLBACK_GUIDE.md` for the manual recovery path when automatic rollback itself cannot complete.

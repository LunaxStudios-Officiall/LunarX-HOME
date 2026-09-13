# Installation and lifecycle operations

The root `INSTALLATION.md` is the user-facing quick guide. This page records the operational contract.

Run the non-mutating plan first:

```sh
./install.sh install --dry-run
```

Native Ubuntu uses the established FHS roots and can enable `lunarx-home.service`. Android/PRoot stores product state below the persistent app root and uses `lunarxctl`. The installer accepts both x86_64 and aarch64.

All mutating install/update/repair operations create a timestamped backup before changing the active tree. The application tree is staged and replaced as a unit. Data, profiles, accounts, quotas, app registry, themes, server name, catalog metadata, SSH and Tailscale settings are not overwritten. See `MIGRATION_PLAN.md`, `BACKUP_FORMAT_SPEC.md` and `ROLLBACK_GUIDE.md`.

The primary data directory does not need to be a mount. XFS project quota is used only if the provider detects an XFS filesystem with `prjquota`/`pquota`; otherwise the logical quota provider is selected. No additional physical disk is formatted.

For a first install without an existing native admin, the installer prints one `FIRST_RUN_SETUP_TOKEN` in its terminal output. Use it once at `/api/setup`; a successful setup invalidates it.

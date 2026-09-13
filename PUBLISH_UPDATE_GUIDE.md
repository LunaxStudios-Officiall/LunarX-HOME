# Publish/update guide — LunarX Home 3.1.2

Before public publication, resolve/confirm the project license and repository coordinates, then validate the exact packaged archive. Publish only the single clean `LunarX-Home-3.1.2.zip` plus its recorded SHA-256 if desired; never publish first-run setup tokens, backups, user data, test credentials, or the supplied 3.1.1 baseline archive.

For an installed system, extract the newer release and run its `install.sh update`. The installed `lunarxctl update` command also accepts `--source /path/to/newly-extracted-release` and deliberately refuses to treat its own installed code tree as a new release.

For 3.1.1 → 3.1.2, the updater stages a backup of application/configuration state before activation. If the new application fails activation or the post-update health check, the updater restores the prior application/configuration automatically and re-verifies the restored version. Persistent user data remains outside the replaceable application tree.

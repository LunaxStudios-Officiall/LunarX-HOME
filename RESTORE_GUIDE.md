# Restore guide

Stop the service, preserve the failed active tree, inspect the backup timestamp and validate the backup format. Restore application and config into staging paths, run `tools/validate_release.py`, compare the install-state paths, then activate and run `lunarxctl doctor`. Never restore a backup into a user-data root or copy secrets into the release tree.

# Disaster recovery plan

1. Isolate the host or local portal.
2. Preserve logs, install state and the failed code tree.
3. Confirm that user data and account state still exist in their canonical roots.
4. Restore the last verified application/config backup.
5. Re-run provider, quota, account and health checks.
6. If storage is unavailable, keep Home in degraded mode and repair only after a verified backup.

Recovery objective: no code update or uninstall operation removes user data.

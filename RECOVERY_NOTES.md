# LunarX Home 3.1.2 recovery notes

- Keep persistent data/configuration outside the release code tree.
- Every existing-install update creates a timestamped application/config/install-state backup before activation.
- 3.1.2 automatically restores that snapshot if activation/health verification fails and the backup is usable.
- If recovery cannot be completed automatically, preserve both the failed tree and backup for operator review; do not delete or reformat user storage.
- Android/PRoot uses logical quota/persistent directories and must not be treated as if it had native XFS project quotas.
- Additional disks are never formatted automatically.
- After manual recovery, verify `/healthz`, login, Files, Photos, application/provider state and Desktop entry before discarding the previous snapshot.

Physical reboot persistence and real Android/Ubuntu hardware remain field validation boundaries.

# Storage and quotas

The primary pool is an ordinary, writable directory. Directory readiness is not tied to `os.path.ismount`, which is essential for Android/PRoot and also makes a native ext4 deployment honest.

Quota provider selection is capability-based:

| Detected condition | Provider | Enforcement |
| --- | --- | --- |
| XFS with `prjquota`/`pquota` on supported native Ubuntu | native filesystem | xfs project quota plus API checks |
| ext4, Android/PRoot, portable Linux, or unknown mount | logical | bounded directory accounting plus API preflight |
| legacy image mode explicitly selected | XFS image provider | opt-in migration compatibility only |

Uploads, copies, extraction and compression check the projected durable size before writing. The filesystem remains authoritative for native quota mode. Logical quota boundaries are also checked in the broker and are visible in the UI as `logical` rather than being mislabeled XFS.

Additional disks are discovered and registered only with UUID or `/dev/disk/by-id` identities. Registration never formats a disk; offline members remain in the registry with a visible unavailable state.

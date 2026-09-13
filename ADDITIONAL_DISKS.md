# Additional disks

Additional disks are optional and are never part of the primary personal/shared quota pool. An administrator registers an existing XFS, ext4, or Btrfs filesystem by UUID or `/dev/disk/by-id`; `/dev/sdX` alone is rejected. Registration uses `nofail`/automount and never formats the device.

Access is granted and revoked with filesystem ACLs as well as Home API authorization. If the device disappears, only that resource is marked offline; the Home shell, login, primary files, and diagnostics remain available.

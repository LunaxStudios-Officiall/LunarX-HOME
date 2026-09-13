# Compatibility changes

Removed assumptions that the primary data root must be a mount, that a native systemd service always exists, that netlink is available, that quota enforcement must be XFS, or that the CPU must be x86_64. Added explicit capability fields and preserved native defaults for migration compatibility.

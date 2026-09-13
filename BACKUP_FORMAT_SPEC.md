# Backup format specification

A backup is a mode-0700 timestamped directory containing `application/`, `config/` and `install-state.json` when present. It may include symlinks from the existing code tree but never includes user data, credentials, setup tokens, virtual disks or caches unless an operator separately exports them. Restoration must validate paths, JSON, version and permissions before activation.

# System requirements

- Native: Ubuntu Server 24.04 or 26.04 LTS, x86_64 or aarch64.
- Android/PRoot: Termux/PRoot with a writable persistent home; no root required.
- Other Linux: portable/development mode with explicit operator approval.
- Memory: 2 GiB minimum; 4 GiB recommended for a portal plus Desktop provider.
- Native install: root/sudo authority and access to configured Ubuntu repositories. Portable install: Python 3 and the declared runtime dependencies.
- Storage: writable primary directory. Native XFS project quota is optional and detected; logical quotas are the fallback.
- Network: only required for packages, remote catalog sources, Flathub, Tailscale or the operator's reverse proxy.

Additional filesystems must already contain a supported filesystem and are registered by stable UUID/by-id identity. LunarX never formats them automatically.

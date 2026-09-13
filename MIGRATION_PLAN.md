# Migration plan

1. Snapshot the existing application tree and configuration into the configured backup root.
2. Inventory the canonical data roots, profile directories, account metadata, quotas, app registry, themes, server name, catalog, SSH and Tailscale settings.
3. Resolve legacy roots without assuming they are mounted. Preserve files and photos in place.
4. Convert an XFS-only quota assumption to `native-filesystem` only when options prove project quota support; otherwise write logical quota metadata.
5. Reconcile installed apps into the durable registry without claiming that a provider is available.
6. Stage the new code, run validation, switch the active tree, and write `install-state.json`.
7. Verify `/healthz`, `lunarxctl doctor`, account login, storage access and provider capability reports on the target host.

The installer does not delete tier links, format additional disks, overwrite Termux boot hooks or remove user data. Any manual legacy cleanup is a separately reviewed operation.

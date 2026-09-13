# Migration

Migration is data-preserving and staged. Existing files, photos, shared content, profiles, accounts, quota values, installed-app registry, settings, themes, server name, catalog metadata and host SSH/Tailscale configuration remain outside the code switch.

Legacy roots recognized by the installer and provider path model include `/opt/lunarx-home`, `/srv/lunarx-data`, `/etc/lunarx-home`, `/var/lib/lunarx-home`, `/run/lunarx-home` and the older Android `/opt/lunarx/runtime` layout. A legacy Workspace tier is not copied into a new storage tier and no broken link is deleted automatically.

Use `MIGRATION_PLAN.md`, `MIGRATION_MAP.md` and `MIGRATION_REPORT.md` for the exact disposition and evidence fields.

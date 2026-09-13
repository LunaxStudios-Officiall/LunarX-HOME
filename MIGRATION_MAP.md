# Migration map

| Legacy/source | 3.0 disposition | Verification |
| --- | --- | --- |
| `/opt/lunarx-home` | staged code target; preserve as backup | install-state + manifest |
| `/srv/lunarx-data` | canonical native data root | writable path + file counts |
| `/etc/lunarx-home` | canonical config/state root | JSON parse + permissions |
| `/var/lib/lunarx-home` | canonical state/runtime metadata | state parse + backups |
| `/run/lunarx-home` | runtime-only activity/tickets | recreated at boot |
| Android `/opt/lunarx/runtime` | source for reviewed portable mapping | explicit operator review |
| XFS project records | preserve values where usable | provider capability report |
| app registry/catalog | preserve metadata and annotate availability | catalog validator |
| SSH/Tailscale settings | host-owned; never copied into release | exclusion scan |
| legacy cold-tier links | report only; no automatic deletion | migration report |

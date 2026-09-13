# LunarX Home 3.1.2 migration report

Base version: **3.1.1**  
Target version: **3.1.2**  
Migration type: compatible hardening patch; no architecture rewrite and no destructive user-data migration.

The 3.1.1 persistent state format remains compatible. During update, `config.json`/state schema metadata is advanced to 3.1.2 while existing users, password hashes, files, photos, quotas, storage registrations, themes/profile preferences, installed provider state and custom bind/port values are preserved. No user-data table/key is intentionally removed.

Catalog provider schema remains 3.0.0. The provider records are refined/audited, but this is packaged release metadata rather than destructive persistent user state.

3.1.2 adds safer updater behavior rather than a data migration: an existing installation is backed up before activation and automatically restored if activation/health verification fails. A disposable 3.1.1→3.1.2 update fixture confirmed administrator/profile/theme/config and user-file preservation.

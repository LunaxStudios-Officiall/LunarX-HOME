# Third-party and licensing audit

Audit date: 2026-09-12.

| Component | Distribution in package | License/source status |
| --- | --- | --- |
| LunarX-owned Python/HTML/CSS/JS | Source | Project license unresolved; no grant asserted. |
| xterm.js and xterm.css | Vendored source | Notice retained in `static/vendor/XTERM-LICENSE.txt`; upstream license applies. |
| LunarX logo PNG | Vendored asset | Provenance supplied by the existing deployment; ownership/license not independently documented. Treat as LunarX-internal pending owner confirmation. |
| Python cryptography and Pillow | Dependency metadata only | Installed from Ubuntu/Python sources; not vendored. |
| nginx, XFS tools, XFCE, XRDP, Docker, Flatpak | Package names only | Installed from configured Ubuntu repositories; upstream licenses apply. |
| PostgreSQL and Apache Guacamole containers | Image references only | Pulled at install time; images are not bundled. |
| Flathub applications | Metadata only | Each catalog entry records upstream license metadata; binaries and icons are not bundled. Remote icon URLs are references. |
| VS Code | Metadata only | Microsoft product terms; no proprietary binary or signing key is bundled. |
| Hermes Agent | Link/metadata only | Official source reports MIT; automatic installer disabled until a pinned artifact hash exists. |
| Google Cloud Code | Link/extension ID only | Official Google source; presented as an IDE extension. |

Release blocker: choose and document the LunarX project license and logo provenance before public publication.

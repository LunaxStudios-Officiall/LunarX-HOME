# Catalog sources and policy

The checked-in catalog is a metadata snapshot. LunarX does not redistribute the listed applications.

- The broad catalog is derived from the official Flathub AppStream-backed popular collection and restricted to desktop applications advertising `x86_64` support.
- Application installation uses the app ID from the local allow-list and Flathub's configured repository. User input never becomes a command name or package source.
- Visual Studio Code is identified as `com.visualstudio.code`; its product terms apply. LunarX bundles neither the Microsoft binary nor repository key.
- Hermes Agent points to the official `NousResearch/hermes-agent` source. Automatic installation is disabled until an immutable release artifact and SHA-256 are pinned.
- Google Cloud Code is `GoogleCloudTools.cloudcode`, an extension for VS Code documented by Google. It is not Claude Code and is not presented as a standalone desktop app.
- Multimedia codecs are a global administrator-only Ubuntu package group. Availability, patents, and regional rules vary.
- “Skayo” is excluded because no authoritative product identity was verified for that exact spelling.

Refreshes must retain the previous snapshot until the downloaded document validates, contains at least 150 unique applications, and passes the license/source completeness checks.

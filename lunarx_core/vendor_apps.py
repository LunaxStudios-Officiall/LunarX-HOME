"""Allow-listed third-party ARM64 providers used by LunarX on Android/PRoot.

Only providers audited for 3.1.2 live here. Catalog metadata can reference these
IDs, but arbitrary repository/deb definitions from catalog JSON are never
executed. This keeps privileged package setup constrained to reviewed upstream
sources.
"""

from __future__ import annotations

VENDOR_APT = {
    "mozilla.apt-arm64": {
        "package": "firefox",
        "key_url": "https://packages.mozilla.org/apt/repo-signing-key.gpg",
        "key_path": "/etc/apt/keyrings/packages.mozilla.org.asc",
        "fingerprint": "35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3",
        "source_path": "/etc/apt/sources.list.d/lunarx-mozilla.list",
        "source_content": "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main\n",
        "preferences_path": "/etc/apt/preferences.d/lunarx-mozilla",
        "preferences_content": "Package: firefox\nPin: release o=Ubuntu\nPin-Priority: -1\n\nPackage: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n",
    },
    "brave.apt-arm64": {
        "package": "brave-browser",
        "key_url": "https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg",
        "key_path": "/usr/share/keyrings/brave-browser-archive-keyring.gpg",
        "source_url": "https://brave-browser-apt-release.s3.brave.com/brave-browser.sources",
        "source_path": "/etc/apt/sources.list.d/brave-browser-release.sources",
    },
    "dbeaver.apt-arm64": {
        "package": "dbeaver-ce",
        "key_url": "https://dbeaver.io/debs/dbeaver.gpg.key",
        "key_path": "/usr/share/keyrings/lunarx-dbeaver.gpg",
        "dearmor": True,
        "source_path": "/etc/apt/sources.list.d/lunarx-dbeaver.list",
        "source_content": "deb [signed-by=/usr/share/keyrings/lunarx-dbeaver.gpg] https://dbeaver.io/debs/dbeaver-ce /\n",
    },
}

PINNED_DEB = {
    "rustdesk.deb-arm64-1.4.9": {
        "package": "rustdesk",
        "version": "1.4.9",
        "url": "https://github.com/rustdesk/rustdesk/releases/download/1.4.9/rustdesk-1.4.9-aarch64.deb",
        "sha256": "ce62c996f14d33f3bbe3a330e953644a44bace7f05885a7953f7395d69fb49c0",
    },
}

VENDOR_APT_IDS = frozenset(VENDOR_APT)
PINNED_DEB_IDS = frozenset(PINNED_DEB)

__all__ = ["PINNED_DEB", "PINNED_DEB_IDS", "VENDOR_APT", "VENDOR_APT_IDS"]

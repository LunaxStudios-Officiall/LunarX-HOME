"""Read-only, schema-checked application catalog access.

LunarX Home 3.1.1 keeps the legacy top-level backend fields for migration
compatibility, while provider records describe the real execution path for a
specific platform and architecture. Compatibility is resolved against those
provider records instead of assuming one backend fits every host.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
import json
import re
from urllib.parse import urlparse

from .vendor_apps import PINNED_DEB_IDS, VENDOR_APT_IDS


APP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,190}$")
ALLOWED_BACKENDS = {"flatpak-user", "ide-extension", "verified-source", "admin-apt-group"}
ALLOWED_PROVIDERS = ALLOWED_BACKENDS | {"apt-system", "web-pwa", "vendor-apt", "pinned-deb", "unsupported"}
PROVIDER_STATUSES = {
    "Native ARM64",
    "Native x86_64",
    "Native Linux",
    "Web/PWA fallback",
    "Available through compatible provider",
    "Source only",
    "Unavailable on this platform",
}


class CatalogError(ValueError):
    pass


SCHEMA_VERSION = "3.0.0"
SUPPORTED_PLATFORMS = {"native-ubuntu", "android-proot", "portable-linux", "unknown", "all"}
SUPPORTED_ARCHITECTURES = {"x86_64", "aarch64", "arm64", "amd64", "all"}


def normalize_architecture(value: str) -> str:
    raw = str(value or "unknown").lower()
    return {"amd64": "x86_64", "x86-64": "x86_64", "arm64": "aarch64"}.get(raw, raw)


def _http_url(value: object, *, field: str) -> str:
    url = str(value or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise CatalogError(f"{field} must use an http(s) URL")
    return url


def _validate_provider(provider: dict[str, Any]) -> None:
    provider_id = str(provider.get("id", ""))
    if not APP_ID_RE.fullmatch(provider_id):
        raise CatalogError("invalid application provider id")
    kind = str(provider.get("provider", ""))
    if kind not in ALLOWED_PROVIDERS:
        raise CatalogError("unsupported application provider")
    platforms = provider.get("platforms", [])
    architectures = provider.get("architectures", [])
    if not isinstance(platforms, list) or not platforms or any(str(value) not in SUPPORTED_PLATFORMS for value in platforms):
        raise CatalogError("invalid provider platform metadata")
    if not isinstance(architectures, list) or not architectures or any(str(value) not in SUPPORTED_ARCHITECTURES for value in architectures):
        raise CatalogError("invalid provider architecture metadata")
    status = str(provider.get("compatibility_status", ""))
    if status and status not in PROVIDER_STATUSES:
        raise CatalogError("invalid provider compatibility status")
    if kind == "web-pwa":
        _http_url(provider.get("launch_url"), field="web/PWA launch URL")
    if kind in {"apt-system", "admin-apt-group", "vendor-apt", "pinned-deb"}:
        packages = provider.get("packages", [])
        if not isinstance(packages, list) or not packages or any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", str(item)) for item in packages):
            raise CatalogError("invalid apt/deb provider package list")
    if kind == "vendor-apt" and provider_id not in VENDOR_APT_IDS:
        raise CatalogError("unknown allow-listed vendor APT provider")
    if kind == "pinned-deb" and provider_id not in PINNED_DEB_IDS:
        raise CatalogError("unknown allow-listed pinned DEB provider")
    if provider.get("source"):
        _http_url(provider.get("source"), field="provider source")


@lru_cache(maxsize=4)
def load_catalog(path: str) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") not in {"1.0.0", "2.0.0", SCHEMA_VERSION} or not isinstance(document.get("apps"), list):
        raise CatalogError("unsupported application catalog")
    seen: set[str] = set()
    for item in document["apps"]:
        if not isinstance(item, dict):
            raise CatalogError("invalid catalog entry")
        app_id = str(item.get("id", ""))
        if not APP_ID_RE.fullmatch(app_id) or app_id in seen:
            raise CatalogError("invalid or duplicate application id")
        if item.get("backend") not in ALLOWED_BACKENDS:
            raise CatalogError("unsupported application backend")
        if not item.get("name") or not item.get("source_url") or not item.get("license"):
            raise CatalogError("application metadata is incomplete")
        _http_url(item.get("source_url"), field="application source")
        platforms = item.get("platforms", ["native-ubuntu"])
        architectures = item.get("architectures", ["all"])
        if not isinstance(platforms, list) or not platforms or any(str(value) not in SUPPORTED_PLATFORMS for value in platforms):
            raise CatalogError("invalid application platform metadata")
        if not isinstance(architectures, list) or not architectures or any(str(value) not in SUPPORTED_ARCHITECTURES for value in architectures):
            raise CatalogError("invalid application architecture metadata")
        if item.get("icon_url"):
            _http_url(item.get("icon_url"), field="application icon")
        providers = item.get("providers")
        if providers is not None:
            if not isinstance(providers, list) or not providers:
                raise CatalogError("application providers must be a non-empty list")
            provider_ids: set[str] = set()
            for provider in providers:
                if not isinstance(provider, dict):
                    raise CatalogError("invalid application provider record")
                _validate_provider(provider)
                provider_id = str(provider["id"])
                if provider_id in provider_ids:
                    raise CatalogError("duplicate application provider id")
                provider_ids.add(provider_id)
        seen.add(app_id)
    return document


def find_app(path: str, app_id: str) -> dict[str, Any]:
    if not APP_ID_RE.fullmatch(app_id):
        raise CatalogError("invalid application id")
    for item in load_catalog(path)["apps"]:
        if item["id"] == app_id:
            return item
    raise CatalogError("application is not in the verified catalog")


def providers_for(item: dict[str, Any]) -> list[dict[str, Any]]:
    providers = item.get("providers")
    if isinstance(providers, list) and providers:
        return [dict(provider) for provider in providers if isinstance(provider, dict)]
    # Migration path for pre-3.1.1 catalogs.
    return [{
        "id": f"legacy.{item.get('backend', 'provider')}",
        "provider": str(item.get("backend", "verified-source")),
        "provider_type": "native",
        "platforms": list(item.get("platforms", ["native-ubuntu"])),
        "architectures": list(item.get("architectures", ["all"])),
        "install_method": str(item.get("backend", "manual")),
        "launch_method": str(item.get("launch_mode", "external")),
        "update_method": str(item.get("backend", "manual")),
        "uninstall_method": str(item.get("backend", "manual")),
        "source": str(item.get("source_url", "")),
        "scope": "system" if item.get("admin_only") else "user",
        "requires_admin": bool(item.get("admin_only")),
        "compatibility_status": "Source only" if item.get("installable") is False else "Available through compatible provider",
        "reason": str(item.get("availability_note") or "legacy provider metadata"),
        "priority": 50,
    }]


def find_provider(item: dict[str, Any], provider_id: str) -> dict[str, Any]:
    for provider in providers_for(item):
        if str(provider.get("id")) == str(provider_id):
            return provider
    raise CatalogError("application provider is not in the verified catalog")


def _platform_match(provider: dict[str, Any], platform_name: str) -> bool:
    platforms = [str(value) for value in provider.get("platforms", [])]
    return "all" in platforms or platform_name in platforms


def _architecture_match(provider: dict[str, Any], architecture: str) -> bool:
    current = normalize_architecture(architecture)
    allowed = {normalize_architecture(str(value)) for value in provider.get("architectures", [])}
    return "all" in allowed or current in allowed


def _provider_runtime_available(
    provider: dict[str, Any],
    *,
    backend: str | None,
    package_probe: Callable[[str], bool] | None,
) -> tuple[bool, str]:
    kind = str(provider.get("provider", ""))
    if kind == "unsupported":
        return False, str(provider.get("reason") or "provider is explicitly unsupported")
    if kind in {"flatpak-user", "ide-extension"}:
        return (backend == "flatpak-user", "Flatpak user provider is not available on this host")
    if kind in {"apt-system", "admin-apt-group"}:
        if package_probe is None:
            return False, "APT package availability could not be verified"
        missing = [str(package) for package in provider.get("packages", []) if not package_probe(str(package))]
        return (not missing, "APT package is unavailable in configured repositories" if missing else "available")
    if kind == "vendor-apt":
        return (str(provider.get("id")) in VENDOR_APT_IDS, "official vendor APT provider is allow-listed")
    if kind == "pinned-deb":
        return (str(provider.get("id")) in PINNED_DEB_IDS, "official ARM64 DEB artifact is checksum-pinned")
    if kind == "web-pwa":
        return True, "available"
    if kind == "verified-source":
        return bool(provider.get("automatic", False)), str(provider.get("reason") or "verified source is informational only")
    return False, "provider is unavailable"


def compatibility(
    item: dict[str, Any],
    *,
    platform_name: str,
    architecture: str,
    backend: str | None = None,
    package_probe: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Resolve the best real provider for this platform/architecture.

    The result intentionally exposes the selected provider so both the UI and
    the privileged/broker execution path operate on the same allow-listed
    provider record.
    """
    providers = providers_for(item)
    matching_platform = [provider for provider in providers if _platform_match(provider, platform_name)]
    matching_arch = [provider for provider in matching_platform if _architecture_match(provider, architecture)]
    candidates: list[tuple[int, dict[str, Any]]] = []
    runtime_notes: list[str] = []
    for provider in matching_arch:
        available, note = _provider_runtime_available(provider, backend=backend, package_probe=package_probe)
        if available:
            candidates.append((int(provider.get("priority", 50)), provider))
        elif provider.get("provider") != "unsupported":
            runtime_notes.append(note)
    candidates.sort(key=lambda value: value[0])
    selected = dict(candidates[0][1]) if candidates else None
    installable = bool(item.get("installable", True)) and selected is not None
    if selected:
        reason = str(selected.get("reason") or "compatible provider selected")
        status = str(selected.get("compatibility_status") or "Available through compatible provider")
    else:
        explicit = next((provider for provider in matching_arch if provider.get("provider") == "unsupported"), None)
        if explicit:
            reason = str(explicit.get("reason") or "unavailable on this platform")
        elif not matching_platform:
            reason = f"no audited provider for platform {platform_name}"
        elif not matching_arch:
            reason = f"no audited provider for architecture {normalize_architecture(architecture)}"
        elif runtime_notes:
            reason = runtime_notes[0]
        else:
            reason = str(item.get("availability_note") or "no verified provider is available")
        status = "Unavailable on this platform"
    if item.get("installable") is False:
        installable = False
        status = "Source only"
        if selected is None:
            reason = str(item.get("notes") or item.get("availability_note") or "automatic installation is unavailable")
    return {
        "compatible": installable,
        "platform": bool(matching_platform),
        "architecture": bool(matching_arch),
        "provider": selected is not None,
        "backend": str(selected.get("provider")) if selected else (backend or "unavailable"),
        "catalog_backend": str(item.get("backend", "")),
        "reason": reason,
        "status": status,
        "selected_provider": selected,
        "provider_count": len(providers),
    }


__all__ = [
    "ALLOWED_PROVIDERS",
    "CatalogError",
    "SCHEMA_VERSION",
    "compatibility",
    "find_app",
    "find_provider",
    "load_catalog",
    "normalize_architecture",
    "providers_for",
]

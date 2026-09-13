"""Small provider facades shared by the UI, broker and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import shutil
import subprocess

from .paths import PathConfig, resolve_paths
from .platform import PlatformInfo, detect_platform
from .quota import LogicalQuotaProvider, QuotaProvider, select_provider
from .storage import primary_storage_ready


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    provider: str
    state: str
    message: str = ""
    data: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {"ok": self.ok, "provider": self.provider, "state": self.state, "message": self.message, **(self.data or {})}


class StorageProvider:
    def __init__(self, paths: PathConfig, info: PlatformInfo) -> None:
        self.paths, self.info = paths, info
        self.quota: QuotaProvider = select_provider(info.filesystem, set(info.mount_options), "auto")

    def status(self) -> ProviderResult:
        try:
            usage = shutil.disk_usage(self.paths.data_root)
            data = {"root": str(self.paths.data_root), "total_bytes": usage.total, "free_bytes": usage.free, "used_bytes": usage.total - usage.free, "ready": primary_storage_ready(self.paths.data_root), "quota_mode": self.info.quota_mode}
        except OSError as exc:
            return ProviderResult(False, self.quota.name, "unavailable", "storage provider could not be inspected", {"detail": str(exc)[:120]})
        return ProviderResult(bool(data["ready"]), self.quota.name, "ready" if data["ready"] else "unavailable", data=data)


class ServiceManagerProvider:
    def __init__(self, info: PlatformInfo) -> None:
        self.info = info

    def status(self, units: list[str]) -> ProviderResult:
        if self.info.service_manager != "systemd":
            return ProviderResult(False, self.info.service_manager, "unavailable", "service manager is not available in this environment", {"units": [{"unit": unit, "state": "unknown"} for unit in units]})
        output = []
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return ProviderResult(False, "systemd", "unavailable", "systemctl is not installed")
        for unit in units:
            try:
                result = subprocess.run([systemctl, "is-active", "--quiet", unit], check=False, timeout=8)
                output.append({"unit": unit, "state": "active" if result.returncode == 0 else "inactive"})
            except (OSError, subprocess.SubprocessError):
                output.append({"unit": unit, "state": "unknown"})
        return ProviderResult(True, "systemd", "ready", data={"units": output})


class DesktopProvider:
    def __init__(self, info: PlatformInfo) -> None:
        self.info = info

    def status(self) -> ProviderResult:
        if not self.info.desktop_available:
            return ProviderResult(False, self.info.desktop_backend, "unavailable", "desktop session backend is not installed or not configured")
        return ProviderResult(True, self.info.desktop_backend, "ready", "desktop session backend detected")


class AppProvider:
    def __init__(self, info: PlatformInfo) -> None:
        self.info = info

    def status(self) -> ProviderResult:
        return ProviderResult(self.info.app_backend != "registry-only", self.info.app_backend, "ready" if self.info.app_backend != "registry-only" else "metadata-only", "application actions are capability-gated")


def provider_snapshot(paths: PathConfig | None = None) -> dict[str, Any]:
    config = paths or resolve_paths()
    info = detect_platform(paths=config)
    return {"storage": StorageProvider(config, info).status().public(), "services": ServiceManagerProvider(info).status(["lunarx-home.service"]).public(), "desktop": DesktopProvider(info).status().public(), "apps": AppProvider(info).status().public()}


__all__ = ["AppProvider", "DesktopProvider", "ProviderResult", "ServiceManagerProvider", "StorageProvider", "provider_snapshot"]

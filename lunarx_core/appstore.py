"""Application-store orchestration with platform/provider compatibility gating."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
import json
import re
import secrets
import shutil
import subprocess
import threading
import time

from .catalog import CatalogError, compatibility, find_app, load_catalog


ALLOWED_OPERATIONS = {"install", "launch", "update", "uninstall"}


@dataclass
class AppJob:
    id: str
    username: str
    app_id: str
    operation: str
    state: str = "queued"
    progress: int = 0
    message: str = "Queued"
    created_at: int = 0
    updated_at: int = 0
    result: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return asdict(self)


class AppJobRegistry:
    def __init__(self, max_jobs: int = 256) -> None:
        self._jobs: dict[str, AppJob] = {}
        self._lock = threading.RLock()
        self._max_jobs = max_jobs

    def create(self, username: str, app_id: str, operation: str) -> AppJob:
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError("unsupported application operation")
        now = int(time.time())
        job = AppJob(secrets.token_urlsafe(18), username, app_id, operation, created_at=now, updated_at=now)
        with self._lock:
            self._jobs[job.id] = job
            if len(self._jobs) > self._max_jobs:
                oldest = sorted(self._jobs.values(), key=lambda item: item.updated_at)[: len(self._jobs) - self._max_jobs]
                for item in oldest:
                    self._jobs.pop(item.id, None)
        return job

    def update(self, job_id: str, *, state: str, progress: int, message: str, result: dict[str, Any] | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.state = state
            job.progress = max(0, min(100, progress))
            job.message = message[:240]
            if isinstance(result, dict):
                # Never echo unbounded helper output into the browser job registry.
                job.result = {str(key)[:80]: value for key, value in result.items() if key in {"launch_url", "provider", "scope", "version", "message"}}
            job.updated_at = int(time.time())

    def for_user(self, username: str, job_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            values = [job for job in self._jobs.values() if job.username == username and (not job_id or job.id == job_id)]
            return [job.public() for job in sorted(values, key=lambda item: item.created_at, reverse=True)]


@lru_cache(maxsize=256)
def apt_package_available(package: str) -> bool:
    """Return True only when the configured APT repositories expose a candidate.

    Catalog metadata may name an Ubuntu package, but the Install button is not
    enabled until the *current host* confirms that package through apt-cache.
    This keeps PRoot and distro-version differences truthful.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package):
        return False
    binary = shutil.which("apt-cache")
    if not binary:
        return False
    try:
        result = subprocess.run([binary, "policy", package], check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode not in {0, 100}:
        return False
    match = re.search(r"^\s*Candidate:\s*(\S+)\s*$", result.stdout, re.MULTILINE)
    return bool(match and match.group(1) != "(none)")


def catalog_path(config_root: Path, source_root: Path) -> Path:
    configured = ""
    try:
        document = json.loads((config_root / "config.json").read_text(encoding="utf-8"))
        configured = str(document.get("app_store", {}).get("catalog", ""))
    except (OSError, ValueError, TypeError):
        pass
    candidate = Path(configured) if configured else source_root / "catalog" / "apps.json"
    return candidate if candidate.is_file() else source_root / "catalog" / "apps.json"


def public_catalog(path: Path, *, query: str = "", category: str = "all") -> dict[str, Any]:
    document = load_catalog(str(path))
    needle = query.casefold().strip()
    apps = []
    for item in document["apps"]:
        if category != "all" and item.get("category") != category:
            continue
        haystack = " ".join(str(item.get(key, "")) for key in ("name", "summary", "developer", "id", "category")).casefold()
        if needle and needle not in haystack:
            continue
        apps.append(dict(item))
    return {
        "schema_version": document["schema_version"],
        "generated_at": document.get("generated_at"),
        "source": document.get("source"),
        "audit": document.get("audit"),
        "count": len(apps),
        "apps": apps,
    }


def annotate_catalog(
    document: dict[str, Any],
    *,
    platform_name: str,
    architecture: str,
    backend: str | None = None,
    package_probe: Callable[[str], bool] | None = apt_package_available,
) -> dict[str, Any]:
    result = dict(document)
    output: list[dict[str, Any]] = []
    for item in document.get("apps", []):
        entry = dict(item)
        state = compatibility(entry, platform_name=platform_name, architecture=architecture, backend=backend, package_probe=package_probe)
        entry["compatibility"] = state
        entry["selected_provider"] = state.get("selected_provider")
        entry["compatible"] = bool(state["compatible"])
        entry["installable_here"] = bool(entry.get("installable", True)) and bool(state["compatible"])
        entry["compatibility_status"] = state.get("status", "Unavailable on this platform")
        entry["unavailable_reason"] = "" if state["compatible"] else str(state.get("reason") or "unavailable on this device")
        output.append(entry)
    result["apps"] = output
    result["count"] = len(output)
    return result


def start_job(
    registry: AppJobRegistry,
    catalog: Path,
    username: str,
    app_id: str,
    operation: str,
    helper: Callable[[dict[str, Any]], tuple[bool, dict[str, Any]]],
    *,
    is_admin: bool,
    platform_name: str,
    architecture: str,
    backend: str | None,
    package_probe: Callable[[str], bool] | None = apt_package_available,
) -> AppJob:
    app = find_app(str(catalog), app_id)
    if app.get("admin_only") and not is_admin:
        raise PermissionError("this application requires an administrator")
    state = compatibility(app, platform_name=platform_name, architecture=architecture, backend=backend, package_probe=package_probe)
    provider = state.get("selected_provider") if isinstance(state.get("selected_provider"), dict) else None
    if operation != "launch" and not state["compatible"]:
        raise ValueError("application unavailable on this device: " + str(state.get("reason") or "provider incompatibility"))
    if app.get("installable") is False and operation != "launch":
        raise ValueError("this catalog entry is informational and cannot be installed automatically")
    if not provider:
        raise ValueError("application has no verified provider for this device")
    if bool(provider.get("requires_admin")) and not is_admin:
        raise PermissionError("the selected application provider requires an administrator")
    job = registry.create(username, app_id, operation)

    def run() -> None:
        registry.update(job.id, state="running", progress=10, message="Validating application provider")
        ok, result = helper({
            "action": "user_app",
            "username": username,
            "app_id": app_id,
            "operation": operation,
            "provider_id": provider.get("id"),
            # Kept as a compatibility hint; helpers re-read the provider from the catalog.
            "backend": provider.get("provider"),
        })
        if ok:
            registry.update(job.id, state="complete", progress=100, message=str(result.get("message", "Completed")), result=result)
        else:
            registry.update(job.id, state="failed", progress=100, message=str(result.get("error", "Application action failed")), result=result)

    threading.Thread(target=run, name=f"lunarx-app-{job.id[:8]}", daemon=True).start()
    return job


def installed_for_user(helper: Callable[[dict[str, Any]], tuple[bool, dict[str, Any]]], username: str) -> dict[str, Any]:
    ok, result = helper({"action": "user_app_list", "username": username})
    if not ok:
        return {
            "available": False,
            "apps": [],
            "backend": result.get("backend", "unavailable"),
            "error": str(result.get("error") or "application provider unavailable"),
            "error_code": str(result.get("error_code") or "APP_PROVIDER_UNAVAILABLE"),
        }
    apps = result.get("apps", [])
    return {
        "available": True,
        "apps": apps if isinstance(apps, list) else [],
        "backend": result.get("backend", result.get("provider", "unknown")),
        "providers": result.get("providers", []),
    }


__all__ = [
    "AppJobRegistry",
    "CatalogError",
    "annotate_catalog",
    "apt_package_available",
    "catalog_path",
    "installed_for_user",
    "public_catalog",
    "start_job",
]

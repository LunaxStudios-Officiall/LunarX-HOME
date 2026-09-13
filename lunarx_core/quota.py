"""Portable quota providers with honest native and logical modes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
import os
import re


PROJECT_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class QuotaError(ValueError):
    pass


class QuotaProvider(Protocol):
    name: str

    def limit_commands(self, mount: Path, project: str, hard_gib: int) -> list[list[str]]: ...


def directory_size(path: Path) -> int:
    """Count regular files without traversing symlinked trees."""
    total = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in files:
                item = Path(root) / name
                if item.is_symlink():
                    continue
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def quota_status(root: Path, quota_bytes: int) -> dict[str, int | bool]:
    used = directory_size(root)
    return {"used_bytes": used, "quota_bytes": max(0, int(quota_bytes)), "free_bytes": max(int(quota_bytes) - used, 0), "within_limit": quota_bytes <= 0 or used <= quota_bytes}


def quota_check(root: Path, quota_bytes: int, additional_bytes: int = 0) -> bool:
    if quota_bytes <= 0:
        return True
    return directory_size(root) + max(0, int(additional_bytes)) <= quota_bytes


def _validate(project: str, hard_gib: int) -> None:
    if not PROJECT_RE.fullmatch(project):
        raise QuotaError("invalid quota project")
    if hard_gib < 1 or hard_gib > 1_000_000:
        raise QuotaError("invalid quota limit")


@dataclass(frozen=True)
class XfsProjectQuotaProvider:
    name: str = "xfs-project"

    def limit_commands(self, mount: Path, project: str, hard_gib: int) -> list[list[str]]:
        _validate(project, hard_gib)
        return [
            ["/usr/sbin/xfs_quota", "-x", "-c", f"project -s {project}", str(mount)],
            [
                "/usr/sbin/xfs_quota",
                "-x",
                "-c",
                f"limit -p bsoft={hard_gib}g bhard={hard_gib}g {project}",
                str(mount),
            ],
        ]


@dataclass(frozen=True)
class XfsImageQuotaProvider(XfsProjectQuotaProvider):
    image: Path = Path("/var/lib/lunarx-storage/primary.xfs")
    name: str = "xfs-image-project"


@dataclass(frozen=True)
class LogicalQuotaProvider:
    """Quota accounting for ext4, Android/PRoot and ordinary directories."""

    name: str = "logical"

    def limit_commands(self, mount: Path, project: str, hard_gib: int) -> list[list[str]]:
        _validate(project, hard_gib)
        return []

    def status(self, root: Path, hard_gib: int) -> dict[str, int | bool]:
        return quota_status(root, hard_gib * 1024**3)


def select_provider(filesystem: str, mount_options: set[str], configured: str = "auto") -> QuotaProvider:
    if configured not in {"auto", "logical", "xfs-project", "xfs-image-project"}:
        raise QuotaError("unsupported quota provider")
    native = filesystem == "xfs" and bool({"prjquota", "pquota"} & mount_options)
    if configured == "xfs-project" and not native:
        raise QuotaError("native XFS project quotas are not active")
    if configured == "xfs-project" or (configured == "auto" and native):
        return XfsProjectQuotaProvider()
    if configured == "xfs-image-project":
        return XfsImageQuotaProvider()
    return LogicalQuotaProvider()


__all__ = ["LogicalQuotaProvider", "QuotaError", "QuotaProvider", "XfsImageQuotaProvider", "XfsProjectQuotaProvider", "directory_size", "quota_check", "quota_status", "select_provider"]

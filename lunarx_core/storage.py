"""Storage planning and stable additional-disk identities.

This module deliberately contains no formatting operation. Installation code may
create the explicitly selected same-disk quota image, but an additional physical
disk is only discovered and registered after an administrator chooses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import re
import shutil
import subprocess


GIB = 1024**3
STABLE_ID_RE = re.compile(r"^(?:UUID=[A-Fa-f0-9-]{8,}|/dev/disk/by-id/[A-Za-z0-9._:+-]+)$")


class StoragePlanError(ValueError):
    """A requested storage plan is unsafe or incomplete."""


@dataclass(frozen=True)
class CapacityPlan:
    filesystem_bytes: int
    free_bytes: int
    reserve_bytes: int
    allocatable_bytes: int
    requested_bytes: int

    @property
    def accepted(self) -> bool:
        return 0 < self.requested_bytes <= self.allocatable_bytes


def capacity_plan(
    path: Path,
    requested_bytes: int,
    *,
    reserve_gib: int = 20,
    reserve_percent: int = 20,
) -> CapacityPlan:
    usage = shutil.disk_usage(path)
    reserve = max(reserve_gib * GIB, usage.total * reserve_percent // 100)
    allocatable = max(0, usage.free - reserve)
    return CapacityPlan(usage.total, usage.free, reserve, allocatable, requested_bytes)


def validate_capacity(plan: CapacityPlan) -> None:
    if plan.requested_bytes <= 0:
        raise StoragePlanError("requested capacity must be positive")
    if not plan.accepted:
        raise StoragePlanError(
            f"requested capacity exceeds the safe allocation ({plan.allocatable_bytes} bytes available after OS reserve)"
        )


def stable_disk_id(value: str) -> str:
    candidate = value.strip()
    if not STABLE_ID_RE.fullmatch(candidate):
        raise StoragePlanError("disk identity must be a filesystem UUID or /dev/disk/by-id path")
    return candidate


def discover_block_devices() -> list[dict[str, Any]]:
    binary = shutil.which("lsblk")
    if not binary:
        return []
    try:
        result = subprocess.run(
        [
            binary,
            "--json",
            "--bytes",
            "--output",
            "NAME,PATH,SIZE,TYPE,FSTYPE,FSAVAIL,FSUSE%,LABEL,UUID,MOUNTPOINTS,MODEL,TRAN,RM,ROTA,RO",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if result.returncode:
        return []
    try:
        document = json.loads(result.stdout)
    except (ValueError, TypeError):
        return []
    return document.get("blockdevices", []) if isinstance(document, dict) else []


def primary_storage_ready(root: Path) -> bool:
    try:
        # A directory is a valid primary pool on Android/PRoot and on
        # non-XFS native installs.  Mount status is capability information,
        # not a readiness requirement.
        return root.is_dir() and os.access(root, os.R_OK | os.W_OK | os.X_OK)
    except OSError:
        return False


__all__ = ["CapacityPlan", "GIB", "StoragePlanError", "capacity_plan", "discover_block_devices", "primary_storage_ready", "stable_disk_id", "validate_capacity"]

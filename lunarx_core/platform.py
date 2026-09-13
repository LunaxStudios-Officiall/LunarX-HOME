"""Runtime capability detection for native Ubuntu and Android/Termux PRoot.

The 3.1 detector is deliberately capability-first: it reports what is actually
present instead of assuming that a distro name implies systemd, desktop,
Flatpak, XFS quotas, or VPN control.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
import os
import platform as host_platform
import shutil
import subprocess

from .paths import PathConfig, resolve_paths


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for row in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = row.partition("=")
            if sep:
                result[key] = value.strip().strip('"')
    except OSError:
        pass
    return result


def _fs_type(path: Path) -> str:
    binary = shutil.which("stat")
    if not binary or not path.exists():
        return "unknown"
    try:
        result = subprocess.run([binary, "-f", "-c", "%T", str(path)], check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3)
        return result.stdout.strip().lower() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _mount_options(path: Path) -> set[str]:
    best: tuple[int, set[str]] = (-1, set())
    try:
        resolved = path.resolve(strict=False)
        for row in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            parts = row.split()
            if len(parts) < 4:
                continue
            target = Path(parts[1].replace("\\040", " "))
            try:
                resolved.relative_to(target.resolve(strict=False))
            except (ValueError, OSError):
                continue
            length = len(str(target))
            if length > best[0]:
                best = (length, set(parts[3].split(",")))
    except OSError:
        pass
    return best[1]


def _has_novnc() -> bool:
    return any((Path(root) / "vnc.html").is_file() for root in ("/usr/share/novnc", "/usr/share/noVNC", "/opt/novnc"))


@dataclass(frozen=True)
class PlatformInfo:
    mode: str
    distribution: str
    distribution_version: str
    architecture: str
    kernel: str
    is_termux: bool
    is_proot: bool
    service_manager: str
    storage_mode: str
    filesystem: str
    mount_options: tuple[str, ...]
    quota_mode: str
    app_backend: str
    desktop_backend: str
    desktop_available: bool
    tailscale_installed: bool
    capabilities: tuple[str, ...]

    @property
    def termux(self) -> bool:  # 2.1 compatibility
        return self.is_termux

    @property
    def proot(self) -> bool:  # 2.1 compatibility
        return self.is_proot

    @property
    def systemd(self) -> bool:  # 2.1 compatibility
        return self.service_manager == "systemd"

    @property
    def flatpak(self) -> bool:  # 2.1 compatibility
        return self.app_backend == "flatpak-user"

    @property
    def os_id(self) -> str:  # 2.1 compatibility
        return self.distribution

    @property
    def os_version(self) -> str:
        return self.distribution_version

    def as_dict(self) -> dict[str, object]:
        output = asdict(self)
        output["mount_options"] = list(self.mount_options)
        output["capabilities"] = list(self.capabilities)
        return output

    def public(self) -> dict[str, object]:
        value = self.as_dict()
        value["capabilities_detail"] = {
            "service_manager": self.service_manager,
            "quota_backend": self.quota_mode,
            "block_devices": not self.is_proot,
            "desktop_backend": self.desktop_backend,
            "flatpak": self.flatpak,
            "application_providers": [name for name in ("flatpak-user", "apt-system", "vendor-apt", "pinned-deb", "web-pwa") if name in self.capabilities],
            "tailscale_control": "android-host" if self.is_proot or self.is_termux else ("linux-cli" if self.tailscale_installed else "unavailable"),
        }
        return value


def detect_platform(
    *,
    env: Mapping[str, str] | None = None,
    machine: str | None = None,
    os_release: Mapping[str, str] | None = None,
    paths: PathConfig | None = None,
) -> PlatformInfo:
    values = os.environ if env is None else env
    release = dict(os_release or _read_os_release())
    marker = " ".join(str(values.get(key, "")) for key in ("LUNARX_PLATFORM", "TERMUX_VERSION", "PREFIX", "PROOT_VERSION", "PROOT_TMP_DIR", "PROOT_DISTRIBUTION")).lower()
    is_termux = "termux" in marker or bool(values.get("TERMUX_VERSION")) or "/com.termux/" in str(values.get("PREFIX", ""))
    explicit = str(values.get("LUNARX_PLATFORM", ""))
    is_proot = explicit == "android-proot" or "proot" in marker or bool(values.get("LUNARX_PROOT"))
    # Ubuntu/Debian userspace under a Termux host is a PRoot-style environment
    # even when the launcher stripped PROOT_* variables.
    if is_termux and release.get("ID") in {"ubuntu", "debian"}:
        is_proot = True
    if explicit in {"android-proot", "native-ubuntu", "portable-linux"}:
        mode = explicit
    elif is_proot:
        mode = "android-proot"
    elif release.get("ID") == "ubuntu":
        mode = "native-ubuntu"
    else:
        mode = "portable-linux"
    arch = (machine or host_platform.machine()).lower()
    architecture = {"amd64": "x86_64", "x86-64": "x86_64", "arm64": "aarch64"}.get(arch, arch or "unknown")
    config = paths or resolve_paths(env=values)
    fs = _fs_type(config.data_root)
    options = _mount_options(config.data_root) if config.data_root.exists() else set()
    native_quota = fs == "xfs" and bool({"prjquota", "pquota"} & options) and mode == "native-ubuntu"
    systemd = bool(shutil.which("systemctl")) and Path("/run/systemd/system").exists() and not is_proot
    service_manager = "systemd" if systemd else "lunarxctl" if mode in {"android-proot", "portable-linux"} else "portable"
    flatpak = shutil.which("flatpak") is not None and not is_proot
    vnc_server = bool(shutil.which("tigervncserver") or shutil.which("vncserver"))
    vnc_password = bool(shutil.which("tigervncpasswd") or shutil.which("vncpasswd"))
    novnc = _has_novnc()
    xrdp = bool(shutil.which("xrdp")) and mode == "native-ubuntu"
    if vnc_server and vnc_password and novnc:
        desktop_backend = "tigervnc-novnc"
        desktop_available = True
    elif xrdp:
        desktop_backend = "guacamole-rdp"
        desktop_available = True
    else:
        desktop_backend = "tigervnc-novnc" if mode == "android-proot" else "guacamole-rdp" if mode == "native-ubuntu" else "portable-session"
        desktop_available = False
    capabilities_set = {"canonical-paths", "logical-quota", "structured-broker", "registry-apps", "safe-updates", "first-run-bootstrap", "web-pwa"}
    if native_quota:
        capabilities_set.discard("logical-quota")
        capabilities_set.add("native-filesystem-quota")
    if flatpak:
        capabilities_set.add("flatpak-user")
    if shutil.which("apt-get") and shutil.which("apt-cache"):
        capabilities_set.add("apt-system")
        capabilities_set.add("pinned-deb")
        if shutil.which("gpg"):
            capabilities_set.add("vendor-apt")
    if desktop_available:
        capabilities_set.add("desktop-session")
    if systemd:
        capabilities_set.add("systemd")
    if shutil.which("tailscale") or is_proot:
        capabilities_set.add("tailscale-assistance")
    return PlatformInfo(
        mode=mode,
        distribution=release.get("ID", "unknown"),
        distribution_version=release.get("VERSION_ID", "unknown"),
        architecture=architecture,
        kernel=host_platform.release(),
        is_termux=is_termux,
        is_proot=is_proot,
        service_manager=service_manager,
        storage_mode="android-persistent-directory" if is_proot else "native-directory",
        filesystem=fs,
        mount_options=tuple(sorted(options)),
        quota_mode="native-filesystem" if native_quota else "logical",
        app_backend="flatpak-user" if flatpak else "registry-only",
        desktop_backend=desktop_backend,
        desktop_available=desktop_available,
        tailscale_installed=shutil.which("tailscale") is not None,
        capabilities=tuple(sorted(capabilities_set)),
    )


def capabilities(paths: PathConfig | None = None) -> dict[str, object]:
    config = paths or resolve_paths()
    info = detect_platform(paths=config)
    return {"platform": info.as_dict(), "paths": config.as_dict()}


__all__ = ["PlatformInfo", "capabilities", "detect_platform"]

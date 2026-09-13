#!/usr/bin/env python3
"""LunarX Home universal installer, updater, repairer and doctor.

The installer stages code before switching the active tree, preserves all
product data, and selects capabilities at runtime. Native Ubuntu may use
systemd and native XFS project quotas when explicitly available; Android/PRoot
and portable Linux use persistent directories, the local broker and logical
quotas. No command in this module formats a disk automatically.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform as host_platform
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request


VERSION = "3.1.3"
SUPPORTED_UBUNTU = {"24.04", "26.04"}
EXCLUDES = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "build", "dist", "packaging", "validation", "*.zip", "*.tar", "*.gz", "*.pyc", "*.img", "*.vhdx"}
NATIVE_PACKAGES = ["acl", "avahi-daemon", "curl", "file", "ffmpeg", "flatpak", "nginx", "python3", "python3-cryptography", "python3-pil", "smartmontools", "sudo", "unzip"]
DESKTOP_PACKAGES = ["xfce4", "xfce4-goodies", "tigervnc-standalone-server", "tigervnc-tools", "novnc", "websockify", "xrdp"]
PROOT_PACKAGES = ["ca-certificates", "curl", "file", "ffmpeg", "gnupg", "python3", "python3-cryptography", "python3-pil", "unzip"]
PROOT_DESKTOP_PACKAGES = ["dbus-x11", "xfce4", "xfce4-goodies", "tigervnc-standalone-server", "tigervnc-tools", "novnc", "websockify"]


class InstallError(RuntimeError):
    pass


def _json_load(path: Path, fallback: dict[str, object]) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(fallback)
    except (OSError, ValueError, TypeError):
        return dict(fallback)


def atomic_text(path: Path, content: str, mode: int = 0o640) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.lunarx-{os.getpid()}-{secrets.token_hex(4)}")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def run(command: list[str], *, check: bool = True, input_text: str | None = None, timeout: int = 120, stream: bool = False) -> subprocess.CompletedProcess[str]:
    environment = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "DEBIAN_FRONTEND": os.environ.get("DEBIAN_FRONTEND", "noninteractive")}
    try:
        if stream:
            completed = subprocess.run(command, check=False, text=True, input=input_text, timeout=timeout, env=environment)
            result = subprocess.CompletedProcess(command, completed.returncode, "", "")
        else:
            result = subprocess.run(command, check=False, text=True, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise InstallError(f"command unavailable or timed out: {command[0]}") from exc
        return subprocess.CompletedProcess(command, 127, "", str(exc))
    if check and result.returncode:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip() or f"exit {result.returncode}"
        raise InstallError(f"command failed: {command[0]}: {detail[:500]}")
    return result


def read_os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for row in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, sep, value = row.partition("=")
            if sep:
                values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def detect_mode() -> str:
    marker = " ".join(str(os.environ.get(key, "")) for key in ("LUNARX_PLATFORM", "TERMUX_VERSION", "PREFIX", "PROOT_VERSION", "PROOT_TMP_DIR")).lower()
    if os.environ.get("LUNARX_PLATFORM") in {"android-proot", "native-ubuntu", "portable-linux"}:
        return str(os.environ["LUNARX_PLATFORM"])
    if any(value in marker for value in ("android", "termux", "proot")):
        return "android-proot"
    return "native-ubuntu" if read_os_release().get("ID") == "ubuntu" else "portable-linux"


def machine_architecture() -> str:
    value = host_platform.machine().lower()
    return {"amd64": "x86_64", "x86-64": "x86_64", "arm64": "aarch64"}.get(value, value)


def memory_bytes() -> int:
    try:
        for row in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if row.startswith("MemTotal:"):
                return int(row.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def copy_source(source: Path, destination: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in EXCLUDES or any(name.endswith(suffix[1:]) for suffix in EXCLUDES if suffix.startswith("*")):
                ignored.add(name)
        return ignored
    staging = destination.with_name(f".{destination.name}.stage-{os.getpid()}-{secrets.token_hex(4)}")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging, ignore=ignore, symlinks=False)
    for path in staging.rglob("*"):
        if path.name in {"install.sh", "bootstrap.sh", "lunarx-admin.py", "lunarx-terminal", "lunarxctl", "lunarx_installer.py", "lunarx-desktop-session.py", "lunarx-desktop-governor.py", "lunarx-user-apps"}:
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    old = destination.with_name(f".{destination.name}.old-{os.getpid()}-{secrets.token_hex(4)}")
    if destination.exists():
        os.replace(destination, old)
    os.replace(staging, destination)
    if old.exists():
        shutil.rmtree(old)


def _portable_copy(src: str, dst: str) -> str:
    """Buffered copy that avoids sendfile EINVAL on Android/Termux PRoot."""
    with open(src, "rb") as source_handle, open(dst, "wb") as destination_handle:
        shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
    shutil.copystat(src, dst, follow_symlinks=False)
    return dst


def _backup_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in EXCLUDES or any(name.endswith(suffix[1:]) for suffix in EXCLUDES if suffix.startswith("*")):
            ignored.add(name)
    return ignored


def copy_backup(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=_portable_copy, ignore=_backup_ignore)
    else:
        _portable_copy(str(source), str(destination))


class Installer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.source = args.source.resolve()
        self.mode = detect_mode()
        self.architecture = machine_architecture()
        self.target = Path(args.target).expanduser().resolve() if args.target else (Path.home() / ".local" / "opt" / "lunarx-home" if self.mode == "android-proot" else Path("/opt/lunarx-home"))
        if self.mode == "android-proot":
            persistent = Path(os.environ.get("LUNARX_PERSISTENT_ROOT") or os.environ.get("LUNARX_ANDROID_ROOT") or (Path.home() / ".local" / "share" / "lunarx-home")).expanduser().resolve()
            self.config_root, self.data_root, self.state_root, self.runtime_root, self.backup_root = (persistent / name for name in ("config", "data", "state", "runtime", "backups"))
        else:
            self.config_root = Path(os.environ.get("LUNARX_CONFIG_ROOT", "/etc/lunarx-home")).resolve()
            self.data_root = Path(os.environ.get("LUNARX_DATA_ROOT", "/srv/lunarx-data")).resolve()
            self.state_root = Path(os.environ.get("LUNARX_STATE_ROOT", "/var/lib/lunarx-home")).resolve()
            self.runtime_root = Path(os.environ.get("LUNARX_RUNTIME_ROOT", "/run/lunarx-home")).resolve()
            self.backup_root = Path(os.environ.get("LUNARX_BACKUP_ROOT", "/var/backups/lunarx-home")).resolve()
        self.plan: list[str] = []
        self.backup: Path | None = None

    def emit(self, message: str) -> None:
        print(f"[LunarX] {message}")

    def preflight(self) -> None:
        required = ["app.py", "catalog/apps.json", "lunarx-admin.py", "static/index.html", "lunarx_core/paths.py"]
        missing = [item for item in required if not (self.source / item).is_file()]
        if missing:
            raise InstallError(f"source tree is incomplete: {', '.join(missing)}")
        if self.mode == "native-ubuntu":
            release = read_os_release()
            if release.get("VERSION_ID") not in SUPPORTED_UBUNTU:
                raise InstallError("native installation supports Ubuntu 24.04 and 26.04 LTS")
            if os.geteuid() != 0 and not self.args.dry_run:
                raise InstallError("native installation requires root; use sudo")
        elif self.mode == "portable-linux" and not self.args.allow_unsupported_host:
            if not self.args.dry_run:
                raise InstallError("portable Linux is supported for development/inspection; set --allow-unsupported-host to install")
        if self.architecture not in {"x86_64", "aarch64"}:
            raise InstallError(f"unsupported CPU architecture: {self.architecture}; supported: x86_64 and aarch64")
        if memory_bytes() and memory_bytes() < 2 * 1024**3:
            raise InstallError("at least 2 GiB RAM is required; 4 GiB is recommended")
        self.plan.extend([
            f"Platform: {self.mode} / {self.architecture}",
            f"Service manager: {'systemd' if self.mode == 'native-ubuntu' else 'termux/portable supervisor'}",
            f"Primary storage: {self.data_root} (logical quota by default; no automatic formatting)",
            "Migration policy: preserve user data, configuration, accounts and registry; stage code before activation",
        ])

    def show_plan(self) -> None:
        for item in self.plan:
            self.emit(item)
        self.emit(f"Operation: {self.args.command}; source: {self.source}; version: {VERSION}")

    def require_native_root(self) -> None:
        if self.mode == "native-ubuntu" and os.geteuid() != 0:
            raise InstallError("run native operations with sudo or as root")

    def create_backup(self) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.backup = self.backup_root / stamp
        self.backup.mkdir(mode=0o700, parents=True, exist_ok=True)
        for source, relative in ((self.target, Path("application")), (self.config_root, Path("config")), (self.state_root / "install-state.json", Path("install-state.json"))):
            if source.exists():
                copy_backup(source, self.backup / relative)
        self.emit(f"Backup created: {self.backup}")

    def install_packages(self) -> None:
        if self.args.skip_packages:
            return
        if self.mode == "native-ubuntu":
            packages = NATIVE_PACKAGES + (DESKTOP_PACKAGES if not self.args.no_desktop else [])
        elif self.mode == "android-proot":
            if not shutil.which("apt-get"):
                raise InstallError("Ubuntu PRoot requires apt-get")
            if os.geteuid() != 0:
                raise InstallError("run the Ubuntu PRoot install as root so required packages can be installed")
            packages = PROOT_PACKAGES + (PROOT_DESKTOP_PACKAGES if not self.args.no_desktop else [])
        else:
            return
        self.emit("Updating package metadata (progress follows below)...")
        run(["apt-get", "update"], timeout=900, stream=True)
        self.emit("Installing required packages (progress follows below)...")
        run(["apt-get", "install", "-y", "--no-install-recommends", *packages], timeout=1800, stream=True)

    def ensure_directories(self) -> None:
        for path in (self.config_root, self.data_root, self.state_root, self.runtime_root, self.backup_root, self.data_root / "shared", self.data_root / "users"):
            path.mkdir(mode=0o770 if path.name in {"shared", "users"} else 0o750, parents=True, exist_ok=True)

    def ensure_native_accounts(self) -> str | None:
        if self.mode != "native-ubuntu":
            return self.args.admin_user or None
        for group in ("lunarx-home", "lunarx-admin", "lunarx-shared"):
            run(["groupadd", "--force", group])
        if run(["id", "lunarx-home"], check=False).returncode:
            run(["useradd", "--system", "--gid", "lunarx-home", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", "lunarx-home"])
        admin = self.args.admin_user or os.environ.get("SUDO_USER", "")
        if admin == "root":
            admin = ""
        if not admin:
            try:
                admin = next(record.pw_name for record in __import__("pwd").getpwall() if 1000 <= record.pw_uid < 65534 and record.pw_shell not in {"/usr/sbin/nologin", "/bin/false"})
            except StopIteration as exc:
                raise InstallError("--admin-user is required when no normal Linux account exists") from exc
        if run(["id", admin], check=False).returncode:
            raise InstallError(f"normal administrator account does not exist: {admin}")
        run(["usermod", "--append", "--groups", "lunarx-admin,lunarx-shared", admin])
        return admin

    def ensure_config(self, admin: str | None) -> str | None:
        config_path = self.config_root / "config.json"
        config_existed = config_path.is_file()
        config = _json_load(config_path, _json_load(self.source / "config.json", {}))
        config["version"] = VERSION
        if self.mode == "android-proot" and not config_existed:
            # Home-server installs must be reachable from the Android host/LAN/VPN.
            config["bind"] = "0.0.0.0"
        config.setdefault("storage", {})["primary_root"] = str(self.data_root)
        config["storage"]["provider"] = config["storage"].get("provider", "auto")
        config.setdefault("app_store", {})["catalog"] = str(self.target / "catalog" / "apps.json")
        atomic_text(config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")

        defaults = {
            "schema_version": VERSION,
            "settings": {"shared_contribution_gib": 15, "default_quota_gib": 64, "desktop_idle_timeout_minutes": 10, "theme_mode": "dark", "server_name": "LunarX Home"},
            "users": {}, "server_apps": {}, "installed_apps": {}, "managed_apps": {},
            "storage": {"provider": "logical", "members": []},
        }
        state_path = self.config_root / "state.json"
        state = _json_load(state_path, defaults)
        state["schema_version"] = VERSION
        settings = state.setdefault("settings", {})
        for key, value in defaults["settings"].items():
            settings.setdefault(key, value)
        users = state.setdefault("users", {})
        if not isinstance(users, dict):
            users = {}; state["users"] = users

        if admin and admin not in users:
            users[admin] = {
                "display_name": admin.title(), "avatar": "", "theme": "dark", "enabled": True,
                "is_admin": True, "role": "admin", "auth_provider": "pam",
                "permissions": {"desktop_access": True, "desktop_app_install": True, "shared_access": True, "drive": True, "photos": True, "files": True},
            }

        default_quota = max(1, int(settings.get("default_quota_gib", 64) or 64))
        default_permissions = {"desktop_access": True, "desktop_app_install": True, "shared_access": True, "drive": True, "photos": True, "files": True}
        for username, profile in list(users.items()):
            if not isinstance(profile, dict):
                continue
            profile.setdefault("display_name", str(username).title())
            profile.setdefault("avatar", "")
            profile.setdefault("theme", "dark")
            profile.setdefault("enabled", True)
            profile.setdefault("is_admin", profile.get("role") == "admin")
            profile["role"] = "admin" if profile.get("is_admin") else "user"
            profile.setdefault("auth_provider", "local-scrypt" if self.mode == "android-proot" else "pam")
            permissions = profile.setdefault("permissions", {})
            if not isinstance(permissions, dict):
                permissions = {}; profile["permissions"] = permissions
            for key, value in default_permissions.items():
                permissions.setdefault(key, value)
            try:
                quota = int(profile.get("quota_gib", 0) or 0)
            except (TypeError, ValueError):
                quota = 0
            profile["quota_gib"] = max(1, quota or default_quota)

        atomic_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")

        # Keep a compatible quota snapshot for older helper paths, while runtime
        # metrics also derive limits directly from state for legacy migrations.
        quota_rows = [
            {"username": str(username), "hard": f"{int(profile.get('quota_gib', default_quota))}G"}
            for username, profile in users.items() if isinstance(profile, dict)
        ]
        active_users = sum(1 for profile in users.values() if isinstance(profile, dict) and profile.get("enabled", True))
        if active_users:
            shared = max(1, int(settings.get("shared_contribution_gib", 15) or 15)) * active_users
            quota_rows.append({"username": "shared", "hard": f"{shared}G"})
        quotas = self.config_root / "quotas.json"
        atomic_text(quotas, json.dumps({"schema_version": VERSION, "provider": "logical", "quotas": quota_rows}, ensure_ascii=False, indent=2) + "\n")

        env_path = self.config_root / "home.env"
        if self.mode == "native-ubuntu" and not env_path.exists():
            atomic_text(env_path, f"LUNARX_JSON_SECRET={secrets.token_hex(16)}\nLUNARX_SECURE_COOKIE=0\n", 0o600)
        return admin

    def create_setup_token(self, admin: str | None) -> str | None:
        users = _json_load(self.config_root / "state.json", {}).get("users", {})
        has_admin = any(bool(item.get("is_admin", item.get("role") == "admin")) for item in users.values() if isinstance(item, dict)) if isinstance(users, dict) else False
        if admin or has_admin:
            return None
        token_path = self.config_root / "setup-token"
        if token_path.exists():
            try:
                existing = token_path.read_text(encoding="utf-8").strip()
            except OSError:
                existing = ""
            if 20 <= len(existing) <= 160 and "\n" not in existing:
                return existing
        token = secrets.token_urlsafe(32)
        atomic_text(token_path, token + "\n", 0o600)
        return token

    def install_native_units(self) -> None:
        if self.mode != "native-ubuntu":
            return
        target_root = Path("/etc/systemd/system")
        target_root.mkdir(parents=True, exist_ok=True)
        for name in ("lunarx-home.service", "lunarx-desktop-governor.service", "lunarx-desktop-governor.timer"):
            source = self.target / name
            if source.exists():
                shutil.copy2(source, target_root / name)
        helper = Path("/usr/local/libexec/lunarx-admin")
        helper.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copy2(self.target / "lunarx-admin.py", helper); helper.chmod(0o750)
        governor = Path("/usr/local/libexec/lunarx-desktop-governor")
        shutil.copy2(self.target / "lunarx-desktop-governor.py", governor); governor.chmod(0o750)
        terminal = Path("/usr/local/libexec/lunarx-terminal")
        shutil.copy2(self.target / "lunarx-terminal", terminal); terminal.chmod(0o750)
        sudoers = Path("/etc/sudoers.d/lunarx-home")
        sudoers.parent.mkdir(mode=0o755, exist_ok=True)
        if (self.target / "lunarx-home.sudoers").exists():
            shutil.copy2(self.target / "lunarx-home.sudoers", sudoers); sudoers.chmod(0o440)
        run(["systemctl", "daemon-reload"])
        if not self.args.no_start:
            run(["systemctl", "enable", "--now", "lunarx-home.service"])
            if (target_root / "lunarx-desktop-governor.timer").exists():
                run(["systemctl", "enable", "--now", "lunarx-desktop-governor.timer"], check=False)

    def provision_native_admin(self, admin: str | None) -> None:
        """Create the managed storage/session for an existing native account.

        Account creation and configuration are deliberately separated from the
        application copy.  Running the installed broker after the copy keeps
        the native path policy in one place and makes the initial install
        equivalent to an admin provisioning action.
        """
        if self.mode != "native-ubuntu" or not admin:
            return
        payload = {
            "action": "provision_existing_user",
            "username": admin,
            "display_name": admin.title(),
            "quota_gib": 64,
            "permissions": {
                "desktop_access": True,
                "desktop_app_install": True,
                "shared_access": True,
                "drive": True,
                "photos": True,
                "files": True,
            },
        }
        environment = os.environ.copy()
        environment.update({
            "LUNARX_APP_ROOT": str(self.target),
            "LUNARX_PLATFORM": "native-ubuntu",
            "LUNARX_CONFIG_ROOT": str(self.config_root),
            "LUNARX_DATA_ROOT": str(self.data_root),
            "LUNARX_STATE_ROOT": str(self.state_root),
            "LUNARX_RUNTIME_ROOT": str(self.runtime_root),
            "LUNARX_BACKUP_ROOT": str(self.backup_root),
        })
        try:
            result = subprocess.run(
                [sys.executable, str(self.target / "lunarx-admin.py")],
                input=(json.dumps(payload) + "\n").encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=180,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError("native administrator provisioning failed") from exc
        if result.returncode:
            raise InstallError("native administrator provisioning failed")

    def write_control_command(self) -> None:
        command = self.target / "lunarxctl"
        if self.mode == "android-proot" and command.exists():
            destination = Path.home() / ".local" / "bin" / "lunarxctl"
            destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            destination.symlink_to(command)

    def runtime_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update({
            "LUNARX_APP_ROOT": str(self.target),
            "LUNARX_PLATFORM": self.mode,
            "LUNARX_CONFIG_ROOT": str(self.config_root),
            "LUNARX_DATA_ROOT": str(self.data_root),
            "LUNARX_STATE_ROOT": str(self.state_root),
            "LUNARX_RUNTIME_ROOT": str(self.runtime_root),
            "LUNARX_BACKUP_ROOT": str(self.backup_root),
            "LUNARX_BROKER_MODE": "direct" if self.mode != "native-ubuntu" else "native",
        })
        return environment

    def start_portable_runtime(self) -> None:
        if self.mode == "native-ubuntu" or self.args.no_start:
            return
        result = subprocess.run([sys.executable, str(self.target / "lunarxctl"), "restart"], cwd=self.target, env=self.runtime_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=False)
        if result.returncode:
            raise InstallError(f"LunarX service could not be started: {(result.stderr or result.stdout)[-400:]}")

    def configured_listener(self) -> tuple[str, int]:
        config = _json_load(self.config_root / "config.json", {})
        bind = str(config.get("bind", "127.0.0.1") or "127.0.0.1").strip()
        try:
            port = int(config.get("port", 8787) or 8787)
        except (TypeError, ValueError):
            port = 8787
        if not 1 <= port <= 65535:
            port = 8787
        return bind, port

    @staticmethod
    def _remote_listener(bind: str) -> bool:
        if bind in {"0.0.0.0", "::", "[::]"}:
            return True
        try:
            return not ipaddress.ip_address(bind.strip("[]")).is_loopback
        except ValueError:
            return False

    @staticmethod
    def detect_lan_address() -> str | None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("1.1.1.1", 53))
                candidate = str(sock.getsockname()[0])
            finally:
                sock.close()
            address = ipaddress.ip_address(candidate)
            if isinstance(address, ipaddress.IPv4Address) and not address.is_loopback and not address.is_unspecified and not address.is_link_local:
                return candidate
        except (OSError, ValueError):
            pass
        result = run(["ip", "-j", "route", "get", "1.1.1.1"], check=False, timeout=4)
        if result.returncode == 0:
            try:
                rows = json.loads(result.stdout)
                candidate = str(rows[0].get("prefsrc", "")) if isinstance(rows, list) and rows else ""
                address = ipaddress.ip_address(candidate)
                if isinstance(address, ipaddress.IPv4Address) and not address.is_loopback and not address.is_unspecified and not address.is_link_local:
                    return candidate
            except (ValueError, TypeError, IndexError):
                pass
        return None

    def detect_tailscale_address(self) -> str | None:
        if self.mode == "android-proot":
            return None
        binary = shutil.which("tailscale")
        if not binary:
            return None
        result = run([binary, "ip", "-4"], check=False, timeout=6)
        if result.returncode:
            return None
        for row in result.stdout.splitlines():
            candidate = row.strip()
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if isinstance(address, ipaddress.IPv4Address) and address in ipaddress.ip_network("100.64.0.0/10"):
                return candidate
        return None

    def emit_access_summary(self, token: str | None) -> None:
        bind, port = self.configured_listener()
        self.emit("LunarX Home is ready" if not self.args.no_start else "LunarX Home is installed; automatic start was skipped")
        self.emit(f"Listening: {bind}:{port}")
        local_host = "127.0.0.1" if bind in {"0.0.0.0", "::", "[::]"} else bind
        self.emit(f"Local URL: http://{local_host}:{port}")
        if self._remote_listener(bind):
            lan = self.detect_lan_address()
            self.emit(f"LAN URL: http://{lan}:{port}" if lan else "LAN URL: unavailable; confirm the host LAN address and use the listening port shown above.")
            tailscale = self.detect_tailscale_address()
            self.emit(f"Tailscale URL: http://{tailscale}:{port}" if tailscale else "Tailscale URL: unavailable or Tailscale is not connected on this host.")
        else:
            self.emit("LAN URL: unavailable because the configured LunarX listener is loopback-only.")
            self.emit("Tailscale URL: unavailable because the configured LunarX listener is loopback-only.")
        if self.mode == "android-proot":
            self.emit("Android/PRoot note: Tailscale belongs to the Android host, not the Ubuntu PRoot guest. Use the Android host address only when the LunarX listener is configured for host-network access.")
        if token:
            self.emit(f"FIRST_RUN_SETUP_TOKEN={token}")
            self.emit("Use this one-time token in Primeira configuração; it is invalidated after the first administrator is created.")

    def verify_health(self, expected_version: str = VERSION) -> None:
        if self.args.no_start:
            return
        _bind, port = self.configured_listener()
        last = "health endpoint unavailable"
        for _ in range(20):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                    value = json.loads(response.read().decode("utf-8"))
                    if value.get("ok") and value.get("version") == expected_version:
                        self.emit(f"Health check passed on 127.0.0.1:{port}")
                        return
                    last = f"unexpected health response: {value}"
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last = str(exc)
            time.sleep(0.25)
        raise InstallError(f"post-install health check failed: {last[:300]}")

    def write_install_state(self, admin: str | None) -> None:
        state = {"schema_version": VERSION, "version": VERSION, "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "platform": self.mode, "architecture": self.architecture, "paths": {"application": str(self.target), "config": str(self.config_root), "data": str(self.data_root), "state": str(self.state_root), "runtime": str(self.runtime_root)}, "quota_provider": "logical", "admin_user": admin, "backup": str(self.backup) if self.backup else None}
        atomic_text(self.state_root / "install-state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    def compile_check(self) -> None:
        result = run([sys.executable, "-m", "py_compile", str(self.target / "app.py"), str(self.target / "lunarx-admin.py"), str(self.target / "installer" / "lunarx_installer.py")], check=False, timeout=120)
        if result.returncode:
            raise InstallError("Python syntax check failed")

    def restore_backup(self) -> None:
        if not self.backup or not self.backup.is_dir():
            raise InstallError("no rollback backup is available")
        application = self.backup / "application"
        config = self.backup / "config"
        install_state = self.backup / "install-state.json"
        if not application.is_dir():
            raise InstallError("rollback backup does not contain the previous application")

        if self.mode == "native-ubuntu":
            run(["systemctl", "disable", "--now", "lunarx-home.service"], check=False)
        elif self.target.is_dir() and (self.target / "lunarxctl").is_file():
            subprocess.run([sys.executable, str(self.target / "lunarxctl"), "stop"], cwd=self.target, env=self.runtime_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20, check=False)

        copy_source(application, self.target)
        if config.is_dir():
            staged_config = self.config_root.with_name(f".{self.config_root.name}.rollback-{os.getpid()}-{secrets.token_hex(4)}")
            if staged_config.exists():
                shutil.rmtree(staged_config)
            shutil.copytree(config, staged_config, symlinks=True)
            if self.config_root.exists():
                shutil.rmtree(self.config_root)
            os.replace(staged_config, self.config_root)
        if install_state.is_file():
            copy_backup(install_state, self.state_root / "install-state.json")

        if self.mode == "native-ubuntu":
            self.install_native_units()
        else:
            self.write_control_command()
            self.start_portable_runtime()
        previous_version = (self.target / "VERSION").read_text(encoding="utf-8").strip() if (self.target / "VERSION").is_file() else ""
        if previous_version:
            self.verify_health(previous_version)
        self.emit(f"Rollback restored LunarX Home {previous_version or 'previous version'} from {self.backup}")

    def install_or_update(self) -> None:
        self.require_native_root()
        had_existing = self.target.is_dir() or (self.config_root / "state.json").is_file() or (self.config_root / "config.json").is_file()
        self.ensure_directories()
        if had_existing:
            self.create_backup()
        try:
            self.install_packages()
            admin = self.ensure_native_accounts()
            self.ensure_config(admin)
            copy_source(self.source, self.target)
            self.compile_check()
            self.provision_native_admin(admin)
            self.write_control_command()
            self.write_install_state(admin)
            token = self.create_setup_token(admin)
            self.install_native_units()
            self.start_portable_runtime()
            self.verify_health()
        except Exception as exc:
            if had_existing and self.backup:
                try:
                    self.restore_backup()
                except Exception as rollback_exc:
                    raise InstallError(f"update failed and automatic rollback failed; backup preserved at {self.backup}: {rollback_exc}") from exc
                raise InstallError(f"update failed; previous version restored automatically: {exc}") from exc
            raise
        self.emit_access_summary(token)
        self.emit(f"LunarX Home {VERSION} activated; existing data and configuration were preserved")

    def doctor(self) -> dict[str, object]:
        checks: dict[str, object] = {"source_complete": all((self.source / path).is_file() for path in ("app.py", "catalog/apps.json", "lunarx_core/paths.py")), "architecture_supported": self.architecture in {"x86_64", "aarch64"}, "platform": self.mode, "application_present": self.target.is_dir(), "data_directory": self.data_root.is_dir(), "config_directory": self.config_root.is_dir()}
        checks["ok"] = all(bool(value) for key, value in checks.items() if key != "platform")
        return checks

    def repair(self) -> None:
        self.require_native_root(); self.ensure_directories(); self.compile_check(); self.install_native_units(); self.write_control_command(); self.write_install_state(self.args.admin_user); self.start_portable_runtime(); self.verify_health(); self.emit("Doctor repair completed without deleting data")

    def uninstall(self) -> None:
        self.require_native_root(); self.create_backup()
        if self.mode == "native-ubuntu":
            run(["systemctl", "disable", "--now", "lunarx-home.service"], check=False)
            Path("/etc/systemd/system/lunarx-home.service").unlink(missing_ok=True)
            Path("/etc/sudoers.d/lunarx-home").unlink(missing_ok=True)
            run(["systemctl", "daemon-reload"], check=False)
        elif self.target.is_dir() and (self.target / "lunarxctl").is_file():
            subprocess.run([sys.executable, str(self.target / "lunarxctl"), "stop"], cwd=self.target, env=self.runtime_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20, check=False)
        if self.mode == "android-proot":
            control = Path.home() / ".local" / "bin" / "lunarxctl"
            if control.is_symlink():
                try:
                    if control.resolve(strict=False) == (self.target / "lunarxctl").resolve(strict=False):
                        control.unlink()
                except OSError:
                    pass
        if self.target.exists():
            shutil.rmtree(self.target)
        self.emit("Application code removed; data, configuration, accounts and backups were preserved")

    def execute(self) -> int:
        self.preflight(); self.show_plan()
        if self.args.dry_run or self.args.command == "status":
            print(json.dumps({"ok": True, "dry_run": True, "version": VERSION, "platform": self.mode, "architecture": self.architecture, "plan": self.plan}, ensure_ascii=False, indent=2))
            return 0
        if self.args.command in {"install", "update", "upgrade"}:
            self.install_or_update()
        elif self.args.command == "repair":
            self.repair()
        elif self.args.command == "doctor":
            print(json.dumps(self.doctor(), ensure_ascii=False, indent=2))
        elif self.args.command == "uninstall":
            self.uninstall()
        return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=["install", "update", "upgrade", "repair", "uninstall", "status", "doctor"])
    result.add_argument("--source", type=Path, required=True)
    result.add_argument("--target", type=Path)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--allow-unsupported-host", action="store_true", help="allow a portable development host")
    result.add_argument("--admin-user")
    result.add_argument("--skip-packages", action="store_true")
    result.add_argument("--enable-services", action="store_true", help="compatibility flag; services start by default in 3.1")
    result.add_argument("--no-start", action="store_true", help="install or update without starting the service")
    result.add_argument("--no-desktop", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        return Installer(args).execute()
    except (InstallError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "error_code": "installer_failure"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

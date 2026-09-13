"""Safe, structured local broker for portable and Android/PRoot installs.

The broker intentionally exposes a small action allow-list.  It never accepts
shell fragments, never returns command stderr to the browser, and persists
local account metadata atomically.  Native installations can still use the
privileged helper; portable installations use this same contract directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from .auth import AuthError, default_permissions, hash_password, validate_username, verify_password
from .catalog import CatalogError, find_app, find_provider, normalize_architecture
from .paths import PathConfig, resolve_paths
from .platform import PlatformInfo, detect_platform
from .storage import stable_disk_id
from .vendor_apps import PINNED_DEB, VENDOR_APT


DEFAULT_STATE: dict[str, Any] = {
    "schema_version": "3.1.2",
    "settings": {"shared_contribution_gib": 15, "default_quota_gib": 64, "desktop_idle_timeout_minutes": 10, "theme_mode": "dark", "server_name": "LunarX Home"},
    "users": {},
    "server_apps": {},
    "installed_apps": {},
    "managed_apps": {},
    "storage": {"provider": "logical", "members": []},
}


class BrokerError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def public(self) -> dict[str, Any]:
        value: dict[str, Any] = {"error": self.message, "error_code": self.code}
        if self.detail:
            value["detail"] = self.detail[:240]
        return value


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else json.loads(json.dumps(fallback))
    except (OSError, ValueError, TypeError):
        return json.loads(json.dumps(fallback))


def _atomic_json(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(mode)
    os.replace(temporary, path)


def load_state(paths: PathConfig | None = None) -> dict[str, Any]:
    config = paths or resolve_paths()
    state = _load_json(config.state_file, DEFAULT_STATE)
    for key, default in DEFAULT_STATE.items():
        if key not in state or not isinstance(state[key], type(default)):
            state[key] = json.loads(json.dumps(default))
    if not isinstance(state.get("users"), dict):
        state["users"] = {}
    return state


def save_state(state: dict[str, Any], paths: PathConfig | None = None) -> None:
    _atomic_json((paths or resolve_paths()).state_file, state)


def _user_public(username: str, item: dict[str, Any]) -> dict[str, Any]:
    permissions = default_permissions()
    if isinstance(item.get("permissions"), dict):
        permissions.update({key: bool(value) for key, value in item["permissions"].items() if key in permissions})
    return {
        "username": username,
        "display_name": str(item.get("display_name") or username.title()),
        "avatar": str(item.get("avatar") or ""),
        "theme": str(item.get("theme") or "dark"),
        "enabled": bool(item.get("enabled", True)),
        "is_admin": bool(item.get("is_admin", item.get("role") == "admin")),
        "role": "admin" if bool(item.get("is_admin", item.get("role") == "admin")) else "user",
        "permissions": permissions,
        "quota_gib": int(item.get("quota_gib", 0) or 0),
        "auth_provider": str(item.get("auth_provider") or "local-scrypt"),
    }


def _ensure_user_dirs(paths: PathConfig, username: str) -> None:
    root = paths.data_root / "users" / username
    for name in ("Workspace", "Desktop", "Fotos", "Videos"):
        (root / name).mkdir(mode=0o770, parents=True, exist_ok=True)


def _safe_relative(value: object) -> Path:
    raw = str(value or "")
    if not raw or "\\" in raw or raw.startswith("/") or "\x00" in raw:
        raise BrokerError("invalid_path", "invalid storage path")
    candidate = Path(raw)
    if any(part in {"", ".", ".."} or part.startswith(".") for part in candidate.parts):
        raise BrokerError("invalid_path", "invalid storage path")
    return candidate


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in files:
                item = Path(root) / name
                if not item.is_symlink():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        continue
    except OSError:
        return 0
    return total


def _storage_status(paths: PathConfig, info: PlatformInfo) -> dict[str, Any]:
    paths.ensure_dirs()
    try:
        usage = shutil.disk_usage(paths.data_root)
        total, free = int(usage.total), int(usage.free)
    except OSError:
        total = free = 0
    state = load_state(paths)
    return {
        "provider": "native-filesystem" if info.quota_mode == "native-filesystem" else "logical",
        "mode": info.storage_mode,
        "root": str(paths.data_root),
        "available": paths.data_root.is_dir() and os.access(paths.data_root, os.R_OK | os.W_OK | os.X_OK),
        "filesystem": info.filesystem,
        "total_bytes": total,
        "free_bytes": free,
        "used_bytes": max(total - free, 0),
        "logical_quota": info.quota_mode != "native-filesystem",
        "members": state.get("storage", {}).get("members", []),
    }



def _portable_desktop_identity(username: str, paths: PathConfig) -> tuple[int, int, Path]:
    digest = int(hashlib.sha256(username.encode("utf-8")).hexdigest()[:8], 16)
    display = 20 + (digest % 40)
    return display, 5900 + display, paths.state_root / "desktop" / f"{username}.json"


def _portable_desktop_session(username: str, paths: PathConfig, info: PlatformInfo) -> dict[str, Any]:
    if info.desktop_backend != "tigervnc-novnc":
        raise BrokerError("desktop_provider_unavailable", "TigerVNC + noVNC is not available on this host")
    vncserver = shutil.which("tigervncserver") or shutil.which("vncserver")
    vncpasswd = shutil.which("vncpasswd")
    if not vncserver or not vncpasswd:
        raise BrokerError("desktop_provider_unavailable", "TigerVNC is not installed")
    home = paths.user_home(username)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    vnc_dir = home / ".vnc"
    vnc_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    display, port, state_path = _portable_desktop_identity(username, paths)
    state_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    secret = ""
    try:
        current = json.loads(state_path.read_text(encoding="utf-8"))
        secret = str(current.get("password") or "") if isinstance(current, dict) else ""
    except (OSError, ValueError, TypeError):
        pass
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", secret):
        secret = secrets.token_urlsafe(9)[:12]
    encoded = subprocess.run([vncpasswd, "-f"], input=(secret + "\n").encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    if encoded.returncode or not encoded.stdout:
        raise BrokerError("desktop_provider_error", "VNC credential setup failed", detail=encoded.stderr.decode("utf-8", "replace"))
    passwd_file = vnc_dir / "passwd"
    passwd_file.write_bytes(encoded.stdout)
    passwd_file.chmod(0o600)
    xstartup = vnc_dir / "xstartup"
    xstartup.write_text("#!/bin/sh\nunset SESSION_MANAGER\nunset DBUS_SESSION_BUS_ADDRESS\nexec startxfce4\n", encoding="utf-8")
    xstartup.chmod(0o700)
    environment = os.environ.copy()
    environment.update({"HOME": str(home), "USER": username, "LOGNAME": username})
    probe = subprocess.run([vncserver, "-list"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=20, check=False)
    running = f":{display}" in probe.stdout.decode("utf-8", "replace")
    if not running:
        started = subprocess.run([vncserver, f":{display}", "-localhost", "yes", "-geometry", "1280x720", "-depth", "24", "-SecurityTypes", "VncAuth"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=60, check=False)
        if started.returncode:
            detail = (started.stderr or started.stdout).decode("utf-8", "replace")
            raise BrokerError("desktop_start_failed", "graphical desktop could not be started", detail=detail)
    _atomic_json(state_path, {"username": username, "display": display, "port": port, "password": secret, "provider": "tigervnc-localhost", "updated": int(time.time())}, 0o600)
    return {"username": username, "provider": "tigervnc-localhost", "display": display, "port": port, "password": secret}


def _portable_desktop_terminate(username: str, paths: PathConfig) -> dict[str, Any]:
    display, _port, state_path = _portable_desktop_identity(username, paths)
    vncserver = shutil.which("tigervncserver") or shutil.which("vncserver")
    terminated: list[str] = []
    if vncserver:
        home = paths.user_home(username)
        environment = os.environ.copy()
        environment.update({"HOME": str(home), "USER": username, "LOGNAME": username})
        result = subprocess.run([vncserver, "-kill", f":{display}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=20, check=False)
        if result.returncode == 0:
            terminated.append(str(display))
    state_path.unlink(missing_ok=True)
    return {"username": username, "provider": "tigervnc-localhost", "terminated_sessions": terminated}


def _catalog_file() -> Path:
    return Path(__file__).resolve().parents[1] / "catalog" / "apps.json"


def _provider_supports(provider: dict[str, Any], info: PlatformInfo) -> bool:
    platforms = {str(value) for value in provider.get("platforms", [])}
    arches = {normalize_architecture(str(value)) for value in provider.get("architectures", [])}
    return ("all" in platforms or info.mode in platforms) and ("all" in arches or normalize_architecture(info.architecture) in arches)


def _apt_candidate(package: str) -> bool:
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package):
        return False
    binary = shutil.which("apt-cache")
    if not binary:
        return False
    try:
        result = subprocess.run([binary, "policy", package], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=4, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.search(r"^\s*Candidate:\s*(\S+)\s*$", result.stdout, re.MULTILINE)
    return bool(match and match.group(1) != "(none)")


def _atomic_bytes(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(mode)
    os.replace(temporary, path)


def _download_https(url: str, *, max_bytes: int) -> bytes:
    if not str(url).startswith("https://"):
        raise BrokerError("invalid_provider_source", "provider download must use HTTPS")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LunarX-Home/3.1.2"})
        with urllib.request.urlopen(request, timeout=45) as response:
            final_url = str(response.geturl())
            if not final_url.startswith("https://"):
                raise BrokerError("invalid_provider_source", "provider download redirected to a non-HTTPS URL")
            declared = int(response.headers.get("Content-Length", "0") or 0)
            if declared > max_bytes:
                raise BrokerError("provider_download_too_large", "provider download exceeds the safe size limit")
            content = response.read(max_bytes + 1)
    except BrokerError:
        raise
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise BrokerError("provider_download_failed", "official provider download could not be retrieved", detail=str(exc)) from exc
    if len(content) > max_bytes:
        raise BrokerError("provider_download_too_large", "provider download exceeds the safe size limit")
    return content


def _gpg_fingerprints(content: bytes) -> set[str]:
    binary = shutil.which("gpg")
    if not binary:
        raise BrokerError("gpg_unavailable", "GnuPG is required to verify this application repository")
    with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
        temporary = Path(handle.name); handle.write(content)
    try:
        result = subprocess.run([binary, "--batch", "--show-keys", "--with-colons", str(temporary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20, check=False)
    finally:
        temporary.unlink(missing_ok=True)
    if result.returncode:
        raise BrokerError("repository_key_invalid", "the official repository signing key could not be inspected")
    return {row.split(":")[9].upper() for row in result.stdout.splitlines() if row.startswith("fpr:") and len(row.split(":")) > 9}


def _prepare_vendor_apt(provider_id: str) -> tuple[list[str], str]:
    definition = VENDOR_APT.get(provider_id)
    if not definition:
        raise BrokerError("invalid_provider", "vendor APT provider is not allow-listed")
    apt_get = shutil.which("apt-get")
    if not apt_get:
        raise BrokerError("apt_unavailable", "APT is not available on this host")
    key_data = _download_https(str(definition["key_url"]), max_bytes=2 * 1024 * 1024)
    expected = str(definition.get("fingerprint", "")).upper()
    if expected and expected not in _gpg_fingerprints(key_data):
        raise BrokerError("repository_key_mismatch", "official repository signing-key fingerprint did not match the audited value")
    key_path = Path(str(definition["key_path"]))
    if definition.get("dearmor"):
        gpg = shutil.which("gpg")
        if not gpg:
            raise BrokerError("gpg_unavailable", "GnuPG is required to install this application repository")
        key_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
            source_key = Path(handle.name); handle.write(key_data)
        temporary_key = key_path.with_name(f".{key_path.name}.lunarx-{os.getpid()}")
        try:
            result = subprocess.run([gpg, "--batch", "--yes", "--dearmor", "--output", str(temporary_key), str(source_key)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
            if result.returncode:
                raise BrokerError("repository_key_invalid", "repository key conversion failed")
            temporary_key.chmod(0o644); os.replace(temporary_key, key_path)
        finally:
            source_key.unlink(missing_ok=True); temporary_key.unlink(missing_ok=True)
    else:
        _atomic_bytes(key_path, key_data)
    source_path = Path(str(definition["source_path"]))
    if definition.get("source_url"):
        source_data = _download_https(str(definition["source_url"]), max_bytes=256 * 1024)
        if b"http://" in source_data:
            raise BrokerError("repository_source_invalid", "official repository definition contains an insecure HTTP source")
        _atomic_bytes(source_path, source_data)
    else:
        _atomic_bytes(source_path, str(definition["source_content"]).encode("utf-8"))
    files = [str(key_path), str(source_path)]
    if definition.get("preferences_path"):
        pref = Path(str(definition["preferences_path"]))
        _atomic_bytes(pref, str(definition.get("preferences_content", "")).encode("utf-8"))
        files.append(str(pref))
    update = subprocess.run([apt_get, "update"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, check=False)
    if update.returncode:
        raise BrokerError("apt_repository_failed", "APT could not refresh the official application repository", detail=(update.stderr or update.stdout).decode("utf-8", "replace")[-240:])
    package = str(definition["package"])
    if not _apt_candidate(package):
        raise BrokerError("apt_package_unavailable", "the official repository did not expose the audited application package for this host")
    return files, package


def _install_pinned_deb(provider_id: str) -> tuple[str, str]:
    definition = PINNED_DEB.get(provider_id)
    if not definition:
        raise BrokerError("invalid_provider", "pinned DEB provider is not allow-listed")
    apt_get = shutil.which("apt-get")
    if not apt_get:
        raise BrokerError("apt_unavailable", "APT is not available on this host")
    content = _download_https(str(definition["url"]), max_bytes=300 * 1024 * 1024)
    if hashlib.sha256(content).hexdigest().lower() != str(definition["sha256"]).lower():
        raise BrokerError("provider_checksum_mismatch", "downloaded application package did not match the audited SHA-256")
    with tempfile.NamedTemporaryFile("wb", suffix=".deb", delete=False) as handle:
        package_file = Path(handle.name); handle.write(content)
    try:
        result = subprocess.run([apt_get, "install", "-y", str(package_file)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, check=False)
    finally:
        package_file.unlink(missing_ok=True)
    if result.returncode:
        raise BrokerError("apt_action_failed", "APT could not install the verified application package", detail=(result.stderr or result.stdout).decode("utf-8", "replace")[-240:])
    package = str(definition["package"]); version = _dpkg_version(package) or str(definition["version"])
    return package, version


def _cleanup_vendor_repository(provider_id: str) -> None:
    definition = VENDOR_APT.get(provider_id)
    if not definition:
        return
    for key in ("source_path", "preferences_path", "key_path"):
        value = definition.get(key)
        if value:
            Path(str(value)).unlink(missing_ok=True)


def _dpkg_version(package: str) -> str:
    binary = shutil.which("dpkg-query")
    if not binary:
        return ""
    try:
        result = subprocess.run([binary, "-W", "-f=${Status}\t${Version}", package], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=4, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0 or not result.stdout.startswith("install ok installed\t"):
        return ""
    return result.stdout.split("\t", 1)[1].strip()


def _installed_records(state: dict[str, Any], username: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    per_user = state.get("installed_apps", {})
    if isinstance(per_user, dict) and isinstance(per_user.get(username), list):
        output.extend(dict(item) for item in per_user[username] if isinstance(item, dict) and item.get("id"))
    managed = state.get("managed_apps", {})
    if isinstance(managed, dict):
        for app_id, item in managed.items():
            if not isinstance(item, dict) or not item.get("installed"):
                continue
            packages = [str(value) for value in item.get("packages", []) if str(value)]
            versions = [value for value in (_dpkg_version(package) for package in packages) if value]
            if packages and len(versions) != len(packages):
                continue
            record = dict(item)
            record["id"] = str(app_id)
            record["version"] = ", ".join(versions) if versions else str(item.get("version") or "system")
            record["scope"] = "system"
            output.append(record)
    dedup: dict[str, dict[str, Any]] = {}
    for item in output:
        dedup[str(item.get("id"))] = item
    return list(dedup.values())


def _store_user_app(state: dict[str, Any], username: str, record: dict[str, Any] | None) -> None:
    store = state.setdefault("installed_apps", {})
    values = [dict(item) for item in store.get(username, []) if isinstance(item, dict) and item.get("id") != (record or {}).get("id")]
    if record is not None:
        values.append(record)
    store[username] = values


def _portable_user_app(payload: dict[str, Any], state: dict[str, Any], config: PathConfig, info: PlatformInfo) -> dict[str, Any]:
    username = validate_username(str(payload.get("username", "")))
    user = state.get("users", {}).get(username)
    if not isinstance(user, dict):
        raise BrokerError("user_not_found", "user not found")
    app_id = str(payload.get("app_id", ""))
    operation = str(payload.get("operation", ""))
    if operation not in {"install", "launch", "update", "uninstall"}:
        raise BrokerError("invalid_operation", "unsupported application operation")
    try:
        app = find_app(str(_catalog_file()), app_id)
        provider = find_provider(app, str(payload.get("provider_id", "")))
    except CatalogError as exc:
        raise BrokerError("invalid_provider", "application provider is not allow-listed", detail=str(exc)) from exc
    if not _provider_supports(provider, info) or provider.get("provider") == "unsupported":
        raise BrokerError("app_provider_unavailable", str(provider.get("reason") or "provider is unavailable on this platform"))
    if bool(provider.get("requires_admin")) and not bool(user.get("is_admin", user.get("role") == "admin")):
        raise BrokerError("admin_required", "the selected application provider requires an administrator")
    kind = str(provider.get("provider", ""))
    now = int(time.time())
    if kind == "web-pwa":
        launch_url = str(provider.get("launch_url", ""))
        if operation in {"install", "update"}:
            _store_user_app(state, username, {"id": app_id, "name": app.get("name", app_id), "provider": kind, "provider_id": provider.get("id"), "scope": "user", "version": "web", "installed_at": now, "launch_url": launch_url, "update_available": False})
            save_state(state, config)
            return {"message": "Web application added to LunarX", "provider": kind, "scope": "user", "version": "web", "launch_url": launch_url}
        if operation == "uninstall":
            _store_user_app(state, username, {"id": app_id})
            # _store_user_app with a non-None placeholder would re-add it; remove cleanly.
            values = [item for item in state.setdefault("installed_apps", {}).get(username, []) if isinstance(item, dict) and item.get("id") != app_id]
            state["installed_apps"][username] = values
            save_state(state, config)
            return {"message": "Web application removed from LunarX", "provider": kind, "scope": "user"}
        return {"message": "Opening official web application", "provider": kind, "scope": "user", "launch_url": launch_url, "version": "web"}
    if kind in {"apt-system", "admin-apt-group", "vendor-apt", "pinned-deb"}:
        packages = [str(value) for value in provider.get("packages", [])]
        if not packages:
            raise BrokerError("invalid_provider", "application package list is empty")
        apt_get = shutil.which("apt-get")
        if not apt_get:
            raise BrokerError("apt_unavailable", "APT is not available on this host")
        repository_files: list[str] = []
        if operation in {"install", "update"}:
            if kind in {"apt-system", "admin-apt-group"}:
                if any(not _apt_candidate(package) for package in packages):
                    raise BrokerError("apt_package_unavailable", "required package is not available from the configured APT repositories")
                args = [apt_get, "install", "-y"] + (["--only-upgrade"] if operation == "update" else []) + packages
                result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, check=False)
                if result.returncode:
                    raise BrokerError("apt_action_failed", "APT could not complete the application action", detail=(result.stderr or result.stdout).decode("utf-8", "replace")[-240:])
                version = ", ".join(filter(None, (_dpkg_version(package) for package in packages))) or "system"
            elif kind == "vendor-apt":
                repository_files, verified_package = _prepare_vendor_apt(str(provider.get("id")))
                if verified_package not in packages:
                    raise BrokerError("invalid_provider", "catalog package does not match the audited vendor repository")
                args = [apt_get, "install", "-y"] + (["--only-upgrade"] if operation == "update" else []) + packages
                result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, check=False)
                if result.returncode:
                    raise BrokerError("apt_action_failed", "APT could not complete the vendor application action", detail=(result.stderr or result.stdout).decode("utf-8", "replace")[-240:])
                version = ", ".join(filter(None, (_dpkg_version(package) for package in packages))) or "system"
            else:
                package, version = _install_pinned_deb(str(provider.get("id")))
                if package not in packages:
                    raise BrokerError("invalid_provider", "catalog package does not match the audited pinned package")
            state.setdefault("managed_apps", {})[app_id] = {"installed": True, "name": app.get("name", app_id), "provider": kind, "provider_id": provider.get("id"), "scope": "system", "packages": packages, "version": version, "available_version": str(provider.get("available_version") or version), "repository_files": repository_files, "updated_at": now, "update_available": False}
            save_state(state, config)
            return {"message": f"{operation} completed", "provider": kind, "scope": "system", "version": version}
        if operation == "uninstall":
            result = subprocess.run([apt_get, "remove", "-y", *packages], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, check=False)
            if result.returncode:
                raise BrokerError("apt_action_failed", "APT could not remove the application", detail=(result.stderr or result.stdout).decode("utf-8", "replace")[-240:])
            if kind == "vendor-apt":
                _cleanup_vendor_repository(str(provider.get("id")))
            state.setdefault("managed_apps", {}).pop(app_id, None)
            save_state(state, config)
            return {"message": "uninstall completed", "provider": kind, "scope": "system"}
        command = [str(value) for value in provider.get("command", []) if str(value)]
        if not command:
            raise BrokerError("app_not_launchable", "this system package does not expose a launch action")
        executable = shutil.which(command[0])
        if not executable:
            raise BrokerError("app_not_installed", "application executable is not installed")
        display, _port, state_path = _portable_desktop_identity(username, config)
        if not state_path.is_file():
            raise BrokerError("desktop_required", "open the LunarX Desktop before launching a graphical application")
        environment = os.environ.copy()
        environment.update({"HOME": str(config.user_home(username)), "USER": username, "LOGNAME": username, "DISPLAY": f":{display}"})
        subprocess.Popen([executable, *command[1:]], env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        return {"message": "Application launch requested in the active Desktop", "provider": kind, "scope": "system", "version": _dpkg_version(packages[0])}
    raise BrokerError("app_provider_unavailable", "this provider requires the native Ubuntu helper")


def _service_status(info: PlatformInfo, units: list[object] | None = None) -> dict[str, Any]:
    names = [str(item) for item in (units or []) if str(item) and len(str(item)) < 160]
    return {"service_manager": info.service_manager, "supported": info.service_manager != "portable", "units": [{"unit": name, "state": "unknown", "message": "Use lunarxctl on the host to inspect this service"} for name in names]}


def handle(payload: dict[str, Any], *, paths: PathConfig | None = None, info: PlatformInfo | None = None) -> tuple[bool, dict[str, Any]]:
    config = paths or resolve_paths()
    platform_info = info or detect_platform(paths=config)
    action = str(payload.get("action", "")) if isinstance(payload, dict) else ""
    allowed = {"authenticate", "list_users", "create_user", "update_user", "delete_user", "settings_update", "storage_status", "storage_loopback_test", "prepare_storage_directory", "normalize_storage", "status", "restart_service", "user_app_list", "user_app", "server_app", "storage_register", "storage_grant", "provision_existing_user", "desktop_session", "terminate_desktop"}
    if action not in allowed:
        return False, BrokerError("unsupported_action", "broker action is not allowed").public()
    try:
        config.ensure_dirs()
        state = load_state(config)
        if action == "authenticate":
            username = validate_username(str(payload.get("username", "")))
            item = state["users"].get(username)
            authenticated = bool(item and item.get("enabled", True) and verify_password(str(payload.get("password", "")), str(item.get("password_hash", ""))))
            return True, {"authenticated": authenticated, "auth_provider": "local-scrypt"}
        if action == "list_users":
            return True, {"users": [_user_public(name, item) for name, item in sorted(state["users"].items()) if isinstance(item, dict)]}
        if action in {"create_user", "provision_existing_user"}:
            username = validate_username(str(payload.get("username", "")))
            if username in state["users"] and action == "create_user":
                raise BrokerError("user_exists", "user already exists")
            existing = state["users"].get(username, {}) if isinstance(state["users"].get(username), dict) else {}
            password = payload.get("password")
            item = dict(existing)
            if password:
                item["password_hash"] = hash_password(str(password))
            elif not item.get("password_hash"):
                raise BrokerError("password_required", "password is required")
            item.update({"display_name": str(payload.get("display_name") or existing.get("display_name") or username.title()), "enabled": True, "auth_provider": "local-scrypt"})
            item["is_admin"] = bool(payload.get("is_admin", existing.get("is_admin", True if action == "provision_existing_user" else False)))
            item["role"] = "admin" if item["is_admin"] else "user"
            item["permissions"] = dict(default_permissions())
            if isinstance(payload.get("permissions"), dict):
                item["permissions"].update({key: bool(value) for key, value in payload["permissions"].items() if key in item["permissions"]})
            item["quota_gib"] = int(payload.get("quota_gib", existing.get("quota_gib", state["settings"].get("default_quota_gib", 64))))
            state["users"][username] = item
            _ensure_user_dirs(config, username)
            save_state(state, config)
            return True, {"username": username, "user": _user_public(username, item)}
        if action == "update_user":
            username = validate_username(str(payload.get("username", "")))
            item = state["users"].get(username)
            if not isinstance(item, dict):
                raise BrokerError("user_not_found", "user not found")
            new_name = validate_username(str(payload["new_username"])) if payload.get("new_username") else username
            if new_name != username and new_name in state["users"]:
                raise BrokerError("user_exists", "new username already exists")
            for key in ("display_name", "theme", "avatar"):
                if key in payload:
                    item[key] = str(payload[key])[:14 * 1024 * 1024]
            if "enabled" in payload:
                item["enabled"] = bool(payload["enabled"])
            if "password" in payload and str(payload["password"]):
                item["password_hash"] = hash_password(str(payload["password"]))
            if "quota_gib" in payload:
                item["quota_gib"] = max(1, min(1_000_000, int(payload["quota_gib"])))
            if isinstance(payload.get("permissions"), dict):
                current = dict(default_permissions()); current.update(item.get("permissions", {})); current.update({key: bool(value) for key, value in payload["permissions"].items() if key in current}); item["permissions"] = current
            if new_name != username:
                state["users"][new_name] = item; del state["users"][username]
                old_root = config.data_root / "users" / username; new_root = config.data_root / "users" / new_name
                if old_root.exists() and not new_root.exists(): old_root.rename(new_root)
            save_state(state, config)
            return True, {"username": new_name, "user": _user_public(new_name, item)}
        if action == "delete_user":
            username = validate_username(str(payload.get("username", "")))
            if username not in state["users"]:
                raise BrokerError("user_not_found", "user not found")
            del state["users"][username]; save_state(state, config)
            return True, {"username": username, "data_preserved": (config.data_root / "users" / username).exists()}
        if action == "settings_update":
            settings = state.setdefault("settings", {})
            for key in ("shared_contribution_gib", "default_quota_gib", "desktop_idle_timeout_minutes"):
                if key in payload:
                    settings[key] = max(1, min(1_000_000, int(payload[key])))
            if "theme_mode" in payload and str(payload["theme_mode"]) in {"dark", "light", "system"}:
                settings["theme_mode"] = str(payload["theme_mode"])
            if "server_name" in payload:
                name = str(payload["server_name"]).strip()[:80]
                if name:
                    settings["server_name"] = name
            save_state(state, config)
            return True, {"settings": settings}
        if action == "storage_status":
            return True, {"storage": _storage_status(config, platform_info)}
        if action == "storage_loopback_test":
            probe = config.runtime_root / f"storage-self-test-{secrets.token_hex(8)}"
            probe.write_text("lunarx", encoding="ascii"); readback = probe.read_text(encoding="ascii"); probe.unlink(missing_ok=True)
            return True, {"passed": readback == "lunarx", "provider": "logical"}
        if action in {"prepare_storage_directory", "normalize_storage"}:
            username = validate_username(str(payload.get("username", "")))
            _ensure_user_dirs(config, username)
            raw_path = str(payload.get("path", ""))
            relative = _safe_relative(raw_path) if action == "prepare_storage_directory" and raw_path else None
            if relative is not None:
                target = (config.data_root / "users" / username / relative).resolve(strict=False)
                target.relative_to((config.data_root / "users" / username).resolve())
                target.mkdir(mode=0o770, parents=True, exist_ok=True)
            return True, {"normalized": True, "provider": "logical"}
        if action == "status":
            return True, _service_status(platform_info, payload.get("units") if isinstance(payload.get("units"), list) else None)
        if action == "restart_service":
            return False, BrokerError("service_manager_unavailable", "service control is unavailable in portable mode").public()
        if action == "desktop_session":
            username = validate_username(str(payload.get("username", "")))
            if username not in state.get("users", {}):
                raise BrokerError("user_not_found", "user not found")
            return True, _portable_desktop_session(username, config, platform_info)
        if action == "terminate_desktop":
            username = validate_username(str(payload.get("username", "")))
            return True, _portable_desktop_terminate(username, config)
        if action == "user_app_list":
            username = validate_username(str(payload.get("username", "")))
            if username not in state.get("users", {}):
                raise BrokerError("user_not_found", "user not found")
            apps = _installed_records(state, username)
            return True, {"apps": apps, "provider": "platform-aware", "providers": sorted({str(item.get("provider", "unknown")) for item in apps})}
        if action == "user_app":
            return True, _portable_user_app(payload, state, config, platform_info)
        if action == "server_app":
            operation = str(payload.get("operation", "")); app_id = str(payload.get("app_id", ""))
            if operation not in {"install", "uninstall", "update"}:
                raise BrokerError("invalid_operation", "unsupported server application operation")
            state.setdefault("server_apps", {})[app_id] = {"installed": operation != "uninstall", "version": "3.1.2", "updated_at": int(time.time())}
            save_state(state, config); return True, {"app_id": app_id, "operation": operation}
        if action == "storage_register":
            member = stable_disk_id(str(payload.get("device") or payload.get("stable_id") or payload.get("source") or ""))
            state.setdefault("storage", {}).setdefault("members", [])
            members = state["storage"]["members"]
            if not any((item.get("source") if isinstance(item, dict) else item) == member for item in members):
                members.append({"source": member, "name": str(payload.get("name") or member)[0:64], "online": False, "formatting": False, "grants": []})
            save_state(state, config); return True, {"member": member, "registered": True, "formatting": False}
        if action == "storage_grant":
            name = str(payload.get("name") or "")[:64]; username = validate_username(str(payload.get("username") or "")); access = str(payload.get("access") or "rw")
            if access not in {"ro", "rw", "none"}:
                raise BrokerError("invalid_disk_access", "invalid disk access")
            members = state.setdefault("storage", {}).setdefault("members", [])
            selected = next((item for item in members if isinstance(item, dict) and (item.get("name") == name or item.get("source") == name)), None)
            if not isinstance(selected, dict):
                raise BrokerError("disk_not_found", "additional disk is not registered")
            grants = [item for item in selected.get("grants", []) if isinstance(item, dict) and item.get("username") != username]
            if access != "none": grants.append({"username": username, "access": access})
            selected["grants"] = grants; save_state(state, config); return True, {"name": name, "username": username, "access": access}
        return False, BrokerError("unsupported_action", "broker action is not allowed").public()
    except BrokerError as exc:
        return False, exc.public()
    except (AuthError, OSError, ValueError, TypeError, OverflowError) as exc:
        return False, BrokerError("broker_failure", "broker operation could not be completed", detail=str(exc)).public()


__all__ = ["BrokerError", "DEFAULT_STATE", "handle", "load_state", "save_state"]

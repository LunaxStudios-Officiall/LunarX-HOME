#!/usr/bin/env python3
"""LunarX Home: small dependency-light, server-side authenticated home portal."""

from __future__ import annotations

import base64
import binascii
import ctypes
import ctypes.util
import datetime as dt
import errno
import email
import fcntl
import grp
import hashlib
import hmac
import io
import json
import mimetypes
import os
import pty
import posixpath
import pwd
import re
import secrets
import select
import signal
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import zipfile
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, ImageOps, UnidentifiedImageError

from lunarx_core.appstore import AppJobRegistry, annotate_catalog, catalog_path, installed_for_user, public_catalog, start_job
from lunarx_core.auth import AuthError, validate_password, validate_username
from lunarx_core.broker import handle as broker_handle
from lunarx_core.catalog import CatalogError
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import capabilities as platform_capabilities, detect_platform
from lunarx_core.providers import provider_snapshot
from lunarx_core.remote import tailscale_status
from lunarx_core.quota import directory_size as quota_directory_size
from lunarx_core.storage import primary_storage_ready


APP_VERSION = "3.1.2"
PATHS = resolve_paths(Path(__file__).resolve().parent)
PLATFORM_INFO = detect_platform(paths=PATHS)
DATA_ROOT = PATHS.data_root
CONFIG_ROOT = PATHS.config_root
STATIC_ROOT = Path(__file__).resolve().parent / "static"
STATE_ROOT = PATHS.state_root
MEDIA_STATE_ROOT = STATE_ROOT / "media"
MEDIA_CACHE_ROOT = STATE_ROOT / "media-cache"
RUNTIME_ROOT = PATHS.runtime_root
DESKTOP_ACTIVITY_PATH = RUNTIME_ROOT / "desktop-activity.json"
RESOURCE_STATUS_PATH = RUNTIME_ROOT / "resource-status.json"
SESSION_TTL = 8 * 60 * 60
MAX_JSON = 64 * 1024
MAX_PROFILE_JSON = 14 * 1024 * 1024
MAX_UPLOAD = 100 * 1024 * 1024
# Browser folder uploads stream one file at a time. 25 GiB is the maximum
# supported size for one streamed file. The total batch is bounded by the
# destination's existing filesystem/project quota, not by an artificial cap.
MAX_STREAM_UPLOAD = 25 * 1024 * 1024 * 1024
MAX_NAME = 160
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
SAFE_NAME_RE = re.compile(r"^[^/\\\x00-\x1f\x7f]{1,160}$")
ALLOWED_STATUS_UNITS = (
    "lunarx-home.service",
    "xrdp.service",
    "xrdp-sesman.service",
    "docker.service",
    "lunarx-storage-scan.timer",
    "lunarx-desktop-governor.timer",
)


class StorageUnavailableError(RuntimeError):
    """The managed data pool is not mounted or is not currently reachable."""


def _storage_available() -> bool:
    try:
        return primary_storage_ready(DATA_ROOT)
    except OSError:
        return False


def _require_storage() -> None:
    if not _storage_available():
        raise StorageUnavailableError("storage unavailable")


_session_lock = threading.RLock()
_sessions: dict[str, dict[str, Any]] = {}
_login_lock = threading.RLock()
_login_attempts: dict[str, list[float]] = {}
_secret_box = Fernet(Fernet.generate_key())
_terminal_lock = threading.RLock()
_terminal_tickets: dict[str, dict[str, Any]] = {}
_desktop_ticket_lock = threading.RLock()
_desktop_tickets: dict[str, dict[str, Any]] = {}
_setup_lock = threading.RLock()
_cpu_lock = threading.RLock()
_cpu_previous: tuple[int, int] | None = None
_cpu_ema: float | None = None
_media_lock = threading.RLock()
_activity_lock = threading.RLock()
_app_jobs = AppJobRegistry()


def _load_json_file(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else fallback
    except (OSError, ValueError):
        return fallback


def app_config() -> dict[str, Any]:
    return _load_json_file(PATHS.config_file, {})


def home_state() -> dict[str, Any]:
    fallback = {"schema_version": APP_VERSION, "settings": {"shared_contribution_gib": 15, "default_quota_gib": 64, "desktop_idle_timeout_minutes": 10, "theme_mode": "dark", "server_name": "LunarX Home"}, "users": {}, "server_apps": {}, "installed_apps": {}, "storage": {"provider": PLATFORM_INFO.quota_mode, "members": []}}
    state = _load_json_file(PATHS.state_file, fallback)
    for key, value in fallback.items():
        if key not in state:
            state[key] = value
    return state


def user_profile(username: str) -> dict[str, Any]:
    state = home_state()
    profile = state.get("users", {}).get(username, {}) if isinstance(state.get("users"), dict) else {}
    permissions = {"desktop_access": True, "desktop_app_install": True, "shared_access": True, "drive": True, "photos": True, "files": True}
    if isinstance(profile.get("permissions"), dict):
        permissions.update({key: bool(value) for key, value in profile["permissions"].items()})
    return {"username": username, "display_name": str(profile.get("display_name") or username.title()), "avatar": str(profile.get("avatar") or ""), "theme": str(profile.get("theme") or "dark"), "is_admin": _is_admin(username), "role": "admin" if _is_admin(username) else "user", "auth_provider": str(profile.get("auth_provider") or ("local-scrypt" if profile else "pam")), "permissions": permissions}


def _pam_authenticate(username: str, password: str) -> bool:
    """Authenticate against the host PAM stack without storing the password."""
    if not username or not password or len(password) > 512:
        return False
    pam_name = ctypes.util.find_library("pam") or "libpam.so.0"
    libc_name = ctypes.util.find_library("c") or "libc.so.6"
    try:
        pam = ctypes.CDLL(pam_name)
        libc = ctypes.CDLL(libc_name)
    except OSError:
        return False

    class PamMessage(ctypes.Structure):
        _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]

    class PamResponse(ctypes.Structure):
        _fields_ = [("resp", ctypes.c_char_p), ("resp_retcode", ctypes.c_int)]

    class PamConv(ctypes.Structure):
        pass

    Conversation = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.POINTER(PamMessage)),
        ctypes.POINTER(ctypes.POINTER(PamResponse)),
        ctypes.c_void_p,
    )
    PamConv._fields_ = [("conv", Conversation), ("data", ctypes.c_void_p)]

    try:
        pam.pam_start.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(PamConv),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        pam.pam_start.restype = ctypes.c_int
        pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
        pam.pam_authenticate.restype = ctypes.c_int
        pam.pam_acct_mgmt.argtypes = [ctypes.c_void_p, ctypes.c_int]
        pam.pam_acct_mgmt.restype = ctypes.c_int
        pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
        pam.pam_end.restype = ctypes.c_int
        libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        libc.calloc.restype = ctypes.c_void_p
        libc.strdup.argtypes = [ctypes.c_char_p]
        libc.strdup.restype = ctypes.c_void_p
        libc.free.argtypes = [ctypes.c_void_p]
        libc.free.restype = None
    except AttributeError:
        return False

    username_bytes = username.encode("utf-8", "strict")
    password_bytes = password.encode("utf-8", "strict")

    @Conversation
    def conversation(
        count: int,
        messages: ctypes.POINTER(ctypes.POINTER(PamMessage)),
        responses: ctypes.POINTER(ctypes.POINTER(PamResponse)),
        _data: ctypes.c_void_p,
    ) -> int:
        if count <= 0 or count > 32 or not messages or not responses:
            return 1
        response_mem = libc.calloc(count, ctypes.sizeof(PamResponse))
        if not response_mem:
            return 1
        response_ptr = ctypes.cast(response_mem, ctypes.POINTER(PamResponse))
        for index in range(count):
            style = messages[index].contents.msg_style
            if style in (1, 2):  # PAM_PROMPT_ECHO_OFF / PAM_PROMPT_ECHO_ON
                value = password_bytes if style == 1 else username_bytes
            else:
                value = b""
            allocated = libc.strdup(value)
            if not allocated:
                for prior in range(index):
                    if response_ptr[prior].resp:
                        libc.free(response_ptr[prior].resp)
                libc.free(response_mem)
                return 1
            response_ptr[index].resp = ctypes.cast(allocated, ctypes.c_char_p)
            response_ptr[index].resp_retcode = 0
        responses[0] = response_ptr
        return 0

    handle = ctypes.c_void_p()
    conversation_struct = PamConv(conversation, None)
    result = pam.pam_start(
        b"login", username.encode("utf-8", "strict"), ctypes.byref(conversation_struct), ctypes.byref(handle)
    )
    if result != 0:
        return False
    try:
        result = pam.pam_authenticate(handle, 0)
        if result == 0:
            result = pam.pam_acct_mgmt(handle, 0)
        return result == 0
    finally:
        pam.pam_end(handle, result)


def _human_user(username: str) -> pwd.struct_passwd | None:
    if not USERNAME_RE.fullmatch(username):
        return None
    try:
        record = pwd.getpwnam(username)
    except KeyError:
        return None
    if record.pw_uid < 1000 or record.pw_uid >= 65534:
        return None
    if record.pw_shell in {"/usr/sbin/nologin", "/bin/false"}:
        return None
    return record


def _local_user(username: str) -> dict[str, Any] | None:
    value = home_state().get("users", {})
    item = value.get(username) if isinstance(value, dict) else None
    return item if isinstance(item, dict) else None


def _is_admin(username: str) -> bool:
    local = _local_user(username)
    if local is not None:
        return bool(local.get("is_admin", local.get("role") == "admin"))
    try:
        record = pwd.getpwnam(username)
        primary = grp.getgrgid(record.pw_gid).gr_name
    except KeyError:
        return False
    groups = {primary}
    for group in grp.getgrall():
        if username in group.gr_mem:
            groups.add(group.gr_name)
    return bool(groups & {"sudo", "admin", "lunarx-admin"})


def _personal_base(username: str) -> Path:
    return DATA_ROOT / "users" / username


def _workspace_base(username: str) -> Path:
    return _personal_base(username) / "Workspace"


def _desktop_base(username: str) -> Path:
    return PATHS.desktop_path(username)


def _virtual_personal_alias(scope: str, relative: str, *, allow_hidden: bool = False) -> str | None:
    if scope != "personal":
        return None
    parts = _check_relative_path(relative, allow_hidden=allow_hidden)
    return parts[0] if parts[:1] in (["Workspace"], ["Desktop"]) else None


def _is_workspace_relative(scope: str, relative: str) -> bool:
    return _virtual_personal_alias(scope, relative) == "Workspace"


def _is_virtual_personal_relative(scope: str, relative: str, *, allow_hidden: bool = False) -> bool:
    return _virtual_personal_alias(scope, relative, allow_hidden=allow_hidden) is not None


def _check_relative_path(value: str, *, allow_hidden: bool = False) -> list[str]:
    value = unquote(value or "")
    if "\\" in value or value.startswith("/") or "\x00" in value:
        raise ValueError("invalid path")
    normalized = posixpath.normpath(value) if value else "."
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("invalid path")
    parts = [part for part in PurePosixPath(normalized).parts if part not in {"", "."}]
    if any(part in {"..", "."} or (part.startswith(".") and not allow_hidden) for part in parts):
        raise ValueError("invalid path")
    return parts


def _safe_storage_path(username: str, scope: str, relative: str, *, allow_hidden: bool = False) -> tuple[Path, Path]:
    if scope not in {"personal", "shared"}:
        raise ValueError("invalid scope")
    if scope == "shared" and not user_profile(username).get("permissions", {}).get("shared_access", False):
        raise PermissionError("shared access disabled")
    _require_storage()
    requested_parts = _check_relative_path(relative, allow_hidden=allow_hidden)
    alias = requested_parts[0] if scope == "personal" and requested_parts[:1] in (["Workspace"], ["Desktop"]) else None
    if alias:
        base = (_workspace_base(username) if alias == "Workspace" else _desktop_base(username)).resolve(strict=False)
        if not base.is_dir():
            raise ValueError("workspace unavailable")
        parts = requested_parts[1:]
    else:
        base = _personal_base(username) if scope == "personal" else DATA_ROOT / "shared"
        parts = requested_parts
    base = base.resolve(strict=False)
    candidate = base.joinpath(*parts)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("path escapes storage root") from exc
    # Do not follow user-created symlinks in the broker path, even if they point inside the pool.
    cursor = base
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("symlinks are not supported")
    return base, candidate


def _session_from_request(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
    except Exception:
        return None
    morsel = cookie.get("lx_session")
    if morsel is None:
        return None
    token = morsel.value
    now = time.time()
    with _session_lock:
        session = _sessions.get(token)
        if not session:
            return None
        if now - float(session["last_seen"]) > SESSION_TTL:
            _sessions.pop(token, None)
            return None
        session["last_seen"] = now
        return dict(session)


def _new_session(username: str, password: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    with _session_lock:
        _sessions[token] = {"username": username, "csrf": csrf, "desktop_secret": _secret_box.encrypt(password.encode("utf-8")), "created": time.time(), "last_seen": time.time()}
    return token, csrf


def _allow_login(key: str) -> bool:
    now = time.time()
    with _login_lock:
        recent = [stamp for stamp in _login_attempts.get(key, []) if now - stamp < 60]
        if len(recent) >= 8:
            _login_attempts[key] = recent
            return False
        recent.append(now)
        _login_attempts[key] = recent
        return True


def _clear_login_attempts(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)


def _read_json_body(handler: BaseHTTPRequestHandler, limit: int = MAX_JSON) -> dict[str, Any]:
    raw_length = handler.headers.get("Content-Length")
    try:
        length = int(raw_length or "-1")
    except ValueError as exc:
        raise ValueError("invalid content length") from exc
    if length < 0 or length > limit:
        raise ValueError("request too large")
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ValueError("incomplete request")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("object required")
    return value


def _parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict[str, str], str, bytes]:
    content_type = handler.headers.get("Content-Type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("multipart required")
    _, _, boundary_part = content_type.partition("boundary=")
    boundary = boundary_part.strip().strip('"').encode("utf-8")
    if not boundary or len(boundary) > 200:
        raise ValueError("invalid boundary")
    raw_length = handler.headers.get("Content-Length")
    try:
        length = int(raw_length or "-1")
    except ValueError as exc:
        raise ValueError("invalid content length") from exc
    if length < 1 or length > MAX_UPLOAD:
        raise ValueError("upload too large")
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValueError("incomplete upload")
    fields: dict[str, str] = {}
    file_name = ""
    file_data = b""
    delimiter = b"--" + boundary
    for piece in body.split(delimiter)[1:]:
        if piece.startswith(b"--"):
            break
        piece = piece.lstrip(b"\r\n")
        header_blob, separator, data = piece.partition(b"\r\n\r\n")
        if not separator:
            continue
        data = data.rstrip(b"\r\n")
        message = email.message_from_bytes(header_blob + b"\r\n\r\n")
        disposition = message.get("Content-Disposition", "")
        params = dict(message.get_params(header="Content-Disposition", unquote=True) or [])
        field_name = params.get("name", "")
        if not field_name:
            continue
        if "filename" in params:
            file_name = params.get("filename", "")
            file_data = data
        else:
            fields[field_name] = data.decode("utf-8", "strict")
    if not file_name:
        raise ValueError("file is required")
    return fields, Path(file_name).name, file_data


def _stream_request_body(handler: BaseHTTPRequestHandler, target: Path) -> int:
    raw_length = handler.headers.get("Content-Length")
    try:
        length = int(raw_length or "-1")
    except ValueError as exc:
        raise ValueError("invalid content length") from exc
    if length < 0 or length > MAX_STREAM_UPLOAD:
        raise ValueError("upload exceeds the 25 GiB per-file limit")
    remaining = length
    with target.open("wb") as handle:
        while remaining:
            chunk = handler.rfile.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("incomplete upload")
            handle.write(chunk)
            remaining -= len(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    return length


def _quota_config() -> list[dict[str, Any]]:
    value = _load_json_file(PATHS.quotas_file, {"quotas": []})
    quotas = value.get("quotas", [])
    if isinstance(quotas, list) and quotas:
        return quotas
    state_users = home_state().get("users", {})
    if not isinstance(state_users, dict):
        return []
    return [{"username": str(username), "hard": f"{int(profile.get('quota_gib', 0) or 0)}G"} for username, profile in state_users.items() if isinstance(profile, dict) and int(profile.get("quota_gib", 0) or 0) > 0]


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in files:
                try:
                    item = Path(root) / name
                    if not item.is_symlink():
                        total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _workspace_stats(username: str) -> dict[str, int]:
    """Report the normal Workspace directory inside the user's primary quota."""
    root = _workspace_base(username)
    try:
        stat = os.statvfs(root)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
    except OSError:
        total = free = 0
    return {"total_bytes": total, "free_bytes": free, "used_bytes": max(total - free, 0)}


def _tree_has_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    if not path.is_dir():
        return False
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            if any((Path(root) / name).is_symlink() for name in dirs + files):
                return True
    except OSError:
        return True
    return False


def _archive_member_parts(name: str) -> list[str]:
    """Return safe relative parts for a ZIP member; reject traversal and links."""
    normalized = str(name or "").replace("\\", "/")
    if not normalized or normalized.startswith("/") or "\x00" in normalized:
        raise ValueError("invalid archive member")
    parts = [part for part in PurePosixPath(posixpath.normpath(normalized)).parts if part not in {"", "."}]
    if not parts or any(part in {"..", "."} or part.startswith(".") or not SAFE_NAME_RE.fullmatch(part) for part in parts):
        raise ValueError("invalid archive member")
    return parts


def _unique_file_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("too many conflicting names")


def _extract_zip(username: str, scope: str, relative: str, destination_relative: str) -> str:
    base, source = _safe_storage_path(username, scope, relative)
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise ValueError("a ZIP file is required")
    _, destination_dir = _safe_storage_path(username, scope, destination_relative)
    if not destination_dir.is_dir():
        raise ValueError("invalid extraction destination")
    folder_name = source.stem if SAFE_NAME_RE.fullmatch(source.stem or "") and not source.stem.startswith(".") else "Extraído"
    root = _unique_file_path(destination_dir / folder_name)
    root.mkdir(mode=0o770)
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > 10000:
                raise ValueError("archive contains too many entries")
            total_uncompressed = sum(max(0, int(info.file_size)) for info in infos)
            if total_uncompressed > 20 * 1024 * 1024 * 1024:
                raise ValueError("archive is too large to extract safely")
            _ensure_logical_quota(username, total_uncompressed)
            for info in infos:
                parts = _archive_member_parts(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError("symbolic links are not allowed in archives")
                target = (root.joinpath(*parts)).resolve(strict=False)
                target.relative_to(root.resolve(strict=False))
                is_directory = info.is_dir() or info.filename.endswith(("/", "\\"))
                if is_directory:
                    target.mkdir(mode=0o770, parents=True, exist_ok=True)
                    continue
                if target.exists():
                    raise ValueError("archive contains duplicate paths")
                target.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
                with archive.open(info, "r") as source_handle, target.open("wb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
        relative_root = root.relative_to(base).as_posix()
        # Workspace/Desktop are real user paths exposed as virtual Files roots;
        # they must not be sent through the pool-only normalization helper.
        if not _is_virtual_personal_relative(scope, destination_relative):
            _storage_helper("normalize_storage", username, scope, paths=[relative_root], recursive=True)
        return relative_root
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def _compress_zip(username: str, scope: str, paths: list[str], destination_relative: str, name: str) -> str:
    if not paths or len(paths) > 200:
        raise ValueError("select at least one item")
    if not SAFE_NAME_RE.fullmatch(name) or name.startswith("."):
        raise ValueError("invalid archive name")
    if not name.lower().endswith(".zip"):
        name += ".zip"
    base, destination_dir = _safe_storage_path(username, scope, destination_relative)
    if not destination_dir.is_dir():
        raise ValueError("invalid compression destination")
    sources: list[Path] = []
    planned_bytes = 0
    for relative in paths:
        _, source = _safe_storage_path(username, scope, str(relative))
        if not source.exists() or source in {_personal_base(username).resolve(), (DATA_ROOT / "shared").resolve()} or _tree_has_symlink(source):
            raise ValueError("invalid source")
        sources.append(source)
        if source.is_file():
            planned_bytes += source.stat().st_size
        else:
            planned_bytes += _directory_size(source)
    _ensure_logical_quota(username, planned_bytes)
    target = _unique_file_path(destination_dir / name)
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for source in sources:
                if source.is_dir():
                    children = list(source.rglob("*"))
                    if not children:
                        archive.writestr(source.name.rstrip("/") + "/", b"")
                    for child in children:
                        if child.is_symlink():
                            raise ValueError("symbolic links are not allowed")
                        arcname = (Path(source.name) / child.relative_to(source)).as_posix()
                        if child.is_dir():
                            archive.writestr(arcname.rstrip("/") + "/", b"")
                        else:
                            archive.write(child, arcname)
                else:
                    archive.write(source, source.name)
        relative_target = target.relative_to(base).as_posix()
        # Workspace/Desktop are outside the managed pool helper's path model.
        if not _is_virtual_personal_relative(scope, destination_relative):
            _storage_helper("normalize_storage", username, scope, paths=[relative_target])
        return relative_target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _metrics(username: str) -> dict[str, Any]:
    try:
        with open("/proc/loadavg", "r", encoding="ascii") as handle:
            load = handle.read().split()[:3]
    except OSError:
        load = []
    memory: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable", "MemFree"}:
                    memory[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    try:
        stat = os.statvfs(DATA_ROOT)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
    except OSError:
        total, free = 0, 0
    try:
        system_stat = os.statvfs("/")
        system_total = system_stat.f_blocks * system_stat.f_frsize
        system_free = system_stat.f_bavail * system_stat.f_frsize
    except OSError:
        system_total, system_free = 0, 0
    personal = _personal_base(username)
    shared = DATA_ROOT / "shared"
    quotas = _quota_config()
    limits: dict[str, int] = {}
    for item in quotas:
        match = re.fullmatch(r"(\d+)G", str(item.get("hard", "")))
        if match:
            limits[str(item.get("username"))] = int(match.group(1)) * 1024 ** 3
    cpu = _cpu_percent()
    personal_used = _directory_size(personal)
    shared_used = _directory_size(shared)
    workspace = _workspace_stats(username)
    personal_reserved = sum(value for name, value in limits.items() if name not in {"shared", "system"})
    reserved_total = sum(limits.values())
    resource_status = _load_json_file(RESOURCE_STATUS_PATH, {})
    memory_total = int(memory.get("MemTotal", 0)); memory_available = int(memory.get("MemAvailable", 0)); memory_used = max(0, memory_total - memory_available)
    memory.update({"used_bytes": memory_used, "available_bytes": memory_available, "total_bytes": memory_total, "used_percent": round(100 * memory_used / memory_total, 1) if memory_total else 0.0})
    return {
        "load": load,
        "cpu_percent": cpu,
        "memory": memory,
        "pool": {"total_bytes": total, "free_bytes": free, "used_bytes": max(total - free, 0)},
        "system_storage": {"total_bytes": system_total, "free_bytes": system_free, "used_bytes": max(system_total - system_free, 0)},
        "capacity": {
            "personal_reserved_bytes": personal_reserved,
            "shared_reserved_bytes": limits.get("shared", 0),
            "system_reserved_bytes": limits.get("system", 0),
            "total_reserved_bytes": reserved_total,
            "allocatable_bytes": max(total - reserved_total, 0),
        },
        "personal": {"used_bytes": personal_used, "quota_bytes": limits.get(username, 0), "free_bytes": max(limits.get(username, 0) - personal_used, 0)},
        "workspace": {**workspace, "storage_mode": "primary-quota"},
        "shared": {"used_bytes": shared_used, "quota_bytes": limits.get("shared", 0), "free_bytes": max(limits.get("shared", 0) - shared_used, 0)},
        "quotas": quotas,
        "uptime_seconds": _read_uptime(),
        "desktop": resource_status,
        "platform": PLATFORM_INFO.as_dict(),
        "version": APP_VERSION,
    }


def _cpu_percent() -> float:
    global _cpu_previous, _cpu_ema
    try:
        fields = [int(value) for value in Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]]
        idle, total = fields[3] + (fields[4] if len(fields) > 4 else 0), sum(fields)
    except (OSError, ValueError, IndexError):
        return 0.0
    with _cpu_lock:
        previous, _cpu_previous = _cpu_previous, (idle, total)
    if not previous or total <= previous[1]: return round(_cpu_ema or 0.0, 1)
    sample = max(0.0, min(100.0, 100.0 * (1.0 - (idle - previous[0]) / (total - previous[1]))))
    _cpu_ema = sample if _cpu_ema is None else 0.35 * sample + 0.65 * _cpu_ema
    return round(_cpu_ema, 1)


def _desktop_activity(username: str, event: str, keep: bool | None = None) -> dict[str, Any]:
    now = int(time.time())
    with _activity_lock:
        value = _load_json_file(DESKTOP_ACTIVITY_PATH, {}); item = value.get(username, {}) if isinstance(value.get(username), dict) else {}
        if event in {"connected", "input"}: item["last_input"] = now
        if event == "connected": item["last_connected"] = now
        if event == "heartbeat": item["last_heartbeat"] = now
        if event == "disconnected": item["last_disconnected"] = now
        if keep is not None: item["keep_until"] = now + 8 * 60 * 60 if keep else 0
        value[username] = item; RUNTIME_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=RUNTIME_ROOT, prefix=".desktop-activity-", delete=False) as handle:
            temp = Path(handle.name); json.dump(value, handle, separators=(",", ":")); handle.write("\n")
        os.chmod(temp, 0o640); os.replace(temp, DESKTOP_ACTIVITY_PATH)
        return {"keep_until": int(item.get("keep_until", 0)), "last_input": int(item.get("last_input", 0))}


def _session_token(handler: BaseHTTPRequestHandler) -> str | None:
    cookie = SimpleCookie()
    try: cookie.load(handler.headers.get("Cookie", ""))
    except Exception: return None
    value = cookie.get("lx_session")
    return value.value if value else None


def _desktop_data(session: dict[str, Any]) -> str:
    if PLATFORM_INFO.desktop_backend != "guacamole-rdp":
        raise RuntimeError("desktop session provider is not available on this platform")
    profile = user_profile(str(session["username"]))
    if not profile["permissions"].get("desktop_access"):
        raise PermissionError("desktop access disabled")
    secret_hex = os.environ.get("LUNARX_JSON_SECRET", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", secret_hex):
        raise RuntimeError("desktop integration unavailable")
    try: password = _secret_box.decrypt(session["desktop_secret"], ttl=SESSION_TTL).decode("utf-8")
    except (InvalidToken, KeyError, UnicodeDecodeError) as exc: raise PermissionError("desktop credentials expired") from exc
    key = bytes.fromhex(secret_hex)
    config = app_config()
    document = {"username": session["username"], "expires": int((time.time() + 45) * 1000), "connections": {"LunarX Desktop": {"id": f"lx-{session['username']}", "protocol": "rdp", "parameters": {"hostname": str(config.get("xrdp_host", "127.0.0.1")), "port": "3389", "username": session["username"], "password": password, "security": "any", "ignore-cert": "true", "resize-method": "display-update", "enable-wallpaper": "false", "enable-font-smoothing": "true"}}}}
    plain = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signed = hmac.new(key, plain, hashlib.sha256).digest() + plain
    padder = padding.PKCS7(128).padder(); padded = padder.update(signed) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    password = ""
    return base64.b64encode(encrypted).decode("ascii")


def _read_uptime() -> int:
    try:
        with open("/proc/uptime", "r", encoding="ascii") as handle:
            return int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _admin_names() -> list[str]:
    state = home_state()
    users = state.get("users", {}) if isinstance(state.get("users"), dict) else {}
    return [name for name, value in users.items() if isinstance(value, dict) and bool(value.get("is_admin", value.get("role") == "admin"))]


def _ensure_setup_token() -> str | None:
    """Guarantee a recoverable first-run path without weakening normal login."""
    with _setup_lock:
        admins = _admin_names()
        if admins:
            PATHS.setup_token_file.unlink(missing_ok=True)
            return None
        try:
            current = PATHS.setup_token_file.read_text(encoding="ascii").strip()
            if len(current) >= 24:
                return None
        except OSError:
            pass
        PATHS.config_root.mkdir(mode=0o750, parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        temporary = PATHS.setup_token_file.with_name(".setup-token.tmp")
        temporary.write_text(token + "\n", encoding="ascii")
        temporary.chmod(0o600)
        os.replace(temporary, PATHS.setup_token_file)
        return token


def _setup_state() -> dict[str, Any]:
    admins = _admin_names()
    token = ""
    try:
        token = PATHS.setup_token_file.read_text(encoding="ascii").strip()
    except OSError:
        pass
    return {"enabled": bool(token) and not admins, "required": bool(token) and not admins, "configured": bool(admins), "admin_count": len(admins), "platform": PLATFORM_INFO.as_dict(), "token_path": str(PATHS.setup_token_file) if not admins else None}


def _consume_setup(payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    status = _setup_state()
    if not status["enabled"]:
        return False, {"error": "first-run setup is disabled", "error_code": "setup_disabled"}
    try:
        supplied = str(payload.get("token", ""))
        expected = PATHS.setup_token_file.read_text(encoding="ascii").strip()
        if not supplied or not hmac.compare_digest(supplied, expected):
            return False, {"error": "setup token rejected", "error_code": "setup_token_invalid"}
        username = validate_username(str(payload.get("username", "")))
        password = validate_password(str(payload.get("password", "")))
    except (OSError, UnicodeError, AuthError):
        return False, {"error": "invalid first-run setup request", "error_code": "setup_invalid"}
    ok, result = broker_handle({"action": "create_user", "username": username, "password": password, "display_name": str(payload.get("display_name") or username.title()), "is_admin": True, "quota_gib": int(payload.get("quota_gib", home_state().get("settings", {}).get("default_quota_gib", 64)))}, paths=PATHS, info=PLATFORM_INFO)
    password = ""
    if not ok:
        return False, result
    theme = str(payload.get("theme") or "dark")
    if theme in {"dark", "light", "system"}:
        broker_handle({"action": "update_user", "username": username, "theme": theme}, paths=PATHS, info=PLATFORM_INFO)
    settings_payload: dict[str, Any] = {"action": "settings_update"}
    if payload.get("server_name"):
        settings_payload["server_name"] = str(payload.get("server_name"))
    if payload.get("theme_mode") in {"dark", "light", "system"}:
        settings_payload["theme_mode"] = str(payload.get("theme_mode"))
    if len(settings_payload) > 1:
        broker_handle(settings_payload, paths=PATHS, info=PLATFORM_INFO)
    try:
        PATHS.setup_token_file.unlink()
    except OSError:
        return False, {"error": "setup completed but token could not be invalidated", "error_code": "setup_invalidation_failed"}
    return True, {"configured": True, "username": username, "message": "first administrator created"}


def _helper(payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    # Android/PRoot and explicitly portable installs have no sudo/systemd
    # boundary.  They use the same structured action contract in-process.
    direct = os.environ.get("LUNARX_BROKER_MODE", "").lower() in {"direct", "portable"} or PLATFORM_INFO.is_proot
    if direct:
        return broker_handle(payload, paths=PATHS, info=PLATFORM_INFO)
    sudo = shutil.which("sudo")
    helper = "/usr/local/libexec/lunarx-admin"
    if not sudo or not Path(helper).exists():
        return False, {"error": "privileged action is not configured", "error_code": "broker_unavailable"}
    try:
        result = subprocess.run(
            [sudo, "-n", helper],
            input=(json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=1800 if payload.get("action") == "user_app" else 60,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, {"error": "privileged helper unavailable", "error_code": "broker_unavailable"}
    try:
        output = json.loads(result.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False, {"error": "privileged action returned an invalid response", "error_code": "broker_protocol"}
    if result.returncode != 0:
        return False, output if isinstance(output, dict) else {"error": "privileged action rejected", "error_code": "broker_rejected"}
    return True, output if isinstance(output, dict) else {"result": output}


def _storage_helper(action: str, username: str, scope: str, **values: Any) -> dict[str, Any]:
    ok, result = _helper({"action": action, "username": username, "scope": scope, **values})
    if not ok:
        raise RuntimeError(str(result.get("error") or "storage permission broker unavailable"))
    return result


def _workspace_quota_bytes(username: str) -> int:
    for item in _quota_config():
        if str(item.get("username")) != username:
            continue
        match = re.fullmatch(r"(\d+)G", str(item.get("hard", "")))
        if match:
            return int(match.group(1)) * 1024 ** 3
    return 0


def _workspace_logical_free(username: str) -> int:
    quota = _workspace_quota_bytes(username)
    personal = _directory_size(_personal_base(username))
    return max(quota - personal, 0)


def _ensure_logical_quota(username: str, additional_bytes: int) -> None:
    quota = _workspace_quota_bytes(username)
    if quota and _directory_size(_personal_base(username)) + max(0, int(additional_bytes)) > quota:
        raise OSError(errno.EDQUOT, "durable logical quota exceeded")


def _detected_mime(path: Path) -> str:
    try:
        result = subprocess.run(["/usr/bin/file", "--brief", "--mime-type", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)
        value = result.stdout.decode("ascii", "replace").strip().lower()
        return value if result.returncode == 0 else "application/octet-stream"
    except (OSError, subprocess.TimeoutExpired):
        return "application/octet-stream"


def _media_kind(path: Path, mime: str | None = None) -> str | None:
    detected = mime or _detected_mime(path)
    if detected in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif", "image/heic", "image/heif"}:
        try:
            with Image.open(path) as image:
                image.verify()
                if image.width < 1 or image.height < 1 or image.width * image.height > 120_000_000:
                    return None
            return "photo"
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            return None
    if detected.startswith("video/") or detected in {"application/mp4", "application/ogg"}:
        try:
            result = subprocess.run(["/usr/bin/ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type,width,height", "-of", "json", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=False)
            value = json.loads(result.stdout.decode("utf-8")) if result.returncode == 0 else {}
            return "video" if value.get("streams") else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
    return None


def _media_state(username: str) -> dict[str, Any]:
    fallback = {"albums": []}
    value = _load_json_file(PATHS.media_state_root / f"{username}.json", fallback)
    if not isinstance(value.get("albums"), list): value["albums"] = []
    return value


def _save_media_state(username: str, value: dict[str, Any]) -> None:
    PATHS.media_state_root.mkdir(mode=0o750, parents=True, exist_ok=True)
    target = PATHS.media_state_root / f"{username}.json"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=PATHS.media_state_root, prefix=".media-", delete=False) as handle:
        temp = Path(handle.name); json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True); handle.write("\n")
    os.chmod(temp, 0o640); os.replace(temp, target)


def _media_timestamp(path: Path, kind: str) -> int:
    if kind == "photo":
        try:
            with Image.open(path) as image:
                raw = image.getexif().get(36867) or image.getexif().get(306)
                if raw: return int(dt.datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())
        except (OSError, ValueError, TypeError, UnidentifiedImageError): pass
    elif kind == "video":
        try:
            result = subprocess.run(["/usr/bin/ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time", "-of", "default=nw=1:nk=1", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=False)
            raw = result.stdout.decode("utf-8", "replace").strip()
            if raw: return int(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except (OSError, ValueError, subprocess.TimeoutExpired): pass
    try: return int(path.stat().st_mtime)
    except OSError: return int(time.time())


def _media_library(username: str) -> list[dict[str, Any]]:
    _require_storage()
    base = _personal_base(username).resolve()
    state = _media_state(username)
    album_map: dict[str, list[str]] = {}
    for album in state.get("albums", []):
        if isinstance(album, dict):
            for path in album.get("items", []) if isinstance(album.get("items"), list) else []:
                album_map.setdefault(str(path), []).append(str(album.get("id", "")))
    output = []
    for root_name in ("Fotos", "Videos"):
        root = base / root_name
        if not root.is_dir(): continue
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink() or any(part.startswith(".") for part in path.relative_to(base).parts): continue
            mime = _detected_mime(path); kind = _media_kind(path, mime)
            if not kind: continue
            relative = path.relative_to(base).as_posix(); timestamp = _media_timestamp(path, kind)
            try: size = path.stat().st_size
            except OSError: continue
            output.append({"path": relative, "name": path.name, "kind": kind, "mime": mime, "size": size, "timestamp": timestamp, "date_group": dt.datetime.fromtimestamp(timestamp).strftime("%B %Y"), "albums": album_map.get(relative, [])})
    output.sort(key=lambda item: (-int(item["timestamp"]), str(item["name"]).casefold()))
    return output


def _safe_media_path(username: str, relative: str) -> Path:
    _, target = _safe_storage_path(username, "personal", relative)
    parts = target.relative_to(_personal_base(username).resolve()).parts
    if not parts or parts[0] not in {"Fotos", "Videos"}: raise ValueError("path is outside the media library")
    return target


def _media_thumbnail(username: str, relative: str) -> Path:
    source = _safe_media_path(username, relative)
    if not source.is_file(): raise ValueError("media not found")
    stat = source.stat(); key = hashlib.sha256(f"{username}\0{relative}\0{stat.st_mtime_ns}\0{stat.st_size}".encode()).hexdigest()
    directory = PATHS.media_cache_root / username; directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    target = directory / f"{key}.jpg"
    if target.is_file(): return target
    kind = _media_kind(source)
    if kind == "photo":
        with Image.open(source) as image:
            frame = ImageOps.exif_transpose(image); frame.thumbnail((640, 640)); frame.convert("RGB").save(target, "JPEG", quality=82, optimize=True)
    elif kind == "video":
        result = subprocess.run(["/usr/bin/ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "00:00:01", "-i", str(source), "-frames:v", "1", "-vf", "scale=640:-2:force_original_aspect_ratio=decrease", "-y", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        if result.returncode or not target.is_file(): raise ValueError("video thumbnail unavailable")
    else: raise ValueError("unsupported media")
    os.chmod(target, 0o640); return target


def _normalized_avatar(value: object) -> str:
    raw = str(value or "")
    if not raw: return ""
    if len(raw) > 14 * 1024 * 1024: raise ValueError("profile photo exceeds the 10 MB source limit")
    match = re.fullmatch(r"data:image/(?:jpeg|jpg|png|webp);base64,([A-Za-z0-9+/=\r\n]+)", raw)
    if not match: raise ValueError("profile photo must be JPEG, PNG or WebP")
    try: decoded = base64.b64decode(match.group(1), validate=True)
    except (ValueError, binascii.Error) as exc: raise ValueError("profile photo is malformed") from exc
    if len(decoded) > 10 * 1024 * 1024: raise ValueError("profile photo exceeds the 10 MB source limit")
    try:
        with Image.open(io.BytesIO(decoded)) as image:
            image.verify()
        with Image.open(io.BytesIO(decoded)) as image:
            if image.width < 1 or image.height < 1 or image.width * image.height > 80_000_000: raise ValueError("profile photo dimensions are invalid")
            frame = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"), (512, 512), method=Image.Resampling.LANCZOS)
            output = io.BytesIO(); frame.save(output, "WEBP", quality=82, method=6)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc: raise ValueError("profile photo content is invalid") from exc
    return "data:image/webp;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _ws_send(sock: socket.socket, payload: bytes, opcode: int = 1) -> None:
    header = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126: header += bytes([length])
    elif length < 65536: header += bytes([126]) + struct.pack("!H", length)
    else: header += bytes([127]) + struct.pack("!Q", length)
    sock.sendall(header + payload)


def _ws_receive(sock: socket.socket) -> tuple[int, bytes] | None:
    first = sock.recv(2)
    if len(first) < 2: return None
    opcode, length = first[0] & 0x0F, first[1] & 0x7F
    masked = bool(first[1] & 0x80)
    if length == 126: length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127: length = struct.unpack("!Q", sock.recv(8))[0]
    if length > 65536: return None
    mask = sock.recv(4) if masked else b""
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk: return None
        data += chunk
    if masked: data = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
    return opcode, data


def _terminal_bridge(sock: socket.socket, username: str) -> None:
    master, slave = pty.openpty()
    def child_setup() -> None:
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    process = subprocess.Popen(["/usr/bin/sudo", "-n", "/usr/local/libexec/lunarx-terminal", username], stdin=slave, stdout=slave, stderr=slave,
                               close_fds=True, preexec_fn=child_setup, env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM": "xterm-256color", "LANG": "C.UTF-8"})
    os.close(slave); sock.setblocking(False); last = time.time()
    try:
        _ws_send(sock, f"LunarX Terminal · sessão {username}\r\n".encode())
        while process.poll() is None and time.time() - last < 1800:
            readable, _, _ = select.select([sock, master], [], [], 1)
            if master in readable:
                try: data = os.read(master, 8192)
                except OSError: break
                if data: _ws_send(sock, data)
            if sock in readable:
                try: frame = _ws_receive(sock)
                except (OSError, ConnectionError): break
                if not frame or frame[0] == 8: break
                last = time.time()
                if frame[0] in {1, 2}:
                    try:
                        message = json.loads(frame[1].decode("utf-8"))
                        if message.get("type") == "input": os.write(master, str(message.get("data", "")).encode())
                        elif message.get("type") == "resize":
                            rows, cols = max(8, min(200, int(message.get("rows", 24)))), max(20, min(400, int(message.get("cols", 80))))
                            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                    except (ValueError, TypeError, UnicodeError): continue
    finally:
        try: os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError): pass
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        os.close(master)


def _desktop_vnc_bridge(ws_sock: socket.socket, port: int) -> None:
    target = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        target.settimeout(None)
        ws_sock.settimeout(None)
        while True:
            readable, _, _ = select.select([ws_sock, target], [], [], 30)
            if target in readable:
                data = target.recv(65536)
                if not data:
                    break
                _ws_send(ws_sock, data, 2)
            if ws_sock in readable:
                frame = _ws_receive(ws_sock)
                if not frame:
                    break
                opcode, data = frame
                if opcode == 8:
                    break
                if opcode == 9:
                    _ws_send(ws_sock, data, 10)
                    continue
                if opcode in {1, 2} and data:
                    target.sendall(data)
    except (OSError, ConnectionError):
        pass
    finally:
        try:
            target.close()
        except OSError:
            pass


class HomeHandler(BaseHTTPRequestHandler):
    server_version = "LunarXHome/0.1"
    sys_version = ""

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _security_headers(self) -> list[tuple[str, str]]:
        guac = str(app_config().get("guacamole_url", "/guacamole/"))
        guac_origin = urlparse(guac).scheme + "://" + urlparse(guac).netloc if urlparse(guac).netloc else "'self'"
        return [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "SAMEORIGIN"),
            ("Referrer-Policy", "same-origin"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            ("Content-Security-Policy", f"default-src 'self'; img-src 'self' data: https://dl.flathub.org; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src {guac_origin}; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"),
        ]

    def _finish(self, status: int, body: bytes, content_type: str, extra: list[tuple[str, str]] | None = None) -> None:
        self.send_response(status)
        for key, value in self._security_headers():
            self.send_header(key, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: dict[str, Any], extra: list[tuple[str, str]] | None = None) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._finish(status, body, "application/json; charset=utf-8", extra)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _auth(self, mutation: bool = False, admin: bool = False) -> dict[str, Any] | None:
        session = _session_from_request(self)
        if not session:
            self._error(HTTPStatus.UNAUTHORIZED, "authentication required")
            return None
        if mutation:
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:
                self._error(HTTPStatus.FORBIDDEN, "origin rejected")
                return None
            csrf = self.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(str(session.get("csrf", "")), csrf):
                self._error(HTTPStatus.FORBIDDEN, "csrf rejected")
                return None
        if admin and not _is_admin(str(session["username"])):
            self._error(HTTPStatus.FORBIDDEN, "administrator role required")
            return None
        return session

    def _static(self, resource: str) -> None:
        path = (STATIC_ROOT / resource).resolve()
        try:
            path.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._finish(HTTPStatus.OK, path.read_bytes(), content_type)

    def _file_response(self, target: Path, inline: bool = False, content_type: str | None = None) -> None:
        try:
            size = target.stat().st_size
            handle = target.open("rb")
        except OSError:
            self._error(HTTPStatus.FORBIDDEN, "file unavailable")
            return
        self.send_response(HTTPStatus.OK)
        for key, value in self._security_headers():
            self.send_header(key, value)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(target.name, safe='')}")
        self.end_headers()
        try:
            with handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _list_files(self, session: dict[str, Any], query: dict[str, list[str]]) -> None:
        scope = query.get("scope", ["personal"])[0]
        relative = query.get("path", [""])[0]
        try:
            base, target = _safe_storage_path(str(session["username"]), scope, relative)
        except StorageUnavailableError:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
            return
        except PermissionError:
            self._error(HTTPStatus.FORBIDDEN, "storage access disabled")
            return
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid storage path")
            return
        if not target.exists():
            self._error(HTTPStatus.NOT_FOUND, "folder not found")
            return
        if not target.is_dir():
            self._error(HTTPStatus.BAD_REQUEST, "folder required")
            return
        requested_parts = _check_relative_path(relative)
        entries = []
        try:
            for item in target.iterdir():
                if item.name.startswith(".") or item.is_symlink():
                    continue
                try:
                    stat = item.stat()
                except OSError:
                    continue
                entries.append({"name": item.name, "kind": "folder" if item.is_dir() else "file", "size": stat.st_size, "mtime": int(stat.st_mtime), "storage": "primary"})
        except OSError:
            self._error(HTTPStatus.FORBIDDEN, "storage unavailable")
            return
        if scope == "personal" and not relative:
            for alias, alias_path in (("Desktop", _desktop_base(str(session["username"]))), ("Workspace", _workspace_base(str(session["username"])) )):
                if any(str(item.get("name")) == alias for item in entries):
                    continue
                try:
                    alias_stat = alias_path.stat()
                    if alias_path.is_dir():
                        entries.append({"name": alias, "kind": "folder", "size": 0, "mtime": int(alias_stat.st_mtime)})
                except OSError:
                    pass
        entries.sort(key=lambda item: (item["kind"] != "folder", item["name"].lower()))
        if scope == "personal" and requested_parts[:1] in (["Workspace"], ["Desktop"]):
            suffix = target.relative_to(base).as_posix()
            alias = requested_parts[0]
            rel = alias if not suffix or suffix == "." else f"{alias}/{suffix}"
        else:
            rel = str(target.relative_to(base)) if target != base else ""
        self._json(HTTPStatus.OK, {"ok": True, "scope": scope, "path": rel, "entries": entries})

    def _download(self, session: dict[str, Any], query: dict[str, list[str]]) -> None:
        try:
            base, target = _safe_storage_path(str(session["username"]), query.get("scope", ["personal"])[0], query.get("path", [""])[0])
        except StorageUnavailableError:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
            return
        except PermissionError:
            self._error(HTTPStatus.FORBIDDEN, "storage access disabled")
            return
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid storage path")
            return
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "file not found")
            return
        self._file_response(target)

    def _terminal_websocket(self, parsed: Any) -> None:
        session = _session_from_request(self)
        query = parse_qs(parsed.query)
        ticket = query.get("ticket", [""])[0]
        token = _session_token(self)
        with _terminal_lock:
            item = _terminal_tickets.pop(ticket, None)
        if not session or not token or not item or item.get("session") != token or item.get("expires", 0) < time.time() or not _is_admin(str(session["username"])):
            self._error(HTTPStatus.UNAUTHORIZED, "terminal authorization expired"); return
        if self.headers.get("Upgrade", "").lower() != "websocket": self._error(HTTPStatus.BAD_REQUEST, "websocket required"); return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key: self._error(HTTPStatus.BAD_REQUEST, "websocket key required"); return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101); self.send_header("Upgrade", "websocket"); self.send_header("Connection", "Upgrade"); self.send_header("Sec-WebSocket-Accept", accept); self.end_headers()
        _terminal_bridge(self.connection, str(session["username"]))

    def _desktop_websocket(self, parsed: Any) -> None:
        session = _session_from_request(self)
        query = parse_qs(parsed.query)
        ticket = query.get("ticket", [""])[0]
        token = _session_token(self)
        with _desktop_ticket_lock:
            item = _desktop_tickets.get(ticket)
        if not session or not token or not item or item.get("session") != token or item.get("username") != session.get("username") or item.get("expires", 0) < time.time():
            self._error(HTTPStatus.UNAUTHORIZED, "desktop authorization expired")
            return
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._error(HTTPStatus.BAD_REQUEST, "websocket required")
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self._error(HTTPStatus.BAD_REQUEST, "websocket key required")
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        _desktop_vnc_bridge(self.connection, int(item["port"]))

    def _novnc_static(self, resource: str) -> None:
        if PLATFORM_INFO.desktop_backend != "tigervnc-novnc" or not _session_from_request(self):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        candidates = [Path("/usr/share/novnc"), Path("/usr/share/noVNC"), Path("/opt/novnc")]
        root = next((candidate.resolve() for candidate in candidates if (candidate / "vnc.html").is_file()), None)
        if root is None:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "noVNC assets unavailable")
            return
        path = (root / resource).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "noVNC asset unavailable")
            return
        body = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'self'; base-uri 'none'")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/admin/terminal/ws":
            self._terminal_websocket(parsed); return
        if parsed.path == "/api/desktop/ws":
            self._desktop_websocket(parsed); return
        if parsed.path.startswith("/desktop/novnc/"):
            self._novnc_static(parsed.path.removeprefix("/desktop/novnc/")); return
        if parsed.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "service": "lunarx-home", "version": APP_VERSION, "storage": _storage_available(), "platform": PLATFORM_INFO.mode})
            return
        if parsed.path == "/api/setup/status":
            self._json(HTTPStatus.OK, {"ok": True, **_setup_state()})
            return
        if parsed.path == "/" or parsed.path == "/index.html":
            self._static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/api/me":
            session = _session_from_request(self)
            if not session:
                self._json(HTTPStatus.OK, {"ok": True, "authenticated": False})
                return
            self._json(HTTPStatus.OK, {"ok": True, "authenticated": True, "user": user_profile(str(session["username"])), "csrf_token": session["csrf"]})
            return
        session = self._auth()
        if not session:
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/api/bootstrap":
            profile = user_profile(str(session["username"]))
            self._json(HTTPStatus.OK, {"ok": True, "user": profile, "apps": [{"id": "drive", "name": "Drive", "enabled": profile["permissions"].get("drive")}, {"id": "photos", "name": "Fotos", "enabled": profile["permissions"].get("photos")}, {"id": "files", "name": "Arquivos", "enabled": profile["permissions"].get("files")}, {"id": "store", "name": "Aplicativos", "enabled": profile["permissions"].get("desktop_app_install")}], "settings": home_state().get("settings", {}), "capabilities": platform_capabilities(PATHS), "setup": _setup_state()})
            return
        if parsed.path == "/api/capabilities":
            self._json(HTTPStatus.OK, {"ok": True, **platform_capabilities(PATHS), "providers": provider_snapshot(PATHS)})
            return
        if parsed.path == "/api/apps/catalog":
            profile = user_profile(str(session["username"]))
            if not profile["permissions"].get("desktop_app_install", False): self._error(HTTPStatus.FORBIDDEN, "personal application installation disabled"); return
            try:
                path = catalog_path(CONFIG_ROOT, Path(__file__).resolve().parent)
                result = public_catalog(path, query=query.get("q", [""])[0], category=query.get("category", ["all"])[0])
                result = annotate_catalog(result, platform_name=PLATFORM_INFO.mode, architecture=PLATFORM_INFO.architecture, backend=PLATFORM_INFO.app_backend)
                self._json(HTTPStatus.OK, {"ok": True, **result})
            except (CatalogError, OSError, ValueError) as exc: self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        if parsed.path == "/api/apps/installed":
            profile = user_profile(str(session["username"]))
            if not profile["permissions"].get("desktop_app_install", False): self._error(HTTPStatus.FORBIDDEN, "personal application installation disabled"); return
            installed = installed_for_user(_helper, str(session["username"]))
            self._json(HTTPStatus.OK if installed.get("available", False) else HTTPStatus.SERVICE_UNAVAILABLE, {"ok": bool(installed.get("available", False)), **installed})
            return
        if parsed.path == "/api/apps/jobs":
            job_id = query.get("id", [None])[0]
            self._json(HTTPStatus.OK, {"ok": True, "jobs": _app_jobs.for_user(str(session["username"]), job_id)})
            return
        if parsed.path == "/api/metrics":
            self._json(HTTPStatus.OK, {"ok": True, "metrics": _metrics(session["username"])})
            return
        if parsed.path == "/api/files":
            if query.get("download", ["0"])[0] == "1":
                self._download(session, query)
            else:
                self._list_files(session, query)
            return
        if parsed.path == "/api/media":
            profile = user_profile(str(session["username"]))
            if not profile.get("permissions", {}).get("photos", False): self._error(HTTPStatus.FORBIDDEN, "photos access disabled"); return
            try:
                with _media_lock:
                    media_state = _media_state(str(session["username"]))
                    self._json(HTTPStatus.OK, {"ok": True, "items": _media_library(str(session["username"])), "albums": media_state.get("albums", [])})
            except StorageUnavailableError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
            return
        if parsed.path in {"/api/media/content", "/api/media/thumbnail"}:
            try:
                relative = query.get("path", [""])[0]
                target = _safe_media_path(str(session["username"]), relative)
                if parsed.path.endswith("thumbnail"): target = _media_thumbnail(str(session["username"]), relative); mime = "image/jpeg"
                else: mime = _detected_mime(target)
                if not target.is_file() or (parsed.path.endswith("content") and not _media_kind(target, mime)): raise ValueError("media unavailable")
                self._file_response(target, inline=True, content_type=mime)
            except StorageUnavailableError: self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
            except (ValueError, OSError, subprocess.SubprocessError): self._error(HTTPStatus.NOT_FOUND, "media unavailable")
            return
        if parsed.path == "/api/desktop":
            try:
                profile = user_profile(str(session["username"]))
                if not profile["permissions"].get("desktop_access"):
                    raise PermissionError("desktop access disabled")
                if PLATFORM_INFO.desktop_backend == "tigervnc-novnc":
                    ok, result = _helper({"action": "desktop_session", "username": str(session["username"])})
                    if not ok:
                        raise RuntimeError(str(result.get("error") or "desktop provider unavailable"))
                    ticket = secrets.token_urlsafe(24)
                    token_value = _session_token(self)
                    with _desktop_ticket_lock:
                        now = time.time()
                        for key in [key for key, value in _desktop_tickets.items() if value.get("expires", 0) < now]:
                            _desktop_tickets.pop(key, None)
                        _desktop_tickets[ticket] = {"username": str(session["username"]), "session": token_value, "port": int(result["port"]), "expires": now + 3600}
                    fragment = "autoconnect=1&resize=remote&scale=1&shared=1&password=" + quote(str(result["password"]), safe="") + "&path=" + quote("/api/desktop/ws?ticket=" + ticket, safe="")
                    self._json(HTTPStatus.OK, {"ok": True, "guacamole_url": "/desktop/novnc/vnc.html#" + fragment, "provider": "tigervnc-novnc", "launch_mode": "authenticated-websocket", "expires_in_seconds": 3600, "keyboard_fullscreen_controls": True})
                else:
                    data = _desktop_data(session)
                    base_url = str(app_config().get("guacamole_url", "/guacamole/"))
                    self._json(HTTPStatus.OK, {"ok": True, "guacamole_url": f"{base_url}?data={quote(data, safe='')}", "provider": "guacamole-rdp", "launch_mode": "signed-single-use", "expires_in_seconds": 45, "keyboard_fullscreen_controls": True})
            except PermissionError as exc:
                self._error(HTTPStatus.FORBIDDEN, str(exc))
            except RuntimeError as exc:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        if parsed.path == "/api/tailscale":
            self._json(HTTPStatus.OK, {"ok": True, "tailscale": tailscale_status(PLATFORM_INFO, int(app_config().get("port", 8787)))})
            return
        if parsed.path == "/api/admin/users":
            if not self._auth(admin=True):
                return
            ok, result = _helper({"action": "list_users"})
            self._json(HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE, {"ok": ok, **result})
            return
        if parsed.path == "/api/admin/services":
            if not self._auth(admin=True):
                return
            ok, result = _helper({"action": "status", "units": list(ALLOWED_STATUS_UNITS)})
            self._json(HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE, {"ok": ok, **result})
            return
        if parsed.path == "/api/admin/storage":
            if not self._auth(admin=True):
                return
            ok, result = _helper({"action": "storage_status"})
            self._json(HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE, {"ok": ok, **result})
            return
        if parsed.path == "/api/admin/settings":
            if not self._auth(admin=True): return
            state = home_state(); self._json(HTTPStatus.OK, {"ok": True, "settings": state.get("settings", {}), "server_apps": state.get("server_apps", {})}); return
        if parsed.path == "/api/admin/apps":
            if not self._auth(admin=True): return
            state = home_state(); server_apps = state.get("server_apps", {}) if isinstance(state.get("server_apps"), dict) else {}; installed = server_apps.get("lunarx-status-page"); installed = installed if isinstance(installed, dict) else {}
            self._json(HTTPStatus.OK, {"ok": True, "apps": [{"id": "lunarx-status-page", "name": "LunarX Status Page", "description": "Página leve de estado gerenciada pelo Home", "installed": bool(installed.get("installed")), "version": installed.get("version")}]}); return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/setup":
            try:
                payload = _read_json_body(self)
            except (ValueError, UnicodeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid setup request")
                return
            ok, result = _consume_setup(payload)
            self._json(HTTPStatus.CREATED if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result})
            return
        if parsed.path == "/api/login":
            try:
                payload = _read_json_body(self)
                username = str(payload.get("username", ""))
                password = str(payload.get("password", ""))
            except (ValueError, UnicodeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid login request")
                return
            record = _human_user(username)
            local = _local_user(username)
            key = f"{self.client_address[0]}:{username.lower()}"
            if (not record and not local) or (local is not None and not local.get("enabled", True)) or len(password) > 512 or not _allow_login(key):
                self._error(HTTPStatus.UNAUTHORIZED, "invalid credentials")
                return
            helper_ok, helper_result = _helper({"action": "authenticate", "username": username, "password": password})
            authenticated = helper_ok and helper_result.get("authenticated") is True
            if not authenticated:
                password = ""
                self._error(HTTPStatus.UNAUTHORIZED, "invalid credentials")
                return
            _clear_login_attempts(key)
            token, csrf = _new_session(username, password)
            password = ""
            cookie = f"lx_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"
            if os.environ.get("LUNARX_SECURE_COOKIE") == "1" or self.headers.get("X-Forwarded-Proto", "").lower() == "https":
                cookie += "; Secure"
            self._json(HTTPStatus.OK, {"ok": True, "user": user_profile(username), "csrf_token": csrf}, [("Set-Cookie", cookie)])
            return

        session = self._auth(mutation=True)
        if not session:
            return
        username = str(session["username"])
        if parsed.path == "/api/logout":
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get("lx_session")
            if morsel:
                with _session_lock:
                    _sessions.pop(morsel.value, None)
            self._json(HTTPStatus.OK, {"ok": True}, [("Set-Cookie", "lx_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")])
            return
        if parsed.path == "/api/apps/action":
            profile = user_profile(username)
            if not profile["permissions"].get("desktop_app_install", False): self._error(HTTPStatus.FORBIDDEN, "personal application installation disabled"); return
            try:
                payload = _read_json_body(self)
                job = start_job(_app_jobs, catalog_path(CONFIG_ROOT, Path(__file__).resolve().parent), username, str(payload.get("app_id", "")), str(payload.get("operation", "")), _helper, is_admin=bool(profile["is_admin"]), platform_name=PLATFORM_INFO.mode, architecture=PLATFORM_INFO.architecture, backend=PLATFORM_INFO.app_backend)
                self._json(HTTPStatus.ACCEPTED, {"ok": True, "job": job.public()})
            except PermissionError as exc: self._error(HTTPStatus.FORBIDDEN, str(exc))
            except (CatalogError, ValueError, OSError) as exc: self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/desktop/activity":
            try:
                if not user_profile(username).get("permissions", {}).get("desktop_access", False): raise PermissionError("desktop access disabled")
                payload = _read_json_body(self); event = str(payload.get("event", "heartbeat"))
                if event not in {"connected", "input", "heartbeat", "disconnected", "keep"}: raise ValueError("invalid desktop activity")
                keep = bool(payload.get("enabled")) if event == "keep" else None
                result = _desktop_activity(username, event, keep)
                self._json(HTTPStatus.OK, {"ok": True, **result})
            except PermissionError as exc: self._error(HTTPStatus.FORBIDDEN, str(exc))
            except (ValueError, OSError) as exc: self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/desktop/terminate":
            try:
                _storage_helper("terminate_desktop", username, "desktop")
                with _desktop_ticket_lock:
                    for key in [key for key, value in _desktop_tickets.items() if value.get("username") == username]:
                        _desktop_tickets.pop(key, None)
                self._json(HTTPStatus.OK, {"ok": True, "username": username})
            except (ValueError, OSError, RuntimeError) as exc: self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/files/mkdir":
            try:
                payload = _read_json_body(self)
                scope = str(payload.get("scope", "personal"))
                relative = str(payload.get("path", ""))
                name = str(payload.get("name", ""))
                if not SAFE_NAME_RE.fullmatch(name) or name.startswith(".") or name in {".", ".."}:
                    raise ValueError("invalid name")
                _, parent = _safe_storage_path(username, scope, relative)
                _, target = _safe_storage_path(username, scope, posixpath.join(relative, name))
                if not parent.is_dir() or target.exists():
                    raise ValueError("invalid destination")
                if _is_virtual_personal_relative(scope, posixpath.join(relative, name)):
                    target.mkdir(mode=0o770, parents=True, exist_ok=False)
                else:
                    _storage_helper("prepare_storage_directory", username, scope, path=posixpath.join(relative, name))
            except StorageUnavailableError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
                return
            except (ValueError, OSError, RuntimeError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, f"folder could not be created: {exc}")
                return
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/api/files/rename":
            try:
                payload = _read_json_body(self)
                scope = str(payload.get("scope", "personal"))
                relative = str(payload.get("path", ""))
                new_name = str(payload.get("new_name", ""))
                if not SAFE_NAME_RE.fullmatch(new_name) or new_name.startswith("."):
                    raise ValueError("invalid name")
                _, source = _safe_storage_path(username, scope, relative)
                parent_rel = str(PurePosixPath(relative).parent) if relative else ""
                _, destination = _safe_storage_path(username, scope, posixpath.join(parent_rel, new_name))
                if not source.exists() or destination.exists() or source == destination:
                    raise ValueError("invalid destination")
                source.rename(destination)
                try:
                    if not _is_virtual_personal_relative(scope, posixpath.join(parent_rel, new_name)):
                        _storage_helper("normalize_storage", username, scope, paths=[str(destination.relative_to(_safe_storage_path(username, scope, "")[0]))], recursive=destination.is_dir())
                except RuntimeError:
                    destination.rename(source)
                    raise
            except StorageUnavailableError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
                return
            except (ValueError, OSError, RuntimeError):
                self._error(HTTPStatus.BAD_REQUEST, "item could not be renamed")
                return
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/api/files/upload/preflight":
            try:
                payload = _read_json_body(self)
                total = int(payload.get("total_bytes", -1))
                scope = str(payload.get("scope", "personal")); path = str(payload.get("path", ""))
                if total < 0 or total > MAX_STREAM_UPLOAD * 100000:
                    raise ValueError("invalid upload size")
                is_workspace = scope == "workspace" or (scope == "personal" and (path == "Workspace" or path.startswith("Workspace/")))
                if is_workspace:
                    quota = _workspace_quota_bytes(username)
                    free = _workspace_logical_free(username)
                else:
                    quota = _workspace_quota_bytes(username); free = max(quota - _directory_size(_personal_base(username)), 0)
                if quota and total > free:
                    self._error(HTTPStatus.INSUFFICIENT_STORAGE, "selection exceeds the available durable quota")
                    return
                self._json(HTTPStatus.OK, {"ok": True, "total_bytes": total, "available_bytes": free, "workspace_in_primary_quota": is_workspace})
            except (ValueError, OSError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/files/upload":
            temp_path: Path | None = None
            try:
                query = parse_qs(parsed.query, keep_blank_values=True)
                streaming = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() == "application/octet-stream"
                if streaming:
                    fields = {key: values[0] for key, values in query.items() if values}
                    upload_relative = fields.get("relative_path", "")
                    media_upload = fields.get("purpose") == "media"
                    parts = _check_relative_path(upload_relative, allow_hidden=not media_upload)
                    if not parts:
                        raise ValueError("filename is required")
                    filename = parts[-1]
                    file_data = b""
                    raw_length = self.headers.get("Content-Length")
                    try:
                        request_length = int(raw_length or "-1")
                    except ValueError as exc:
                        raise ValueError("invalid content length") from exc
                    if request_length < 0 or request_length > MAX_STREAM_UPLOAD:
                        raise ValueError("upload exceeds the 25 GiB per-file limit")
                else:
                    fields, filename, file_data = _parse_multipart(self)
                    upload_relative = fields.get("relative_path", filename)
                    media_upload = fields.get("purpose") == "media"
                    parts = _check_relative_path(upload_relative, allow_hidden=not media_upload)
                    request_length = len(file_data)
                if any(not SAFE_NAME_RE.fullmatch(part) or (part.startswith(".") and media_upload) for part in parts):
                    raise ValueError("invalid filename or folder name")
                scope = "personal" if media_upload else fields.get("scope", "personal")
                relative = "Fotos/LunarX Fotos" if media_upload else fields.get("path", "")
                if media_upload and len(parts) != 1: raise ValueError("media upload does not accept a folder path")
                destination_relative = posixpath.join(relative, *parts)
                allow_hidden = not media_upload
                base, destination = _safe_storage_path(username, scope, destination_relative, allow_hidden=allow_hidden)
                parent_relative = str(PurePosixPath(destination_relative).parent)
                if parent_relative == ".": parent_relative = ""
                virtual_alias = _virtual_personal_alias(scope, destination_relative, allow_hidden=allow_hidden)
                virtual_target = virtual_alias is not None
                storage_scope = virtual_alias.lower() if virtual_target else scope
                if virtual_target:
                    virtual_parent = destination.parent.relative_to(base).as_posix()
                    _storage_helper("prepare_storage_directory", username, storage_scope, path="" if virtual_parent == "." else virtual_parent, allow_hidden=allow_hidden)
                else:
                    _storage_helper("prepare_storage_directory", username, scope, path=parent_relative, allow_hidden=allow_hidden)
                _ensure_logical_quota(username, request_length)
                temporary_dir = destination.parent
                with tempfile.NamedTemporaryFile(mode="wb", prefix=".lunarx-upload-", dir=temporary_dir, delete=False) as temp:
                    temp_path = Path(temp.name)
                    if not streaming: temp.write(file_data)
                size = _stream_request_body(self, temp_path) if streaming else len(file_data)
                media_kind = _media_kind(temp_path) if media_upload else None
                if media_upload:
                    if not media_kind: raise ValueError("the file content is not a supported photo or video")
                    media_root = "Fotos/LunarX Fotos" if media_kind == "photo" else "Videos/LunarX Vídeos"
                    destination_relative = posixpath.join(media_root, filename)
                    base, destination = _safe_storage_path(username, scope, destination_relative)
                    _storage_helper("prepare_storage_directory", username, scope, path=media_root)
                conflict = fields.get("conflict", "cancel")
                if destination.exists() and conflict == "rename":
                    stem, suffix = destination.stem, destination.suffix
                    for index in range(1, 1000):
                        candidate = destination.with_name(f"{stem} ({index}){suffix}")
                        if not candidate.exists(): destination = candidate; filename = candidate.name; break
                if (destination.exists() and conflict != "overwrite") or not destination.parent.is_dir(): raise ValueError("invalid destination")
                os.chmod(temp_path, 0o600 if scope == "personal" else 0o660)
                temp_relative = temp_path.relative_to(base).as_posix()
                _storage_helper("normalize_storage", username, storage_scope, paths=[temp_relative], allow_upload_temp=True, allow_hidden=allow_hidden)
                os.replace(temp_path, destination)
                temp_path = None
            except StorageUnavailableError:
                try:
                    if temp_path is not None and temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
                return
            except (ValueError, OSError, RuntimeError) as exc:
                try:
                    if temp_path is not None and temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass
                detail = "quota exceeded" if isinstance(exc, OSError) and getattr(exc, "errno", None) == 122 else str(exc)
                self._error(HTTPStatus.BAD_REQUEST, f"file could not be uploaded: {detail}")
                return
            self._json(HTTPStatus.OK, {"ok": True, "name": destination.name, "path": destination.relative_to(base).as_posix(), "size": size})
            return
        if parsed.path == "/api/files/extract":
            try:
                payload = _read_json_body(self)
                scope = str(payload.get("scope", "personal"))
                relative = str(payload.get("path", ""))
                destination = str(payload.get("destination", ""))
                extracted = _extract_zip(username, scope, relative, destination)
            except StorageUnavailableError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
                return
            except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, f"ZIP could not be extracted: {exc}")
                return
            self._json(HTTPStatus.OK, {"ok": True, "path": extracted})
            return
        if parsed.path == "/api/files/compress":
            try:
                payload = _read_json_body(self)
                scope = str(payload.get("scope", "personal"))
                paths = payload.get("paths") if isinstance(payload.get("paths"), list) else []
                destination = str(payload.get("destination", ""))
                name = str(payload.get("name", "LunarX-archive.zip"))
                compressed = _compress_zip(username, scope, [str(path) for path in paths], destination, name)
            except StorageUnavailableError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
                return
            except (ValueError, OSError, RuntimeError, shutil.Error) as exc:
                self._error(HTTPStatus.BAD_REQUEST, f"ZIP could not be created: {exc}")
                return
            self._json(HTTPStatus.OK, {"ok": True, "path": compressed})
            return
        if parsed.path == "/api/files/delete":
            try:
                payload = _read_json_body(self)
                scope = str(payload.get("scope", "personal"))
                relative = str(payload.get("path", ""))
                _, target = _safe_storage_path(username, scope, relative)
                if not target.exists() or target == _personal_base(username).resolve() or target == (DATA_ROOT / "shared").resolve():
                    raise ValueError("invalid target")
                if target.is_dir():
                    if _tree_has_symlink(target): raise ValueError("symlink rejected")
                    shutil.rmtree(target)
                else:
                    target.unlink()
            except StorageUnavailableError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
                return
            except (ValueError, OSError):
                self._error(HTTPStatus.BAD_REQUEST, "item could not be deleted; folders must be empty")
                return
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/api/files/transfer":
            try:
                payload = _read_json_body(self)
                operation = str(payload.get("operation", ""))
                scope = str(payload.get("scope", "personal"))
                relative = str(payload.get("path", ""))
                paths = payload.get("paths") if isinstance(payload.get("paths"), list) else [relative]
                destination_relative = str(payload.get("destination", ""))
                if operation not in {"copy", "move"}:
                    raise ValueError("invalid operation")
                _, destination_dir = _safe_storage_path(username, scope, destination_relative)
                if not destination_dir.is_dir(): raise ValueError("invalid destination")
                conflict = str(payload.get("conflict", "cancel"))
                planned_bytes = 0
                for candidate_relative in paths[:200]:
                    _, candidate_source = _safe_storage_path(username, scope, str(candidate_relative))
                    if candidate_source.is_file(): planned_bytes += candidate_source.stat().st_size
                    elif candidate_source.is_dir(): planned_bytes += _directory_size(candidate_source)
                if operation == "copy": _ensure_logical_quota(username, planned_bytes)
                completed = []
                for relative in paths[:200]:
                    _, source = _safe_storage_path(username, scope, str(relative))
                    if not source.exists() or source in {_personal_base(username).resolve(), (DATA_ROOT / "shared").resolve()} or _tree_has_symlink(source): raise ValueError("invalid source")
                    destination = (destination_dir / source.name).resolve(strict=False); destination.relative_to(destination_dir.resolve(strict=False))
                    if source.is_dir() and destination_dir.resolve(strict=False).is_relative_to(source.resolve(strict=False)): raise ValueError("recursive destination")
                    if destination.exists():
                        if conflict == "overwrite":
                            if destination.is_dir(): shutil.rmtree(destination)
                            else: destination.unlink()
                        elif conflict == "rename":
                            stem, suffix = destination.stem, destination.suffix
                            for index in range(1, 1000):
                                candidate = destination.with_name(f"{stem} ({index}){suffix}")
                                if not candidate.exists(): destination = candidate; break
                        else: raise FileExistsError("destination already exists")
                    if operation == "move": source.rename(destination)
                    elif source.is_dir(): shutil.copytree(source, destination, symlinks=False)
                    else: shutil.copy2(source, destination)
                    try:
                        if not _is_virtual_personal_relative(scope, destination_relative):
                            _storage_helper("normalize_storage", username, scope, paths=[destination.relative_to(_safe_storage_path(username, scope, "")[0]).as_posix()], recursive=destination.is_dir())
                    except RuntimeError:
                        if operation == "move" and destination.exists() and not source.exists(): destination.rename(source)
                        elif operation == "copy" and destination.exists():
                            if destination.is_dir(): shutil.rmtree(destination)
                            else: destination.unlink()
                        raise
                    completed.append(destination.name)
            except StorageUnavailableError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
                return
            except (ValueError, OSError, RuntimeError, shutil.Error):
                self._error(HTTPStatus.BAD_REQUEST, "item could not be moved or copied")
                return
            self._json(HTTPStatus.OK, {"ok": True, "operation": operation, "completed": completed})
            return
        if parsed.path == "/api/media/action":
            try:
                _require_storage()
                payload = _read_json_body(self); action = str(payload.get("action", "")); paths = payload.get("paths", [])
                if not isinstance(paths, list) or len(paths) > 200: raise ValueError("invalid media selection")
                paths = [str(path) for path in paths]
                with _media_lock:
                    media_state = _media_state(username); albums = media_state.get("albums", [])
                    if action == "create_album":
                        name = str(payload.get("name", "")).strip()
                        if not SAFE_NAME_RE.fullmatch(name) or name.startswith("."): raise ValueError("invalid album name")
                        album = {"id": secrets.token_urlsafe(9), "name": name, "items": [], "created": int(time.time())}; albums.append(album)
                        _save_media_state(username, media_state); result = {"album": album}
                    elif action in {"add_to_album", "remove_from_album"}:
                        album_id = str(payload.get("album_id", "")); album = next((item for item in albums if isinstance(item, dict) and item.get("id") == album_id), None)
                        if not album: raise ValueError("album not found")
                        existing = [str(path) for path in album.get("items", []) if isinstance(path, str)]
                        for relative in paths:
                            target = _safe_media_path(username, relative)
                            if not target.is_file() or not _media_kind(target): raise ValueError("media unavailable")
                        if action == "add_to_album": album["items"] = list(dict.fromkeys(existing + paths))
                        else: album["items"] = [path for path in existing if path not in set(paths)]
                        _save_media_state(username, media_state); result = {"album": album}
                    elif action == "set_album_cover":
                        album_id = str(payload.get("album_id", "")); cover = str(payload.get("cover", ""))
                        album = next((item for item in albums if isinstance(item, dict) and item.get("id") == album_id), None)
                        if not album or cover not in {str(path) for path in album.get("items", [])}: raise ValueError("album cover is not part of this album")
                        target = _safe_media_path(username, cover)
                        if not target.is_file() or not _media_kind(target): raise ValueError("media unavailable")
                        album["cover"] = cover; _save_media_state(username, media_state); result = {"album": album}
                    elif action == "delete_album":
                        album_id = str(payload.get("album_id", "")); before = len(albums); selected_album = next((item for item in albums if isinstance(item, dict) and item.get("id") == album_id), None)
                        media_state["albums"] = [item for item in albums if not isinstance(item, dict) or item.get("id") != album_id]
                        if len(media_state["albums"]) == before: raise ValueError("album not found")
                        deleted_media = []
                        if bool(payload.get("delete_media")) and selected_album:
                            for relative in [str(path) for path in selected_album.get("items", [])]:
                                target = _safe_media_path(username, relative)
                                if target.is_file() and not target.is_symlink(): target.unlink(); deleted_media.append(relative)
                            removed = set(deleted_media)
                            for other in media_state["albums"]:
                                if isinstance(other, dict) and isinstance(other.get("items"), list): other["items"] = [path for path in other["items"] if str(path) not in removed]
                        _save_media_state(username, media_state); result = {"deleted_album": album_id, "deleted_media": deleted_media}
                    elif action == "delete":
                        if not paths: raise ValueError("select at least one item")
                        for relative in paths:
                            target = _safe_media_path(username, relative)
                            if not target.is_file() or target.is_symlink(): raise ValueError("media unavailable")
                        for relative in paths: _safe_media_path(username, relative).unlink()
                        selected = set(paths)
                        for album in albums:
                            if isinstance(album, dict) and isinstance(album.get("items"), list): album["items"] = [path for path in album["items"] if path not in selected]
                        _save_media_state(username, media_state); result = {"deleted": paths}
                    elif action == "move":
                        folder = str(payload.get("folder", "")).strip()
                        if not SAFE_NAME_RE.fullmatch(folder) or folder.startswith(".") or not paths: raise ValueError("invalid destination folder")
                        moved: dict[str, str] = {}
                        for relative in paths:
                            source = _safe_media_path(username, relative); kind = _media_kind(source)
                            if not source.is_file() or not kind: raise ValueError("media unavailable")
                            root = "Fotos/LunarX Fotos" if kind == "photo" else "Videos/LunarX Vídeos"
                            parent = posixpath.join(root, folder); _storage_helper("prepare_storage_directory", username, "personal", path=parent)
                            _, destination = _safe_storage_path(username, "personal", posixpath.join(parent, source.name))
                            if destination.exists():
                                for index in range(1, 1000):
                                    candidate = destination.with_name(f"{destination.stem} ({index}){destination.suffix}")
                                    if not candidate.exists(): destination = candidate; break
                            source.rename(destination); new_relative = destination.relative_to(_personal_base(username).resolve()).as_posix()
                            _storage_helper("normalize_storage", username, "personal", paths=[new_relative]); moved[relative] = new_relative
                        for album in albums:
                            if isinstance(album, dict) and isinstance(album.get("items"), list): album["items"] = [moved.get(str(path), str(path)) for path in album["items"]]
                        _save_media_state(username, media_state); result = {"moved": moved}
                    else: raise ValueError("unsupported media action")
                self._json(HTTPStatus.OK, {"ok": True, **result})
            except StorageUnavailableError: self._error(HTTPStatus.SERVICE_UNAVAILABLE, "storage unavailable")
            except (ValueError, OSError, RuntimeError) as exc: self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/profile":
            try:
                payload = _read_json_body(self, MAX_PROFILE_JSON); request = {"action": "update_user", "username": username}
                for key in ("new_username", "password", "display_name", "avatar", "theme"):
                    if key in payload: request[key] = _normalized_avatar(payload[key]) if key == "avatar" else payload[key]
            except (ValueError, UnicodeError): self._error(HTTPStatus.BAD_REQUEST, "invalid profile request"); return
            ok, result = _helper(request)
            if ok and result.get("username") and result["username"] != username:
                new_name = str(result["username"]); token_value = _session_token(self)
                if token_value:
                    with _session_lock:
                        if token_value in _sessions: _sessions[token_value]["username"] = new_name
            self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result, "user": user_profile(str(result.get("username", username))) if ok else None}); return
        if parsed.path == "/api/admin/users":
            if not self._auth(admin=True):
                return
            try:
                payload = _read_json_body(self, MAX_PROFILE_JSON)
                action = str(payload.get("action", ""))
                target = str(payload.get("username", ""))
                if action not in {"create_user", "update_user", "delete_user"}:
                    raise ValueError("unsupported action")
                request = {"action": action, "username": target}
                for key in ("password", "display_name", "new_username", "avatar", "theme", "quota_gib", "enabled", "permissions"):
                    if key in payload:
                        request[key] = _normalized_avatar(payload[key]) if key == "avatar" else payload[key]
                if action == "create_user" and not request.get("password"):
                    raise ValueError("password required")
            except (ValueError, UnicodeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid user request")
                return
            ok, result = _helper(request)
            self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result})
            return
        if parsed.path == "/api/admin/settings":
            if not self._auth(admin=True): return
            try: payload = _read_json_body(self)
            except ValueError: self._error(HTTPStatus.BAD_REQUEST, "invalid settings request"); return
            ok, result = _helper({"action": "settings_update", **payload}); self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result}); return
        if parsed.path == "/api/admin/services":
            if not self._auth(admin=True): return
            try: payload = _read_json_body(self); unit = str(payload.get("unit", ""))
            except ValueError: self._error(HTTPStatus.BAD_REQUEST, "invalid service request"); return
            ok, result = _helper({"action": "restart_service", "unit": unit}); self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result}); return
        if parsed.path == "/api/admin/apps":
            if not self._auth(admin=True): return
            try: payload = _read_json_body(self)
            except ValueError: self._error(HTTPStatus.BAD_REQUEST, "invalid application request"); return
            ok, result = _helper({"action": "server_app", "app_id": str(payload.get("app_id", "")), "operation": str(payload.get("operation", ""))}); self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result}); return
        if parsed.path == "/api/admin/storage/loopback-test":
            if not self._auth(admin=True): return
            ok, result = _helper({"action": "storage_loopback_test"}); self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result}); return
        if parsed.path == "/api/admin/storage/action":
            if not self._auth(admin=True): return
            try:
                payload = _read_json_body(self); action = str(payload.get("action", ""))
                if action not in {"storage_register", "storage_grant"}: raise ValueError("unsupported storage action")
            except ValueError as exc: self._error(HTTPStatus.BAD_REQUEST, str(exc)); return
            ok, result = _helper(payload); self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result}); return
        if parsed.path == "/api/admin/terminal/ticket":
            if not self._auth(admin=True): return
            ticket = secrets.token_urlsafe(32); token_value = _session_token(self)
            with _terminal_lock:
                now = time.time()
                for key in [key for key, value in _terminal_tickets.items() if value.get("expires", 0) < now]: _terminal_tickets.pop(key, None)
                _terminal_tickets[ticket] = {"session": token_value, "expires": now + 30}
            self._json(HTTPStatus.OK, {"ok": True, "ticket": ticket}); return
        self._error(HTTPStatus.NOT_FOUND, "not found")


def main() -> int:
    runtime_config = app_config()
    host = os.environ.get("LUNARX_HOME_BIND", str(runtime_config.get("bind", "127.0.0.1")))
    try:
        port = int(os.environ.get("LUNARX_HOME_PORT", str(runtime_config.get("port", 8787))))
    except ValueError:
        port = 8787
    if port < 1 or port > 65535:
        return 2
    try:
        PATHS.ensure_dirs()
        MEDIA_STATE_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
        MEDIA_CACHE_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
        created_setup_token = _ensure_setup_token()
        if created_setup_token:
            print(f"LunarX first-run setup token: {created_setup_token}", flush=True)
            print(f"Setup token file: {PATHS.setup_token_file}", flush=True)
    except OSError:
        print("LunarX state root unavailable", file=sys.stderr)
        return 4
    if not _storage_available():
        print("LunarX data root unavailable; starting in degraded storage mode", file=sys.stderr)
    server = ThreadingHTTPServer((host, port), HomeHandler)
    server.daemon_threads = True
    print(f"LunarX Home listening on {host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

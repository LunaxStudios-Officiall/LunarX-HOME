"""Canonical LunarX paths for native Linux, Android/PRoot and portable runs.

All product components import this module so that the web process, broker and
installer cannot silently disagree about where data lives.  Explicit
``LUNARX_*_ROOT`` values always win; otherwise Android/PRoot uses the user's
persistent application directory and native Linux keeps the established FHS
locations for compatibility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
import os


def _flag(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _portable_environment(env: Mapping[str, str]) -> bool:
    marker = " ".join(
        str(env.get(key, ""))
        for key in ("LUNARX_PLATFORM", "TERMUX_VERSION", "PREFIX", "PROOT_VERSION", "PROOT_TMP_DIR")
    ).lower()
    return _flag(env.get("LUNARX_PORTABLE")) or "android" in marker or "termux" in marker or "proot" in marker


@dataclass(frozen=True)
class PathConfig:
    app_root: Path
    config_root: Path
    data_root: Path
    state_root: Path
    runtime_root: Path
    backup_root: Path
    catalog_root: Path
    desktop_root: Path
    portable: bool

    @property
    def config_file(self) -> Path:
        return self.config_root / "config.json"

    @property
    def state_file(self) -> Path:
        return self.config_root / "state.json"

    @property
    def quotas_file(self) -> Path:
        return self.config_root / "quotas.json"

    @property
    def setup_token_file(self) -> Path:
        return self.config_root / "setup-token"

    @property
    def install_state_file(self) -> Path:
        return self.state_root / "install-state.json"

    @property
    def jobs_file(self) -> Path:
        return self.state_root / "app-jobs.json"

    @property
    def desktop_activity_file(self) -> Path:
        return self.runtime_root / "desktop-activity.json"

    @property
    def resource_status_file(self) -> Path:
        return self.runtime_root / "resource-status.json"

    @property
    def media_state_root(self) -> Path:
        return self.state_root / "media"

    @property
    def media_cache_root(self) -> Path:
        return self.state_root / "media-cache"

    def user_home(self, username: str) -> Path:
        if self.portable:
            return self.data_root / "users" / username
        return Path("/home") / username

    def desktop_path(self, username: str) -> Path:
        if self.portable:
            return self.user_home(username) / "Desktop"
        return Path("/home") / username / "Desktop"

    def as_dict(self) -> dict[str, object]:
        return {key: str(value) if isinstance(value, Path) else value for key, value in asdict(self).items()}

    def ensure_dirs(self) -> None:
        for path in (self.config_root, self.data_root, self.state_root, self.runtime_root, self.backup_root):
            path.mkdir(mode=0o750, parents=True, exist_ok=True)
        for path in (self.data_root / "shared", self.data_root / "users", self.media_state_root, self.media_cache_root):
            path.mkdir(mode=0o770 if path.name in {"shared", "users"} else 0o750, parents=True, exist_ok=True)


def resolve_paths(app_root: Path | None = None, env: Mapping[str, str] | None = None) -> PathConfig:
    values = os.environ if env is None else env
    root = Path(app_root or values.get("LUNARX_APP_ROOT") or Path(__file__).resolve().parents[1]).resolve()
    portable = _portable_environment(values)
    persistent = values.get("LUNARX_PERSISTENT_ROOT") or values.get("LUNARX_ANDROID_ROOT")
    if portable and not persistent:
        user_root = Path(values.get("HOME") or ".").expanduser()
        persistent = str(user_root / ".local" / "share" / "lunarx-home")
    if persistent:
        base = Path(persistent).expanduser().resolve()
        defaults = {
            "config": base / "config",
            "data": base / "data",
            "state": base / "state",
            "runtime": base / "runtime",
            "backup": base / "backups",
        }
    else:
        defaults = {
            "config": Path("/etc/lunarx-home"),
            "data": Path("/srv/lunarx-data"),
            "state": Path("/var/lib/lunarx-home"),
            "runtime": Path("/run/lunarx-home"),
            "backup": Path("/var/backups/lunarx-home"),
        }
    def selected(key: str, env_key: str) -> Path:
        return Path(values.get(env_key) or defaults[key]).expanduser().resolve()
    catalog = Path(values.get("LUNARX_CATALOG_ROOT") or root / "catalog").expanduser().resolve()
    desktop_root = Path(values.get("LUNARX_DESKTOP_ROOT") or (defaults["data"] / "users")).expanduser().resolve()
    return PathConfig(
        app_root=root,
        config_root=selected("config", "LUNARX_CONFIG_ROOT"),
        data_root=selected("data", "LUNARX_DATA_ROOT"),
        state_root=selected("state", "LUNARX_STATE_ROOT"),
        runtime_root=selected("runtime", "LUNARX_RUNTIME_ROOT"),
        backup_root=selected("backup", "LUNARX_BACKUP_ROOT"),
        catalog_root=catalog,
        desktop_root=desktop_root,
        portable=portable,
    )


__all__ = ["PathConfig", "resolve_paths"]

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from installer.lunarx_installer import Installer, copy_backup
from lunarx_core.broker import handle
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import PlatformInfo

ROOT = Path(__file__).resolve().parents[1]


def installer_args(source: Path, target: Path) -> argparse.Namespace:
    return argparse.Namespace(command="update", source=source, target=target, dry_run=False, allow_unsupported_host=True, admin_user=None, skip_packages=True, enable_services=False, no_start=False, no_desktop=False)


class FieldFix313Tests(unittest.TestCase):
    def test_version_contract(self) -> None:
        self.assertEqual((ROOT / "VERSION").read_text().strip(), "3.1.3")
        self.assertIn('APP_VERSION = "3.1.3"', (ROOT / "app.py").read_text())
        self.assertIn('VERSION = "3.1.3"', (ROOT / "installer/lunarx_installer.py").read_text())

    def test_browser_login_controls_are_successful_form_controls(self) -> None:
        html = (ROOT / "static/index.html").read_text()
        self.assertIn('id="username" name="username"', html)
        self.assertIn('id="password" name="password"', html)
        script = (ROOT / "static/app.js").read_text()
        self.assertIn('path !== "/api/login"', script)
        self.assertIn('Usuário ou senha incorretos.', script)

    def test_backup_ignores_git_and_uses_buffered_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / "source"; destination = base / "backup"
            (source / ".git" / "objects" / "pack").mkdir(parents=True)
            (source / ".git" / "objects" / "pack" / "big.pack").write_bytes(b"x" * 4096)
            (source / "keep.txt").write_text("keep", encoding="utf-8")
            copy_backup(source, destination)
            self.assertEqual((destination / "keep.txt").read_text(), "keep")
            self.assertFalse((destination / ".git").exists())

    def test_installer_migrates_legacy_user_quota_and_shared_quota(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            env = {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":str(base / "persistent"), "HOME":str(base)}
            with mock.patch.dict(os.environ, env, clear=False):
                item = Installer(installer_args(ROOT, base / "app")); item.ensure_directories()
                item.config_root.mkdir(parents=True, exist_ok=True)
                (item.config_root / "state.json").write_text(json.dumps({
                    "schema_version":"3.1.2", "settings":{"default_quota_gib":64,"shared_contribution_gib":15},
                    "users":{"admin":{"display_name":"Admin","enabled":True,"is_admin":True,"role":"admin","auth_provider":"local-scrypt","password_hash":"kept"}},
                    "server_apps":{},"installed_apps":{},"storage":{"provider":"logical","members":[]}
                }), encoding="utf-8")
                item.ensure_config(None)
                state = json.loads((item.config_root / "state.json").read_text())
                quotas = json.loads((item.config_root / "quotas.json").read_text())["quotas"]
                self.assertEqual(state["users"]["admin"]["quota_gib"], 64)
                self.assertIn({"username":"admin","hard":"64G"}, quotas)
                self.assertIn({"username":"shared","hard":"15G"}, quotas)
                self.assertEqual(json.loads((item.config_root / "config.json").read_text())["bind"], "0.0.0.0")

    def test_portable_service_status_is_capability_aware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env={"LUNARX_PLATFORM":"android-proot","LUNARX_PERSISTENT_ROOT":directory,"HOME":directory}
            paths=resolve_paths(ROOT, env)
            info=PlatformInfo(mode="android-proot",distribution="ubuntu",distribution_version="24.04",architecture="aarch64",kernel="test",is_termux=False,is_proot=True,service_manager="lunarxctl",storage_mode="android-persistent-directory",filesystem="f2fs",mount_options=(),quota_mode="logical",app_backend="registry-only",desktop_backend="tigervnc-novnc",desktop_available=True,tailscale_installed=False,capabilities=("desktop-session",))
            ok, result = handle({"action":"status","units":["lunarx-home.service"]}, paths=paths, info=info)
            self.assertTrue(ok)
            states={item["unit"]:item["state"] for item in result["units"]}
            self.assertEqual(states["lunarx-home"], "active")
            self.assertEqual(states["desktop:tigervnc-novnc"], "active")
            self.assertEqual(states["tailscale"], "host-owned")

    def test_desktop_dependencies_and_command_aliases(self) -> None:
        installer = (ROOT / "installer/lunarx_installer.py").read_text()
        broker = (ROOT / "lunarx_core/broker.py").read_text()
        self.assertIn('"tigervnc-tools"', installer)
        self.assertIn('shutil.which("tigervncpasswd") or shutil.which("vncpasswd")', broker)
        platform = (ROOT / "lunarx_core/platform.py").read_text()
        self.assertIn('vnc_password = bool(shutil.which("tigervncpasswd") or shutil.which("vncpasswd"))', platform)
        self.assertIn('dbus-launch --exit-with-session startxfce4', broker)

    def test_termux_update_entrypoint_is_single_command_ready(self) -> None:
        script = (ROOT / "termux-update.sh").read_text()
        self.assertIn('BRANCH="3.1.3"', script)
        self.assertIn('proot-distro login "$DISTRO"', script)
        self.assertIn('lunarx-home-supervisor', script)
        self.assertIn('kill_tree', script)
        self.assertIn('TigerVNC password tool is missing', script)
        self.assertIn('healthz', script)
        self.assertIn('"version":"3.1.3"', script)


if __name__ == "__main__":
    unittest.main()

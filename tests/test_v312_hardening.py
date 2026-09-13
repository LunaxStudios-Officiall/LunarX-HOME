from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from installer.lunarx_installer import Installer
from lunarx_core.broker import handle
from lunarx_core.catalog import compatibility, find_app, load_catalog
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import detect_platform

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "apps.json"


def installer_args(source: Path, target: Path) -> argparse.Namespace:
    return argparse.Namespace(command="install", source=source, target=target, dry_run=False, allow_unsupported_host=True, admin_user=None, skip_packages=True, enable_services=False, no_start=False, no_desktop=False)


class NavigationHardeningTests(unittest.TestCase):
    def test_navigation_uses_interpolated_destination_guard(self) -> None:
        script = (ROOT / "static/app.js").read_text(encoding="utf-8")
        self.assertIn('const destination = $(`#view-${view}`);', script)
        self.assertIn('if (!destination) return false;', script)
        self.assertNotIn('$("#view-${view}")', script)
        self.assertIn('return true;', script)

    def test_drawer_preserves_focus_and_active_page_semantics(self) -> None:
        script = (ROOT / "static/app.js").read_text(encoding="utf-8")
        self.assertIn('aria-current', script)
        self.assertIn('drawerReturnFocus', script)
        self.assertIn('restoreFocus:true', script)
        self.assertIn('window.innerWidth <= 860', script)



    def test_login_form_node_is_captured_before_async_boundary(self) -> None:
        script = (ROOT / "static/app.js").read_text(encoding="utf-8")
        self.assertIn("const formNode = event.currentTarget", script)
        self.assertIn("formNode.reset(); await enter", script)
        self.assertNotIn("event.currentTarget.reset()", script)

class CatalogHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_catalog(str(CATALOG))
        cls.apps = {app["id"]: app for app in cls.document["apps"]}

    def test_exact_android_proot_coverage_is_partitioned(self) -> None:
        actionable, source_only, unsupported = set(), set(), set()
        for app in self.document["apps"]:
            state = compatibility(
                app,
                platform_name="android-proot",
                architecture="aarch64",
                backend="registry-only",
                package_probe=lambda _package: True,
            )
            if state["compatible"]:
                actionable.add(app["id"])
                continue
            if state["status"] == "Source only":
                source_only.add(app["id"])
            else:
                unsupported.add(app["id"])
        self.assertEqual(len(self.document["apps"]), 154)
        self.assertEqual(len(actionable), 56)
        self.assertEqual(len(source_only), 1)
        self.assertEqual(len(unsupported), 97)
        self.assertFalse(actionable & source_only)
        self.assertFalse(actionable & unsupported)
        self.assertFalse(source_only & unsupported)

    def test_new_official_arm64_providers_resolve_without_fake_apt_probe(self) -> None:
        expected = {
            "org.mozilla.firefox": ("vendor-apt", "mozilla.apt-arm64"),
            "com.brave.Browser": ("vendor-apt", "brave.apt-arm64"),
            "io.dbeaver.DBeaverCommunity": ("vendor-apt", "dbeaver.apt-arm64"),
            "com.rustdesk.RustDesk": ("pinned-deb", "rustdesk.deb-arm64-1.4.9"),
            "org.localsend.localsend_app": ("web-pwa", "official.web"),
            "com.slack.Slack": ("web-pwa", "official.web"),
        }
        for app_id, (kind, provider_id) in expected.items():
            state = compatibility(self.apps[app_id], platform_name="android-proot", architecture="aarch64", backend="registry-only", package_probe=lambda _p: False)
            self.assertTrue(state["compatible"], app_id)
            self.assertEqual(state["selected_provider"]["provider"], kind)
            self.assertEqual(state["selected_provider"]["id"], provider_id)

    def test_unsupported_entries_have_specific_reasons(self) -> None:
        unsupported = []
        for app in self.document["apps"]:
            for provider in app["providers"]:
                if provider["provider"] == "unsupported" and "android-proot" in provider["platforms"]:
                    unsupported.append((app, provider))
        self.assertEqual(len(unsupported), 97)
        for app, provider in unsupported:
            self.assertIn(app["name"], provider["reason"])
            self.assertTrue(provider["source"].startswith("http"))
            self.assertNotIn("No audited PRoot-compatible provider is defined", provider["reason"])

    def test_unknown_privileged_vendor_provider_is_rejected(self) -> None:
        raw = json.loads(CATALOG.read_text(encoding="utf-8"))
        app = next(item for item in raw["apps"] if item["id"] == "org.mozilla.firefox")
        vendor = next(item for item in app["providers"] if item["provider"] == "vendor-apt")
        vendor["id"] = "evil.vendor-repository"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            raw["apps"] = [app]
            path.write_text(json.dumps({**raw, "apps":[app]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_catalog.cache_clear(); load_catalog(str(path))
        load_catalog.cache_clear(); load_catalog(str(CATALOG))

    def test_vendor_system_provider_still_requires_admin_in_broker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":directory, "HOME":directory}
            paths = resolve_paths(ROOT, env); info = detect_platform(env=env, machine="aarch64", os_release={"ID":"ubuntu"}, paths=paths)
            handle({"action":"create_user","username":"alice","password":"safe password","is_admin":False}, paths=paths, info=info)
            ok, result = handle({"action":"user_app","username":"alice","app_id":"org.mozilla.firefox","operation":"install","provider_id":"mozilla.apt-arm64"}, paths=paths, info=info)
            self.assertFalse(ok)
            self.assertEqual(result["error_code"], "admin_required")


class InstallerOutputTests(unittest.TestCase):
    def test_fresh_first_run_token_is_reused_until_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with mock.patch.dict("os.environ", {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":str(base / "persistent"), "HOME":str(base)}, clear=False):
                item = Installer(installer_args(ROOT, base / "app")); item.ensure_directories(); item.ensure_config(None)
                first = item.create_setup_token(None); second = item.create_setup_token(None)
                self.assertTrue(first); self.assertEqual(first, second)

    def test_existing_admin_does_not_emit_setup_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with mock.patch.dict("os.environ", {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":str(base / "persistent"), "HOME":str(base)}, clear=False):
                item = Installer(installer_args(ROOT, base / "app")); item.ensure_directories(); item.ensure_config(None)
                state = json.loads((item.config_root / "state.json").read_text())
                state["users"]["admin"] = {"is_admin": True}
                (item.config_root / "state.json").write_text(json.dumps(state))
                self.assertIsNone(item.create_setup_token(None))

    def test_ready_summary_reports_truthful_loopback_listener(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with mock.patch.dict("os.environ", {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":str(base / "persistent"), "HOME":str(base)}, clear=False):
                item = Installer(installer_args(ROOT, base / "app")); item.ensure_directories(); item.ensure_config(None)
                messages = []
                item.emit = messages.append  # type: ignore[method-assign]
                item.emit_access_summary("one-time-token")
                text = "\n".join(messages)
                self.assertIn("LunarX Home is ready", text)
                self.assertIn("Listening: 127.0.0.1:8787", text)
                self.assertIn("Local URL: http://127.0.0.1:8787", text)
                self.assertIn("LAN URL: unavailable", text)
                self.assertIn("Android/PRoot note", text)
                self.assertIn("FIRST_RUN_SETUP_TOKEN=one-time-token", text)

    def test_runtime_reads_configured_bind_and_port(self) -> None:
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        control = (ROOT / "lunarxctl").read_text(encoding="utf-8")
        self.assertIn('runtime_config = app_config()', source)
        self.assertIn('runtime_config.get("port", 8787)', source)
        self.assertIn('def _configured_port()', control)


    def test_proot_uninstall_stops_runtime_and_preserves_persistent_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"; home.mkdir()
            persistent = base / "persistent"
            target = base / "app"; target.mkdir()
            control_script = target / "lunarxctl"
            stop_marker = base / "stopped.txt"
            control_script.write_text(
                "import pathlib, sys\n"
                f"pathlib.Path({str(stop_marker)!r}).write_text(sys.argv[1] if len(sys.argv)>1 else 'none')\n",
                encoding="utf-8",
            )
            (target / "VERSION").write_text("3.1.2\n", encoding="utf-8")
            config_root = persistent / "config"; config_root.mkdir(parents=True)
            (config_root / "config.json").write_text("{}", encoding="utf-8")
            data_root = persistent / "data"; data_root.mkdir(parents=True)
            (data_root / "keep.txt").write_text("keep", encoding="utf-8")
            with mock.patch.dict("os.environ", {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":str(persistent), "HOME":str(home)}, clear=False):
                args = installer_args(ROOT, target); args.command = "uninstall"
                item = Installer(args)
                item.write_control_command()
                control = home / ".local" / "bin" / "lunarxctl"
                self.assertTrue(control.is_symlink())
                item.uninstall()
                self.assertFalse(target.exists())
                self.assertFalse(control.exists())
                self.assertEqual(stop_marker.read_text(), "stop")
                self.assertEqual((data_root / "keep.txt").read_text(), "keep")
                self.assertTrue(config_root.is_dir())
    def test_failed_update_restores_previous_application_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"; home.mkdir()
            target = base / "app"
            shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
            (target / "VERSION").write_text("3.1.1\n", encoding="utf-8")
            (target / "previous-marker.txt").write_text("keep previous code\n", encoding="utf-8")
            persistent = base / "persistent"
            config_root = persistent / "config"; config_root.mkdir(parents=True)
            original_state = {"schema_version":"3.1.1","users":{"owner":{"is_admin":True,"theme":"light","profile_note":"preserve-me"}},"settings":{"theme_mode":"light"}}
            (config_root / "state.json").write_text(json.dumps(original_state), encoding="utf-8")
            (config_root / "config.json").write_text(json.dumps({"version":"3.1.1","bind":"127.0.0.1","port":8787}), encoding="utf-8")
            data_root = persistent / "data"; data_root.mkdir(parents=True)
            (data_root / "user-file.txt").write_text("persistent data\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":str(persistent), "HOME":str(home)}, clear=False):
                args = installer_args(ROOT, target); args.command = "update"
                item = Installer(args)
                item.start_portable_runtime = lambda: None  # type: ignore[method-assign]
                def health(expected_version: str = "3.1.2") -> None:
                    if expected_version == "3.1.2":
                        raise RuntimeError("injected failed health check")
                item.verify_health = health  # type: ignore[method-assign]
                with self.assertRaisesRegex(Exception, "previous version restored automatically"):
                    item.install_or_update()
                self.assertEqual((target / "VERSION").read_text().strip(), "3.1.1")
                self.assertTrue((target / "previous-marker.txt").is_file())
                restored = json.loads((config_root / "state.json").read_text())
                self.assertEqual(restored, original_state)
                self.assertEqual((data_root / "user-file.txt").read_text(), "persistent data\n")
                self.assertIsNotNone(item.backup)
                self.assertTrue((item.backup / "application").is_dir())


class SecurityFailureTests(unittest.TestCase):
    def test_invalid_provider_identifier_does_not_reach_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":directory, "HOME":directory}
            paths = resolve_paths(ROOT, env); info = detect_platform(env=env, machine="aarch64", os_release={"ID":"ubuntu"}, paths=paths)
            handle({"action":"create_user","username":"admin","password":"safe password","is_admin":True}, paths=paths, info=info)
            ok, result = handle({"action":"user_app","username":"admin","app_id":"org.mozilla.firefox","operation":"install","provider_id":"../../repo"}, paths=paths, info=info)
            self.assertFalse(ok); self.assertEqual(result["error_code"], "invalid_provider")


if __name__ == "__main__":
    unittest.main()

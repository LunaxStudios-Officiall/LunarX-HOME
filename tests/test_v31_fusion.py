from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import http.cookiejar
from unittest import mock

from lunarx_core.appstore import annotate_catalog, installed_for_user, public_catalog
from lunarx_core.broker import handle
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import detect_platform
from lunarx_core.remote import tailscale_status
from lunarx_core.storage import primary_storage_ready
from installer.lunarx_installer import copy_source

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class FusionContracts(unittest.TestCase):
    def test_version_is_consistent(self):
        self.assertEqual((ROOT / "VERSION").read_text().strip(), "3.1.2")
        self.assertIn('APP_VERSION = "3.1.2"', (ROOT / "app.py").read_text())
        self.assertIn('VERSION = "3.1.2"', (ROOT / "installer/lunarx_installer.py").read_text())
        self.assertIn('print("3.1.2")', (ROOT / "lunarxctl").read_text())

    def test_android_paths_share_one_persistent_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LUNARX_PLATFORM": "android-proot", "LUNARX_PERSISTENT_ROOT": tmp, "HOME": tmp}
            paths = resolve_paths(ROOT, env)
            self.assertTrue(paths.portable)
            self.assertEqual(paths.data_root, Path(tmp).resolve() / "data")
            self.assertEqual(paths.config_root, Path(tmp).resolve() / "config")

    def test_proot_capabilities_do_not_claim_systemd_or_flatpak(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LUNARX_PLATFORM": "android-proot", "LUNARX_PERSISTENT_ROOT": tmp, "HOME": tmp}
            paths = resolve_paths(ROOT, env)
            info = detect_platform(env=env, os_release={"ID": "ubuntu", "VERSION_ID": "24.04"}, paths=paths)
            self.assertTrue(info.is_proot)
            self.assertFalse(info.systemd)
            self.assertFalse(info.flatpak)
            self.assertEqual(info.app_backend, "registry-only")

    def test_storage_directory_is_valid_without_mountpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            root.mkdir()
            self.assertTrue(primary_storage_ready(root))

    def test_first_user_broker_creates_hashed_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LUNARX_PLATFORM": "android-proot", "LUNARX_PERSISTENT_ROOT": tmp, "HOME": tmp}
            paths = resolve_paths(ROOT, env); paths.ensure_dirs()
            info = detect_platform(env=env, os_release={"ID": "ubuntu"}, paths=paths)
            ok, result = handle({"action":"create_user","username":"joao","password":"StrongPass123!","is_admin":True}, paths=paths, info=info)
            self.assertTrue(ok, result)
            state = json.loads(paths.state_file.read_text())
            self.assertTrue(state["users"]["joao"]["is_admin"])
            self.assertNotIn("StrongPass123!", paths.state_file.read_text())

    def test_catalog_compatibility_blocks_wrong_provider(self):
        raw = public_catalog(ROOT / "catalog/apps.json")
        result = annotate_catalog(raw, platform_name="android-proot", architecture="aarch64", backend="registry-only")
        unsupported = next(app for app in result["apps"] if app["id"] == "md.obsidian.Obsidian")
        self.assertFalse(unsupported["compatible"])
        self.assertFalse(unsupported["installable_here"])
        self.assertTrue(unsupported["unavailable_reason"])

    def test_provider_failure_is_not_fake_empty_success(self):
        result = installed_for_user(lambda _payload: (False, {"error_code":"APP_PROVIDER_UNAVAILABLE","error":"Flatpak unavailable"}), "joao")
        self.assertFalse(result["available"])
        self.assertEqual(result["apps"], [])
        self.assertEqual(result["error_code"], "APP_PROVIDER_UNAVAILABLE")

    def test_desktop_bridge_is_localhost_only_and_authenticated(self):
        admin = (ROOT / "lunarx-admin.py").read_text()
        app = (ROOT / "app.py").read_text()
        self.assertIn('"-localhost","yes"', admin)
        self.assertIn('/api/desktop/ws', app)
        self.assertIn('authenticated-websocket', app)

    def test_tailscale_proot_is_android_host_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LUNARX_PLATFORM": "android-proot", "LUNARX_PERSISTENT_ROOT": tmp, "HOME": tmp}
            paths = resolve_paths(ROOT, env)
            info = detect_platform(env=env, os_release={"ID": "ubuntu"}, paths=paths)
            status = tailscale_status(info, 8787)
            self.assertEqual(status["provider"], "android-host-app")
            self.assertFalse(status["controllable"])

    def test_staged_update_preserves_external_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); src = base / "src"; dst = base / "app"; data = base / "data"
            src.mkdir(); dst.mkdir(); data.mkdir()
            (src / "app.py").write_text("new\n"); (dst / "app.py").write_text("old\n"); (data / "keep").write_text("yes")
            copy_source(src, dst)
            self.assertEqual((dst / "app.py").read_text(), "new\n")
            self.assertEqual((data / "keep").read_text(), "yes")

    def test_design_has_real_mobile_breakpoints_and_motion_reduction(self):
        css = (ROOT / "static/styles.css").read_text()
        self.assertIn("@media(max-width:620px)", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("dashboard-grid", css)
        self.assertIn("store-grid", css)

    def test_installer_contains_proot_desktop_dependencies(self):
        source = (ROOT / "installer/lunarx_installer.py").read_text()
        for package in ("tigervnc-standalone-server", "novnc", "xfce4", "dbus-x11"):
            self.assertIn(package, source)
        self.assertIn("verify_health", source)


class RuntimeIntegration(unittest.TestCase):
    def test_fresh_proot_bootstrap_login_and_bootstrap_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); port = free_port()
            env = os.environ.copy()
            env.update({
                "PYTHONPATH": str(ROOT), "HOME": str(base), "LUNARX_PLATFORM": "android-proot",
                "LUNARX_PERSISTENT_ROOT": str(base / "persistent"), "LUNARX_HOME_BIND": "127.0.0.1",
                "LUNARX_HOME_PORT": str(port), "LUNARX_BROKER_MODE": "direct",
            })
            proc = subprocess.Popen([sys.executable, str(ROOT / "app.py")], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                base_url = f"http://127.0.0.1:{port}"
                for _ in range(60):
                    try:
                        with urllib.request.urlopen(base_url + "/healthz", timeout=.4) as response:
                            if json.loads(response.read()).get("ok"): break
                    except Exception:
                        time.sleep(.1)
                else:
                    self.fail("server did not start")
                token_file = base / "persistent" / "config" / "setup-token"
                self.assertTrue(token_file.is_file())
                token = token_file.read_text().strip()
                def post(path: str, body: dict, opener=urllib.request):
                    req = urllib.request.Request(base_url + path, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
                    with (opener.open(req, timeout=3) if hasattr(opener, "open") else urllib.request.urlopen(req, timeout=3)) as response: return json.loads(response.read())
                setup = post("/api/setup", {"token":token,"username":"joao","display_name":"João","password":"StrongPass123!","quota_gib":64,"theme":"dark"})
                self.assertTrue(setup["ok"])
                jar = http.cookiejar.CookieJar(); opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
                login = post("/api/login", {"username":"joao","password":"StrongPass123!"}, opener)
                self.assertTrue(login["ok"]); self.assertTrue(login["user"]["is_admin"])
                with opener.open(base_url + "/api/bootstrap", timeout=3) as response: boot = json.loads(response.read())
                self.assertTrue(boot["ok"]); self.assertEqual(boot["user"]["username"], "joao")
                self.assertIn("paths", boot["capabilities"])
            finally:
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    unittest.main()

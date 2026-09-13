from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from lunarx_core.appstore import annotate_catalog, apt_package_available, public_catalog
from lunarx_core.broker import handle
from lunarx_core.catalog import compatibility, find_app, load_catalog
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import detect_platform

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "apps.json"


class CatalogProviderPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_catalog(str(CATALOG))
        cls.apps = {item["id"]: item for item in cls.document["apps"]}

    def test_every_entry_has_explicit_android_proot_decision(self) -> None:
        self.assertEqual(len(self.document["apps"]), 154)
        for app in self.document["apps"]:
            providers = app.get("providers", [])
            self.assertTrue(providers, app["id"])
            self.assertTrue(any("android-proot" in provider.get("platforms", []) for provider in providers), app["id"])
            for provider in providers:
                for field in ("architectures", "platforms", "provider", "install_method", "launch_method", "update_method", "uninstall_method", "source", "compatibility_status"):
                    self.assertIn(field, provider, f"{app['id']} provider missing {field}")

    def test_native_ubuntu_arm64_uses_audited_flatpak(self) -> None:
        app = self.apps["org.videolan.VLC"]
        state = compatibility(app, platform_name="native-ubuntu", architecture="aarch64", backend="flatpak-user", package_probe=lambda _p: False)
        self.assertTrue(state["compatible"])
        self.assertEqual(state["selected_provider"]["provider"], "flatpak-user")

    def test_x86_only_flatpak_does_not_fake_native_arm64(self) -> None:
        app = self.apps["org.vinegarhq.Sober"]
        state = compatibility(app, platform_name="native-ubuntu", architecture="aarch64", backend="flatpak-user", package_probe=lambda _p: False)
        self.assertFalse(state["compatible"])
        self.assertIsNone(state["selected_provider"])

    def test_android_proot_web_fallback_is_explicit(self) -> None:
        app = self.apps["com.visualstudio.code"]
        state = compatibility(app, platform_name="android-proot", architecture="aarch64", backend="registry-only", package_probe=lambda _p: False)
        self.assertTrue(state["compatible"])
        self.assertEqual(state["selected_provider"]["provider"], "web-pwa")
        self.assertEqual(state["selected_provider"]["launch_url"], "https://vscode.dev/")

    def test_android_proot_apt_provider_requires_runtime_candidate(self) -> None:
        app = self.apps["org.videolan.VLC"]
        denied = compatibility(app, platform_name="android-proot", architecture="aarch64", backend="registry-only", package_probe=lambda _p: False)
        allowed = compatibility(app, platform_name="android-proot", architecture="aarch64", backend="registry-only", package_probe=lambda package: package == "vlc")
        self.assertFalse(denied["compatible"])
        self.assertTrue(allowed["compatible"])
        self.assertEqual(allowed["selected_provider"]["provider"], "apt-system")
        self.assertTrue(allowed["selected_provider"]["requires_admin"])

    def test_unsupported_application_explains_why(self) -> None:
        app = self.apps["md.obsidian.Obsidian"]
        state = compatibility(app, platform_name="android-proot", architecture="aarch64", backend="registry-only", package_probe=lambda _p: False)
        self.assertFalse(state["compatible"])
        self.assertIn("Obsidian", state["reason"])
        self.assertIn("PRoot", state["reason"])

    def test_catalog_annotation_exposes_selected_provider(self) -> None:
        raw = public_catalog(CATALOG, query="GeoGebra")
        value = annotate_catalog(raw, platform_name="android-proot", architecture="aarch64", backend="registry-only", package_probe=lambda _p: False)
        app = value["apps"][0]
        self.assertEqual(app["compatibility_status"], "Web/PWA fallback")
        self.assertEqual(app["selected_provider"]["provider"], "web-pwa")


class InstalledStatePatchTests(unittest.TestCase):
    def test_web_app_install_is_persistent_and_per_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":directory, "HOME":directory}
            paths = resolve_paths(ROOT, env); info = detect_platform(env=env, machine="aarch64", os_release={"ID":"ubuntu","VERSION_ID":"24.04"}, paths=paths)
            ok, _ = handle({"action":"create_user","username":"alice","password":"safe password","is_admin":False}, paths=paths, info=info)
            self.assertTrue(ok)
            ok, result = handle({"action":"user_app","username":"alice","app_id":"com.visualstudio.code","operation":"install","provider_id":"official.web"}, paths=paths, info=info)
            self.assertTrue(ok, result); self.assertEqual(result["scope"], "user")
            ok, listing = handle({"action":"user_app_list","username":"alice"}, paths=paths, info=info)
            self.assertTrue(ok); record = next(item for item in listing["apps"] if item["id"] == "com.visualstudio.code")
            self.assertEqual(record["provider"], "web-pwa"); self.assertEqual(record["launch_url"], "https://vscode.dev/")
            ok, _ = handle({"action":"user_app","username":"alice","app_id":"com.visualstudio.code","operation":"uninstall","provider_id":"official.web"}, paths=paths, info=info)
            self.assertTrue(ok)
            ok, listing = handle({"action":"user_app_list","username":"alice"}, paths=paths, info=info)
            self.assertFalse(any(item["id"] == "com.visualstudio.code" for item in listing["apps"]))

    def test_system_provider_requires_admin_even_in_direct_broker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":directory, "HOME":directory}
            paths = resolve_paths(ROOT, env); info = detect_platform(env=env, machine="aarch64", os_release={"ID":"ubuntu"}, paths=paths)
            handle({"action":"create_user","username":"alice","password":"safe password","is_admin":False}, paths=paths, info=info)
            ok, result = handle({"action":"user_app","username":"alice","app_id":"org.videolan.VLC","operation":"install","provider_id":"ubuntu.apt-arm64"}, paths=paths, info=info)
            self.assertFalse(ok); self.assertEqual(result["error_code"], "admin_required")


class InterfacePatchTests(unittest.TestCase):
    def test_sidebar_uses_local_svg_icon_language(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('/static/assets/icons.svg#i-home', html)
        self.assertIn('/static/assets/icons.svg#i-storage', html)
        self.assertIn('/static/assets/icons.svg#i-terminal', html)
        for legacy in ('<span class="nav-icon">⌂</span>', '<span class="nav-icon">⚙</span>', '<span class="nav-icon">◇</span>'):
            self.assertNotIn(legacy, html)
        self.assertTrue((ROOT / "static" / "assets" / "icons.svg").is_file())

    def test_mobile_drawer_keeps_hamburger_and_accessible_close_paths(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="menu-open"', html); self.assertIn('aria-controls="sidebar"', html)
        self.assertIn('function setDrawer(open, { restoreFocus = false } = {})', script); self.assertIn('event.key === "Escape"', script)
        self.assertIn('document.body.classList.toggle("drawer-open"', script); self.assertIn('body.drawer-open{overflow:hidden}', css)
        self.assertNotIn("bottom-nav", html)

    def test_application_details_surface_rich_metadata(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "store.js").read_text(encoding="utf-8")
        for element_id in ("app-detail-developer", "app-detail-license", "app-detail-provider", "app-detail-architecture", "app-detail-installed-version", "app-detail-available-version", "app-detail-storage", "app-detail-uninstall", "app-detail-update"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("app-fallback.svg", script)
        self.assertIn("selected_provider", script)

    def test_responsive_patch_targets_phone_tablet_and_desktop(self) -> None:
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:430px)", css)
        self.assertIn("@media(min-width:861px) and (max-width:1120px)", css)
        self.assertIn("overflow-x:hidden", css)
        self.assertIn("max-height:calc(100dvh - 20px)", css)


class PlatformAndReleaseRegressionTests(unittest.TestCase):
    def test_proot_capabilities_advertise_truthful_provider_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"LUNARX_PLATFORM":"android-proot", "LUNARX_PERSISTENT_ROOT":directory, "HOME":directory}
            paths = resolve_paths(ROOT, env)
            with mock.patch("lunarx_core.platform.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"apt-get", "apt-cache"} else None):
                info = detect_platform(env=env, machine="aarch64", os_release={"ID":"ubuntu"}, paths=paths)
            self.assertIn("web-pwa", info.capabilities); self.assertIn("apt-system", info.capabilities)
            self.assertFalse(info.flatpak)

    def test_installer_still_detects_android_proot_and_preserves_update_backup(self) -> None:
        source = (ROOT / "installer" / "lunarx_installer.py").read_text(encoding="utf-8")
        self.assertIn("android-proot", source)
        self.assertIn("backup", source.lower())
        self.assertIn("verify_health", source)

    def test_catalog_audit_has_arm64_and_x86_counts(self) -> None:
        audit = self.document = json.loads(CATALOG.read_text(encoding="utf-8"))["audit"]
        self.assertEqual(audit["entries"], 154)
        self.assertGreaterEqual(audit["flatpak_aarch64_entries"], 100)
        self.assertGreater(audit["android_proot_provider_types"]["apt-system"], 20)
        self.assertGreaterEqual(audit["android_proot_provider_types"]["web-pwa"], 10)
        self.assertEqual(audit["android_proot_actionable_apps"] + audit["android_proot_source_only_apps"] + audit["android_proot_unsupported_apps"], 154)


if __name__ == "__main__":
    unittest.main()

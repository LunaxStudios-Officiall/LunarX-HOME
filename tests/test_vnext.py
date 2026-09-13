from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from lunarx_core.auth import hash_password, verify_password
from lunarx_core.broker import handle, load_state
from lunarx_core.catalog import compatibility, load_catalog
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import detect_platform
from lunarx_core.quota import LogicalQuotaProvider, quota_check, quota_status, select_provider


ROOT = Path(__file__).resolve().parents[1]


class UniversalProviderTests(unittest.TestCase):
    def test_platform_fixtures(self) -> None:
        android = detect_platform(env={"LUNARX_PLATFORM": "android-proot", "HOME": "/data/data/com.termux/files/home"}, machine="aarch64", os_release={"ID": "ubuntu", "VERSION_ID": "24.04"})
        self.assertEqual(android.mode, "android-proot")
        self.assertEqual(android.architecture, "aarch64")
        self.assertEqual(android.quota_mode, "logical")
        native = detect_platform(env={"LUNARX_PLATFORM": "native-ubuntu"}, machine="x86_64", os_release={"ID": "ubuntu", "VERSION_ID": "24.04"})
        self.assertEqual(native.mode, "native-ubuntu")

    def test_paths_are_persistent_and_consistent(self) -> None:
        paths = resolve_paths(env={"LUNARX_PLATFORM": "android-proot", "HOME": "/tmp/lunarx-test-home"})
        self.assertTrue(str(paths.data_root).endswith("/data"))
        self.assertEqual(paths.desktop_path("alice"), paths.data_root / "users" / "alice" / "Desktop")

    def test_local_password_hash(self) -> None:
        encoded = hash_password("a long test password")
        self.assertTrue(encoded.startswith("scrypt$") or encoded.startswith("pbkdf2-sha256$"))
        self.assertTrue(verify_password("a long test password", encoded))
        self.assertFalse(verify_password("wrong password", encoded))

    def test_logical_quota_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "payload").write_bytes(b"1234")
            self.assertIsInstance(select_provider("ext4", {"rw"}), LogicalQuotaProvider)
            self.assertEqual(quota_status(root, 4)["within_limit"], True)
            self.assertFalse(quota_check(root, 4, 1))

    def test_catalog_reports_provider_compatibility(self) -> None:
        catalog = load_catalog(str(ROOT / "catalog" / "apps.json"))
        code = next(item for item in catalog["apps"] if item["id"] == "com.visualstudio.code")
        result = compatibility(code, platform_name="android-proot", architecture="aarch64", backend="registry-only")
        self.assertTrue(result["compatible"])
        self.assertEqual(result["catalog_backend"], "flatpak-user")
        self.assertEqual(result["selected_provider"]["provider"], "web-pwa")
        self.assertEqual(result["status"], "Web/PWA fallback")

    def test_direct_broker_is_structured_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = resolve_paths(env={"LUNARX_PLATFORM": "android-proot", "LUNARX_PERSISTENT_ROOT": str(root)})
            ok, result = handle({"action": "create_user", "username": "alice", "password": "safe password", "is_admin": True}, paths=paths)
            self.assertTrue(ok); self.assertEqual(result["username"], "alice")
            ok, result = handle({"action": "authenticate", "username": "alice", "password": "safe password"}, paths=paths)
            self.assertTrue(ok); self.assertTrue(result["authenticated"])
            ok, result = handle({"action": "authenticate", "username": "alice", "password": "bad password"}, paths=paths)
            self.assertTrue(ok); self.assertFalse(result["authenticated"])
            ok, result = handle({"action": "not-allowed"}, paths=paths)
            self.assertFalse(ok); self.assertEqual(result["error_code"], "unsupported_action")
            self.assertIn("alice", load_state(paths)["users"])


if __name__ == "__main__":
    unittest.main()

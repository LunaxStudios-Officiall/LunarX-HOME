from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from lunarx_core.catalog import CatalogError, find_app, load_catalog
from lunarx_core.quota import LogicalQuotaProvider, QuotaError, XfsImageQuotaProvider, XfsProjectQuotaProvider, select_provider
from lunarx_core.storage import GIB, StoragePlanError, capacity_plan, stable_disk_id, validate_capacity


ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def test_catalog_is_large_unique_and_complete(self) -> None:
        document = load_catalog(str(ROOT / "catalog" / "apps.json"))
        self.assertGreaterEqual(len(document["apps"]), 150)
        self.assertEqual(len(document["apps"]), len({item["id"] for item in document["apps"]}))
        self.assertEqual(find_app(str(ROOT / "catalog" / "apps.json"), "com.visualstudio.code")["backend"], "flatpak-user")
        self.assertEqual(find_app(str(ROOT / "catalog" / "apps.json"), "com.google.CloudCode.VSCode")["extension_id"], "GoogleCloudTools.cloudcode")
        self.assertFalse(find_app(str(ROOT / "catalog" / "apps.json"), "dev.nousresearch.HermesAgent")["installable"])

    def test_unknown_application_is_rejected(self) -> None:
        with self.assertRaises(CatalogError):
            find_app(str(ROOT / "catalog" / "apps.json"), "not.in.catalog")


class QuotaTests(unittest.TestCase):
    def test_native_provider_requires_xfs_project_option(self) -> None:
        self.assertIsInstance(select_provider("xfs", {"rw", "prjquota"}), XfsProjectQuotaProvider)
        self.assertIsInstance(select_provider("ext4", {"rw"}), LogicalQuotaProvider)
        self.assertIsInstance(select_provider("ext4", {"rw"}, "xfs-image-project"), XfsImageQuotaProvider)
        with self.assertRaises(QuotaError):
            select_provider("ext4", {"rw"}, "xfs-project")

    def test_commands_have_no_shell_interpolation(self) -> None:
        commands = XfsProjectQuotaProvider().limit_commands(Path("/srv/lunarx-data"), "lx_joao", 256)
        self.assertEqual(commands[0][0], "/usr/sbin/xfs_quota")
        with self.assertRaises(QuotaError):
            XfsProjectQuotaProvider().limit_commands(Path("/"), "x;rm", 1)


class StorageTests(unittest.TestCase):
    def test_stable_disk_identifiers(self) -> None:
        self.assertEqual(stable_disk_id("UUID=12345678-abcd"), "UUID=12345678-abcd")
        self.assertEqual(stable_disk_id("/dev/disk/by-id/usb-Example_123-0:0"), "/dev/disk/by-id/usb-Example_123-0:0")
        for invalid in ("/dev/sdb", "LABEL=data", "../../dev/sdb"):
            with self.assertRaises(StoragePlanError):
                stable_disk_id(invalid)

    def test_capacity_preserves_larger_reserve(self) -> None:
        usage = mock.Mock(total=100 * GIB, free=60 * GIB)
        with mock.patch("lunarx_core.storage.shutil.disk_usage", return_value=usage):
            plan = capacity_plan(Path("/"), 39 * GIB, reserve_gib=20, reserve_percent=20)
            validate_capacity(plan)
            self.assertEqual(plan.allocatable_bytes, 40 * GIB)
            with self.assertRaises(StoragePlanError):
                validate_capacity(capacity_plan(Path("/"), 41 * GIB, reserve_gib=20, reserve_percent=20))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProductContractTests(unittest.TestCase):
    def test_runtime_has_no_workspace_tiering(self) -> None:
        targets = [ROOT / "app.py", ROOT / "lunarx-admin.py", ROOT / "static" / "app.js"]
        forbidden = ("workspace_cold", "WORKSPACE_COLD", "TIER_MANAGER", "workspace_tier")
        for target in targets:
            content = target.read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, content, f"{value} remains in {target.name}")

    def test_admin_is_not_redirected_out_of_user_views(self) -> None:
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('$("#user-nav").classList.remove("hidden")', script)
        self.assertNotIn('if(admin&&!name.startsWith("admin-"))', script)

    def test_no_secrets_in_default_configuration(self) -> None:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        serialized = json.dumps(config).lower()
        for marker in ("password", "token", "private_key", "secret="):
            self.assertNotIn(marker, serialized)

    def test_install_command_is_portable(self) -> None:
        wrapper = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"$SCRIPT_DIR/installer/lunarx_installer.py"', wrapper)
        self.assertNotIn("github.com/OWNER", wrapper)


if __name__ == "__main__":
    unittest.main()

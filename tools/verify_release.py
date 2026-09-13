#!/usr/bin/env python3
"""Verify the single clean LunarX Home archive."""

from __future__ import annotations

import argparse
import sys

# Release tooling must not create __pycache__ inside the source tree it validates.
sys.dont_write_bytecode = True
import json
from pathlib import Path
import re
import tempfile
import zipfile

from validate_release import validate


VERSION = "3.1.3"
FORBIDDEN_NAMES = {"home.env", "state.json", "quotas.json", "setup-token", "authorized_keys", ".ssh"}
SECRET_PATTERNS = (re.compile(r"-----BEGIN .*PRIVATE KEY-----"), re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"))


def verify_archive(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)): raise ValueError("duplicate archive member")
        prefix = f"LunarX-Home-{VERSION}/"
        if prefix + "install.sh" not in names: raise ValueError("missing repository root")
        for name, info in ((info.filename, info) for info in archive.infolist()):
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts: raise ValueError(f"unsafe archive path: {name}")
            if any(part in FORBIDDEN_NAMES for part in relative.parts): raise ValueError(f"private archive item: {name}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000: raise ValueError(f"symlink archive member: {name}")
        with tempfile.TemporaryDirectory(prefix="lunarx-release-") as temporary:
            archive.extractall(temporary); root = Path(temporary) / prefix.rstrip("/"); result = validate(root)
            if not result["ok"]: raise ValueError(f"extracted validation failed: {result['errors']}")
            for file in root.rglob("*"):
                if not file.is_file() or file.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} or file.name in {"validate_release.py", "verify_release.py", "package_release.py"}: continue
                try: text = file.read_text(encoding="utf-8")
                except UnicodeDecodeError: continue
                if any(pattern.search(text) for pattern in SECRET_PATTERNS): raise ValueError(f"possible secret: {file.relative_to(root)}")
            return {"archive":path.name,"members":len(names),"bytes":path.stat().st_size,"sha256":__import__("hashlib").sha256(path.read_bytes()).hexdigest(),"validation":result["evidence"]}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--release", type=Path, required=True); args = parser.parse_args(); release = args.release.resolve(); archives = sorted(release.glob(f"LunarX-Home-{VERSION}.zip"));
    if len(archives) != 1: raise SystemExit(f"expected exactly one clean release ZIP, found {len(archives)}")
    report = verify_archive(archives[0]); print(json.dumps({"ok":True,"zip_count":1,"archives":[report]}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())

#!/usr/bin/env python3
"""Build one deterministic, clean LunarX Home release archive."""

from __future__ import annotations

import argparse
import sys

# Release tooling must not create __pycache__ inside the source tree it validates.
sys.dont_write_bytecode = True
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from validate_release import validate


VERSION = "3.1.2"
STAMP = (2026, 9, 12, 19, 30, 0)
EXCLUDE_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "build", "dist", "packaging", "validation"}
EVIDENCE = {"PACKAGE_MANIFEST.json", "VALIDATION.json", "FINAL_BUILD_REPORT.md", "SHA256SUMS.txt"}


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def members(root: Path) -> list[Path]:
    output = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in EXCLUDE_PARTS for part in relative.parts) or path.name in EVIDENCE:
            continue
        if path.suffix.lower() in {".zip", ".tar", ".gz", ".pyc", ".img", ".vhdx"}:
            continue
        output.append(path)
    return sorted(output, key=lambda item: item.relative_to(root).as_posix())


def write_zip(root: Path, target: Path, files: list[Path], generated: dict[str, bytes]) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = f"LunarX-Home-{VERSION}/{path.relative_to(root).as_posix()}"
            info = zipfile.ZipInfo(relative, STAMP); info.external_attr = (0o755 if path.name in {"install.sh", "bootstrap.sh", "lunarx-admin.py", "lunarx-terminal", "lunarxctl", "lunarx_installer.py", "lunarx-desktop-session.py", "lunarx-desktop-governor.py", "lunarx-user-apps"} else 0o644) << 16; info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, path.read_bytes())
        for name, content in sorted(generated.items()):
            info = zipfile.ZipInfo(f"LunarX-Home-{VERSION}/{name}", STAMP); info.external_attr = 0o644 << 16; info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, content)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--source", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--validation", type=Path); args = parser.parse_args(); root, output = args.source.resolve(), args.output.resolve(); output.mkdir(mode=0o750, parents=True, exist_ok=True)
    initial = validate(root)
    if not initial["ok"]: raise SystemExit(f"validation is not PASS; refusing to package: {initial['errors']}")
    files = members(root)
    manifest = {"schema_version":"2.0.0", "product":"LunarX Home", "version":VERSION, "generated_at":"2026-09-12T19:30:00Z", "license_status":"SEE_LICENSE_AUDIT", "catalog_entries":initial.get("evidence",{}).get("catalog_entries"), "manifest_scope":"all clean source files excluding generated evidence: PACKAGE_MANIFEST.json, VALIDATION.json, FINAL_BUILD_REPORT.md, SHA256SUMS.txt", "files":[{"path":path.relative_to(root).as_posix(),"bytes":path.stat().st_size,"sha256":digest(path)} for path in files], "tree_file_count":len(files), "tree_bytes":sum(path.stat().st_size for path in files), "exclusions":sorted(EXCLUDE_PARTS | EVIDENCE | {"home.env","state.json","quotas.json","setup-token"})}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    report = (root / "FINAL_BUILD_REPORT.md").read_bytes() if (root / "FINAL_BUILD_REPORT.md").is_file() else b"# LunarX Home 3.1.2 final build report\n\nSee VALIDATION.json and SHA256SUMS.txt for evidence.\n"
    validation = validate(root)
    validation["package"] = {"version":VERSION,"manifest_scope":manifest["manifest_scope"],"clean_source_files":len(files),"clean_source_bytes":sum(path.stat().st_size for path in files)}
    validation_bytes = (json.dumps(validation, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    evidence_payload = {"PACKAGE_MANIFEST.json":manifest_bytes,"VALIDATION.json":validation_bytes,"FINAL_BUILD_REPORT.md":report}
    sums = "\n".join(f"{digest_bytes(content)}  {name}" for name, content in sorted({**{path.relative_to(root).as_posix():path.read_bytes() for path in files}, **evidence_payload}.items())) + "\n"
    evidence_payload["SHA256SUMS.txt"] = sums.encode("ascii")
    for name, content in evidence_payload.items(): (root / name).write_bytes(content)
    target = output / f"LunarX-Home-{VERSION}.zip"
    if target.exists(): target.unlink()
    write_zip(root, target, files, evidence_payload)
    for name, content in evidence_payload.items(): (output / name).write_bytes(content)
    archive_sum = f"{digest(target)}  {target.name}\n"
    (output / "SHA256SUMS.txt").write_text(sums + archive_sum, encoding="ascii")
    (output / f"{target.name}.sha256").write_text(archive_sum, encoding="ascii")
    print(json.dumps({"ok":True,"zip_count":1,"archive":str(target),"sha256":digest(target),"clean_source_files":len(files),"clean_source_bytes":sum(path.stat().st_size for path in files)}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())

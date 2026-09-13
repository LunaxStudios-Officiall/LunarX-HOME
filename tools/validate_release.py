#!/usr/bin/env python3
"""Static, component and package-shape validation for LunarX Home 3.1."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


VERSION = "3.1.2"
REQUIRED = [
    "VERSION", "README.md", "INSTALL_COMMAND.txt", "UPDATE_COMMAND.txt", "INSTALLATION.md", "UPDATE.md", "UNINSTALL_AND_ROLLBACK.md", "COMPATIBILITY_MATRIX.md", "MIGRATION_PLAN.md", "MIGRATION_REPORT.md", "INSTALLER_QA.md", "UPDATER_QA.md", "UI_QA.md", "SECURITY_CHANGES.md", "KNOWN_LIMITATIONS.md", "FINAL_BUILD_REPORT.md", "CHANGELOG.md", "PACKAGE_MANIFEST.json", "VALIDATION.json", "app.py", "lunarx-admin.py", "install.sh", "bootstrap.sh", "lunarxctl", "installer/lunarx_installer.py", "catalog/apps.json", "catalog/schema.json", "static/index.html", "static/app.js", "static/store.js", "static/styles.css", "docs/ARCHITECTURE.md", "docs/PROVIDERS.md", "CATALOG_ARM64_AUDIT.md"
]
FORBIDDEN_NAMES = {".env", "home.env", "state.json", "quotas.json", "node_modules", ".venv", "__pycache__", ".pytest_cache", "setup-token"}
SECRET_PATTERNS = [re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), re.compile(r"(?i)\b(?:aws_secret_access_key|private_key|client_secret)\s*[:=]\s*['\"][^'\"]{12,}['\"]")]
EXCLUDED_EVIDENCE = {"PACKAGE_MANIFEST.json", "VALIDATION.json", "SHA256SUMS.txt", "FINAL_BUILD_REPORT.md"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duplicate_functions(script: str) -> list[str]:
    names = re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", script)
    names += re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", script)
    return sorted({name for name in names if names.count(name) > 1})


def validate(root: Path) -> dict[str, object]:
    root = root.resolve(); errors: list[str] = []; warnings: list[str] = []; evidence: dict[str, object] = {}
    errors.extend(f"missing required file: {name}" for name in REQUIRED if not (root / name).is_file())
    version = (root / "VERSION").read_text(encoding="utf-8").strip() if (root / "VERSION").is_file() else ""
    if version != VERSION: errors.append(f"unexpected version: {version!r}")
    files = [path for path in root.rglob("*") if path.is_file()]
    bad_names = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.name in FORBIDDEN_NAMES)
    errors.extend(f"forbidden package item: {name}" for name in bad_names)
    for path in files:
        if path.stat().st_size > 50 * 1024 * 1024: errors.append(f"unexpected large file: {path.relative_to(root)}")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2", ".zip", ".gz"}: continue
        try: content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError): continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content): errors.append(f"possible secret in {path.relative_to(root)}"); break
    for path in files:
        if path.suffix == ".json":
            try: json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc: errors.append(f"invalid JSON {path.relative_to(root)}: {exc}")
        if path.suffix == ".py":
            try: compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc: errors.append(f"Python syntax error {path.relative_to(root)}:{exc.lineno}: {exc.msg}")
    catalog_path = root / "catalog" / "apps.json"
    if catalog_path.is_file():
        try: catalog = json.loads(catalog_path.read_text(encoding="utf-8")); apps = catalog.get("apps", [])
        except (ValueError, OSError) as exc: apps = []; errors.append(f"catalog could not be loaded: {exc}")
        ids = [item.get("id") for item in apps if isinstance(item, dict)]
        if catalog.get("schema_version") != "3.0.0": errors.append("catalog schema must be 3.0.0")
        if len(apps) < 150 or len(ids) != len(set(ids)): errors.append("catalog must contain at least 150 unique entries")
        for required_id in ("com.visualstudio.code", "dev.nousresearch.HermesAgent", "com.google.CloudCode.VSCode", "lunarx.multimedia-codecs"):
            if required_id not in ids: errors.append(f"required catalog entry missing: {required_id}")
        provider_records = 0; proot_decisions = 0; web_fallbacks = 0; arm64_native = 0
        for item in apps:
            if not all(key in item for key in ("platforms", "architectures", "launch_mode", "privilege", "backend_requirements", "verification_status", "providers")): errors.append(f"catalog metadata incomplete: {item.get('id')}")
            providers = item.get("providers", [])
            if not isinstance(providers, list) or not providers: errors.append(f"catalog providers missing: {item.get('id')}"); continue
            provider_records += len(providers)
            proot = [provider for provider in providers if isinstance(provider, dict) and "android-proot" in provider.get("platforms", []) and ("aarch64" in provider.get("architectures", []) or "all" in provider.get("architectures", []))]
            if proot: proot_decisions += 1
            else: errors.append(f"Android/PRoot provider decision missing: {item.get('id')}")
            if any(provider.get("provider") == "web-pwa" for provider in providers if isinstance(provider, dict)): web_fallbacks += 1
            if any(provider.get("provider_type") == "native" and "aarch64" in provider.get("architectures", []) for provider in providers if isinstance(provider, dict)): arm64_native += 1
            for provider in providers:
                if not isinstance(provider, dict): errors.append(f"invalid provider record: {item.get('id')}"); continue
                for key in ("id", "provider", "provider_type", "platforms", "architectures", "install_method", "launch_method", "update_method", "uninstall_method", "source", "compatibility_status"):
                    if key not in provider: errors.append(f"provider metadata incomplete {item.get('id')}: {key}")
                if provider.get("provider") == "unsupported" and not str(provider.get("reason") or "").strip(): errors.append(f"unsupported provider reason missing: {item.get('id')}")
        evidence.update({"catalog_entries": len(apps), "catalog_provider_records": provider_records, "android_proot_decisions": proot_decisions, "web_pwa_fallback_apps": web_fallbacks, "arm64_native_apps": arm64_native})
    runtime = "\n".join((root / name).read_text(encoding="utf-8") for name in ("app.py", "lunarx-admin.py", "static/app.js"))
    for marker in ("WORKSPACE_COLD", "workspace_cold", "TIER_MANAGER", "workspace_tier"):
        if marker in runtime: errors.append(f"obsolete tier runtime marker remains: {marker}")
    script = (root / "static" / "app.js").read_text(encoding="utf-8") if (root / "static" / "app.js").is_file() else ""
    if duplicate_functions(script): errors.append(f"duplicate JavaScript functions: {duplicate_functions(script)}")
    html = (root / "static" / "index.html").read_text(encoding="utf-8") if (root / "static" / "index.html").is_file() else ""
    ids = re.findall(r'\bid="([^"]+)"', html); duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates: errors.append(f"duplicate HTML ids: {duplicates}")
    if 'name="viewport"' not in html: errors.append("responsive viewport metadata missing")
    if "store.js" not in html: errors.append("application store script is not loaded")
    node = shutil.which("node")
    if node:
        for relative in ("static/app.js", "static/store.js"):
            result = subprocess.run([node, "--check", str(root / relative)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode: errors.append(f"JavaScript syntax error {relative}: {result.stderr.strip()}")
    else: warnings.append("Node.js unavailable; JavaScript syntax check skipped")
    manifest = root / "PACKAGE_MANIFEST.json"
    if manifest.is_file():
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
            if value.get("version") != VERSION: errors.append("package manifest version mismatch")
            if not isinstance(value.get("files"), list): errors.append("package manifest files list missing")
        except (ValueError, OSError) as exc: errors.append(f"invalid package manifest: {exc}")
    evidence.update({"version": version, "file_count": len(files), "tree_bytes": sum(path.stat().st_size for path in files), "catalog_sha256": sha256(catalog_path) if catalog_path.is_file() else None, "node_syntax_checked": bool(node), "duplicate_function_check": "PASS" if not duplicate_functions(script) else "FAIL", "manifest_excluded_evidence": sorted(EXCLUDED_EVIDENCE)})
    return {"ok": not errors, "errors": errors, "warnings": warnings, "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--source", type=Path, required=True); parser.add_argument("--json-out", type=Path); args = parser.parse_args(); result = validate(args.source)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.json_out: args.json_out.parent.mkdir(parents=True, exist_ok=True); args.json_out.write_text(output, encoding="utf-8")
    print(output, end=""); return 0 if result["ok"] else 1


if __name__ == "__main__": raise SystemExit(main())

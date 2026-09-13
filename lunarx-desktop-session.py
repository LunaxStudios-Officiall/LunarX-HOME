#!/usr/bin/env python3
"""Inspect and validate the configured Desktop provider without fabricating a session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import detect_platform
from lunarx_core.providers import DesktopProvider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "doctor"])
    args = parser.parse_args()
    paths = resolve_paths(ROOT); info = detect_platform(paths=paths); result = DesktopProvider(info).status().public(); result["platform"] = info.as_dict(); result["paths"] = paths.as_dict(); print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["ok"] or args.command == "doctor" else 1


if __name__ == "__main__": raise SystemExit(main())

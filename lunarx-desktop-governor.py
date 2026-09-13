#!/usr/bin/env python3
"""Conservative cgroup-v2 governance for LunarX XRDP sessions."""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

APP_ROOT = Path(os.environ.get("LUNARX_APP_ROOT", "/opt/lunarx-home")).resolve()
import sys
sys.path.insert(0, str(APP_ROOT))
from lunarx_core.paths import resolve_paths
from lunarx_core.platform import detect_platform

PATHS = resolve_paths(APP_ROOT)
CONFIG = PATHS.config_file
STATE = PATHS.state_file
RUNTIME = PATHS.runtime_root
ACTIVITY = RUNTIME / "desktop-activity.json"
STATUS = RUNTIME / "resource-status.json"
SNAPSHOT = RUNTIME / "desktop-process-snapshot.json"
BASELINE = {
    "Xorg", "xrdp-chansrv", "xfce4-session", "xfwm4", "xfce4-panel", "xfdesktop",
    "xfsettingsd", "xfconfd", "xfce4-power-man", "xfce4-power-manager", "Thunar",
    "xfce4-terminal", "bash", "sh", "dash", "dbus-daemon", "dbus-launch", "ssh-agent",
    "gpg-agent", "at-spi-bus-laun", "at-spi2-registr", "gvfsd", "gvfsd-fuse",
    "gvfs-udisks2-vo", "gvfs-mtp-volume", "gvfs-goa-volume", "gvfs-afc-volume",
    "gvfs-gphoto2-vo", "dconf-service", "pulseaudio", "pipewire", "wireplumber",
    "polkit-gnome-au", "nm-applet", "light-locker", "xscreensaver", "tumblerd",
    "polkit-mate-aut", "wrapper-2.0", "xiccd", "xrdp-sesexec", "systemd", "(sd-pam)",
    "bwrap", "glycin-image-rs", "glycin-svg",
    "epiphany", "WebKitWebProces", "WebKitNetworkPr", "WebKitWebProcess",
    "WebKitNetworkProcess", "xdg-desktop-por", "xdg-document-po", "xdg-permission-",
}
MEANINGFUL = re.compile(
    r"(^|/|\b)(codex|node|npm|pnpm|yarn|python\d*|perl|ruby|java|javac|gcc|g\+\+|clang|make|ninja|cargo|rustc|go|dotnet|"
    r"curl|wget|aria2c|rsync|rclone|scp|sftp|apt|apt-get|dpkg|snap|tar|zip|unzip|7z|ffmpeg|ffprobe|handbrake|"
    r"convert|magick|blender|git|restic|borg|zstd|xz|gzip|bzip2)(\b|$)", re.I,
)


def run(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False, env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def load(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8")); return value if isinstance(value, dict) else fallback
    except (OSError, ValueError): return fallback


def atomic(path: Path, value: dict[str, Any], mode: int = 0o644) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", delete=False) as handle:
        temp = Path(handle.name); json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True); handle.write("\n")
    os.chmod(temp, mode); os.replace(temp, path)


def properties(unit: str, names: list[str]) -> dict[str, str]:
    result = run(["systemctl", "show", unit] + [item for name in names for item in ("-p", name)])
    output: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep: output[key] = value
    return output


def sessions() -> list[dict[str, Any]]:
    result = run(["loginctl", "list-sessions", "--no-legend", "--no-pager"]); output = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields: continue
        sid = fields[0]; info = properties(sid if sid.endswith(".scope") else f"session-{sid}.scope", []) if False else {}
        shown = run(["loginctl", "show-session", sid, "-p", "Name", "-p", "User", "-p", "Type", "-p", "Class", "-p", "Remote", "-p", "State", "-p", "Leader", "-p", "Scope", "-p", "TimestampMonotonic"])
        for item in shown.stdout.splitlines():
            key, sep, value = item.partition("=")
            if sep: info[key] = value
        if info.get("Type") == "x11" and info.get("Class") == "user" and info.get("Remote") == "yes" and info.get("Scope"):
            info["id"] = sid; output.append(info)
    return output


def number(path: Path) -> int:
    try:
        value = path.read_text(encoding="ascii").strip(); return int(value) if value not in {"", "max"} else 0
    except (OSError, ValueError): return 0


def process_start_epoch(pid: int, fallback: float) -> float:
    try:
        boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime "))
        fields = (Path("/proc") / str(pid) / "stat").read_text().split(); return boot + int(fields[21]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration): return fallback


def session_processes(control_group: str) -> list[dict[str, Any]]:
    root = Path("/sys/fs/cgroup") / control_group.lstrip("/"); pids: set[int] = set()
    for source in root.rglob("cgroup.procs") if root.is_dir() else []:
        try: pids.update(int(value) for value in source.read_text().split())
        except (OSError, ValueError): continue
    output = []
    for pid in sorted(pids):
        try:
            comm = (Path("/proc") / str(pid) / "comm").read_text().strip()
            cmdline = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            stat = (Path("/proc") / str(pid) / "stat").read_text().split(); ticks = int(stat[13]) + int(stat[14])
            io_values = {}
            for line in (Path("/proc") / str(pid) / "io").read_text().splitlines():
                key, _, value = line.partition(":"); io_values[key] = int(value.strip())
            output.append({"pid": pid, "comm": comm, "cmdline": cmdline[:500], "ticks": ticks, "write_bytes": io_values.get("write_bytes", 0)})
        except (OSError, ValueError, IndexError): continue
    return output


def process_decision(items: list[dict[str, Any]], previous: dict[str, Any]) -> tuple[bool, list[str], int, int]:
    unknown = []; cpu_delta = 0; write_delta = 0
    old = previous.get("processes", {}) if isinstance(previous.get("processes"), dict) else {}
    for item in items:
        key = str(item["pid"]); prior = old.get(key, {}) if isinstance(old.get(key), dict) else {}
        cpu_delta += max(0, int(item["ticks"]) - int(prior.get("ticks", item["ticks"])))
        write_delta += max(0, int(item["write_bytes"]) - int(prior.get("write_bytes", item["write_bytes"])))
        label = f"{item['comm']} {item['cmdline']}"
        if MEANINGFUL.search(label): unknown.append(item["comm"])
        elif item["comm"] not in BASELINE and not item["comm"].startswith(("gvfs", "xfce4-", "WebKit", "xdg-")): unknown.append(item["comm"])
    active_io = cpu_delta > max(100, os.sysconf("SC_CLK_TCK") * 2) or write_delta > 4 * 1024 * 1024
    return bool(unknown) or active_io, sorted(set(unknown)), cpu_delta, write_delta


def top_processes() -> list[dict[str, Any]]:
    result = run(["ps", "-eo", "user=,pid=,pcpu=,rss=,comm=", "--sort=-rss"]); output = []
    for line in result.stdout.splitlines()[:16]:
        fields = line.split(None, 4)
        if len(fields) == 5:
            try: output.append({"user": fields[0], "pid": int(fields[1]), "cpu_percent": float(fields[2]), "memory_bytes": int(fields[3]) * 1024, "process": fields[4]})
            except ValueError: continue
    return output


def docker_consumers() -> list[dict[str, Any]]:
    result = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=25); output = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line); output.append({"name": item.get("Name"), "memory": item.get("MemUsage"), "memory_percent": item.get("MemPerc"), "cpu_percent": item.get("CPUPerc")})
        except ValueError: continue
    return output


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--dry-run", action="store_true"); args = parser.parse_args()
    info = detect_platform(paths=PATHS)
    if info.service_manager != "systemd":
        atomic(STATUS, {"timestamp": int(time.time()), "available": False, "reason": "desktop governance requires the native service manager", "platform": info.as_dict()})
        print(json.dumps({"ok": True, "available": False, "platform": info.as_dict()}, ensure_ascii=False))
        return 0
    config = load(CONFIG, {}); state = load(STATE, {}); settings = state.get("settings", {}) if isinstance(state.get("settings"), dict) else {}
    pool = int(float(config.get("desktop_pool_gib", 5)) * 1024 ** 3); timeout_minutes = max(5, min(240, int(settings.get("desktop_idle_timeout_minutes", 10))))
    cleanup = bool(config.get("desktop_idle_cleanup", True)) and not args.dry_run; now = time.time(); activity = load(ACTIVITY, {}); previous = load(SNAPSHOT, {})
    found = sessions(); count = len(found); fair = pool // max(1, count); report_sessions = []; next_snapshot: dict[str, Any] = {"timestamp": int(now), "sessions": {}}
    for item in found:
        scope = item["Scope"]; props = properties(scope, ["ControlGroup", "MemoryCurrent", "MemorySwapCurrent"]); group = props.get("ControlGroup", "")
        memory_low = min(512 * 1024 ** 2, max(128 * 1024 ** 2, fair // 10))
        applied = run(["systemctl", "set-property", "--runtime", scope, f"MemoryLow={memory_low}", f"MemoryHigh={fair}", "MemoryMax=infinity"])
        processes = session_processes(group); old_session = previous.get("sessions", {}).get(item["id"], {}) if isinstance(previous.get("sessions"), dict) else {}
        meaningful, names, cpu_delta, write_delta = process_decision(processes, old_session)
        user_activity = activity.get(item["Name"], {}) if isinstance(activity.get(item["Name"]), dict) else {}
        last_input = float(user_activity.get("last_input", 0) or 0); keep_until = float(user_activity.get("keep_until", 0) or 0)
        if not last_input:
            leader = int(item.get("Leader", 0) or 0)
            last_input = process_start_epoch(leader, now)
        idle_seconds = max(0, int(now - last_input)); decision = "active"
        if keep_until > now: decision = "explicit-keep-alive"
        elif meaningful: decision = "meaningful-workload"
        elif idle_seconds >= timeout_minutes * 60:
            decision = "would-clean" if not cleanup else "cleaned"
            if cleanup: run(["loginctl", "terminate-session", item["id"]], timeout=20)
        next_snapshot["sessions"][item["id"]] = {"processes": {str(p["pid"]): {"ticks": p["ticks"], "write_bytes": p["write_bytes"]} for p in processes}}
        report_sessions.append({"id": item["id"], "username": item["Name"], "scope": scope, "state": item.get("State"), "memory_bytes": int(props.get("MemoryCurrent", "0") or 0), "swap_bytes": int(props.get("MemorySwapCurrent", "0") or 0), "memory_low_bytes": memory_low, "memory_high_bytes": fair, "memory_max": "infinity (aggregate parent enforces 5 GiB)", "limits_applied": applied.returncode == 0, "limit_error": applied.stderr.strip()[:300], "idle_seconds": idle_seconds, "keep_until": int(keep_until), "meaningful_processes": names, "cpu_tick_delta": cpu_delta, "write_byte_delta": write_delta, "decision": decision})
    atomic(SNAPSHOT, next_snapshot, 0o600)
    user_group = Path("/sys/fs/cgroup/user.slice")
    status = {"timestamp": int(now), "dry_run": args.dry_run, "cleanup_enabled": cleanup, "idle_timeout_minutes": timeout_minutes, "desktop_pool_total_bytes": pool, "desktop_pool_used_bytes": sum(int(item.get("memory_bytes", 0)) for item in report_sessions if item.get("decision") != "cleaned"), "desktop_pool_accounted_bytes": number(user_group / "memory.current"), "desktop_pool_swap_bytes": number(user_group / "memory.swap.current"), "active_desktop_sessions": count, "fair_share_bytes": fair, "sessions": report_sessions, "top_processes": top_processes(), "containers": docker_consumers()}
    atomic(STATUS, status)
    return 0


if __name__ == "__main__": raise SystemExit(main())

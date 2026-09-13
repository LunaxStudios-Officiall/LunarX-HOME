"""Remote-access diagnostics for LunarX Home.

The module is intentionally read-only: it reports whether Tailscale is available
and which private URL would expose the Home UI. Android/PRoot treats the Android
Tailscale app as the owner of the tunnel instead of pretending the guest can
control host networking.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from .platform import PlatformInfo, detect_platform


def tailscale_status(info: PlatformInfo | None = None, port: int = 8787) -> dict[str, Any]:
    info = info or detect_platform()
    if info.is_proot or info.is_termux:
        return {
            "provider": "android-host-app",
            "available": None,
            "state": "host-owned",
            "controllable": False,
            "ip": None,
            "dns_name": None,
            "url": None,
            "guidance": "Use the Tailscale Android app. LunarX Home inside PRoot does not control the host VPN.",
        }
    binary = shutil.which("tailscale")
    if not binary:
        return {
            "provider": "tailscale-cli",
            "available": False,
            "state": "not-installed",
            "controllable": False,
            "ip": None,
            "dns_name": None,
            "url": None,
            "guidance": "Tailscale is not installed. LAN/local access remains available.",
        }
    try:
        result = subprocess.run(
            [binary, "status", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=6,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "provider": "tailscale-cli",
            "available": True,
            "state": "error",
            "controllable": True,
            "ip": None,
            "dns_name": None,
            "url": None,
            "guidance": "Tailscale is installed, but its local status could not be read.",
            "diagnostic": str(exc)[:240],
        }
    if result.returncode:
        return {
            "provider": "tailscale-cli",
            "available": True,
            "state": "needs-login-or-stopped",
            "controllable": True,
            "ip": None,
            "dns_name": None,
            "url": None,
            "guidance": "Authorize Tailscale on the host or run 'tailscale up' there.",
        }
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return {
            "provider": "tailscale-cli",
            "available": True,
            "state": "invalid-status",
            "controllable": True,
            "ip": None,
            "dns_name": None,
            "url": None,
            "guidance": "The local Tailscale response could not be interpreted.",
        }
    ips = data.get("TailscaleIPs") or []
    ip = next((value for value in ips if isinstance(value, str) and ":" not in value), ips[0] if ips else None)
    self_node = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    dns_name = str(self_node.get("DNSName") or "").rstrip(".") or None
    state = str(data.get("BackendState") or "unknown").lower()
    address = dns_name or ip
    return {
        "provider": "tailscale-cli",
        "available": True,
        "state": state,
        "controllable": True,
        "ip": ip,
        "dns_name": dns_name,
        "url": f"http://{address}:{port}" if address else None,
        "guidance": "Private remote access through Tailscale is available." if address else "Tailscale is present but no private address is currently available.",
    }


__all__ = ["tailscale_status"]

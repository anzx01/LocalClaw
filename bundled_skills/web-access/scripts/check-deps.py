#!/usr/bin/env python
"""LocalClaw-friendly dependency check for the web-access CDP runtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


HEALTH_URL = "http://127.0.0.1:3456/health"


def _print(label: str, status: str, detail: str) -> None:
    print(f"{label}: {status} - {detail}")


def check_node() -> bool:
    node_path = shutil.which("node")
    if not node_path:
        _print("node", "missing", "Install Node.js 22+")
        return False

    try:
        completed = subprocess.run(
            [node_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception as exc:
        _print("node", "error", str(exc))
        return False

    version = completed.stdout.strip() or completed.stderr.strip()
    major = version.lstrip("v").split(".", 1)[0]
    try:
        major_num = int(major)
    except ValueError:
        _print("node", "warn", f"unrecognized version {version}")
        return True

    if major_num < 22:
        _print("node", "warn", f"{version} detected, 22+ recommended")
        return True

    _print("node", "ok", version)
    return True


def check_chrome_debugging() -> bool:
    candidates = []
    local_app_data = os.getenv("LOCALAPPDATA", "")
    if local_app_data:
        candidates.extend(
            [
                Path(local_app_data) / "Google" / "Chrome" / "User Data" / "DevToolsActivePort",
                Path(local_app_data) / "Chromium" / "User Data" / "DevToolsActivePort",
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            _print("chrome", "ok", f"found {candidate}")
            return True

    _print(
        "chrome",
        "pending",
        "Open chrome://inspect/#remote-debugging and enable Allow remote debugging for this browser instance",
    )
    return False


def check_proxy() -> bool:
    try:
        with urlopen(HEALTH_URL, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (URLError, OSError, ValueError):
        _print("proxy", "pending", "not running or not reachable on 127.0.0.1:3456")
        return False

    connected = bool(payload.get("connected"))
    if connected:
        _print("proxy", "ok", "running and connected")
        return True

    _print("proxy", "pending", "running but Chrome is not connected yet")
    return False


def main() -> int:
    node_ok = check_node()
    chrome_ok = check_chrome_debugging()
    proxy_ok = check_proxy()
    return 0 if node_ok and (chrome_ok or proxy_ok) else 1


if __name__ == "__main__":
    sys.exit(main())

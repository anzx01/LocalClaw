"""Lightweight entrypoint for running the LocalClaw web server."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from typing import List, Tuple


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8016


def _get_port() -> int:
    raw = os.getenv("LOCALCLAW_SERVER_PORT", str(DEFAULT_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        print(f"Invalid LOCALCLAW_SERVER_PORT={raw!r}, falling back to {DEFAULT_PORT}.")
        return DEFAULT_PORT
    if not (1 <= port <= 65535):
        print(f"Out-of-range LOCALCLAW_SERVER_PORT={port}, falling back to {DEFAULT_PORT}.")
        return DEFAULT_PORT
    return port


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _is_windows() -> bool:
    return os.name == "nt"


def _address_matches_port(local_address: str, port: int) -> bool:
    normalized = str(local_address or "").strip()
    if not normalized:
        return False
    return normalized.endswith(f":{port}")


def _find_port_owner_pids(host: str, port: int) -> List[int]:
    del host  # Netstat output can vary by bind host; we filter by port.
    if not _is_windows():
        return []

    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []

    if completed.returncode != 0:
        return []

    pids: List[int] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].upper()
        local_address = parts[1]
        state = parts[3].upper()
        pid_token = parts[4]
        if not proto.startswith("TCP"):
            continue
        if state != "LISTENING":
            continue
        if not _address_matches_port(local_address, port):
            continue
        try:
            pid = int(pid_token)
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def _get_process_details(pid: int) -> Tuple[str, str]:
    if not _is_windows():
        return "", ""

    command = (
        f'$p = Get-CimInstance Win32_Process -Filter "ProcessId = {pid}"; '
        "if ($p) { Write-Output $p.Name; Write-Output $p.CommandLine }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "", ""

    if completed.returncode != 0:
        return "", ""

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return "", ""
    if len(lines) == 1:
        return lines[0], ""
    return lines[0], lines[1]


def _looks_like_localclaw_process(process_name: str, command_line: str) -> bool:
    name = (process_name or "").strip().lower()
    cmd = (command_line or "").strip().lower()

    if "run_server.py" in cmd:
        return True
    if "localclaw.channels.web" in cmd:
        return True
    if "localclaw" in cmd and ("python" in name or "uvicorn" in cmd):
        return True
    return False


def _stop_process_by_pid(pid: int) -> bool:
    if not _is_windows():
        return False

    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _wait_for_port_release(host: str, port: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.time() + max(0.2, timeout_seconds)
    while time.time() < deadline:
        if _is_port_available(host, port):
            return True
        time.sleep(0.2)
    return _is_port_available(host, port)


def _attempt_auto_stop_existing_server(host: str, port: int) -> Tuple[bool, str]:
    if not _is_windows():
        return False, "Automatic restart is currently supported on Windows only."

    owner_pids = _find_port_owner_pids(host, port)
    if not owner_pids:
        return False, f"Could not identify which process is using {host}:{port}."

    for pid in owner_pids:
        process_name, command_line = _get_process_details(pid)
        if not _looks_like_localclaw_process(process_name, command_line):
            summary = (command_line or process_name or "unknown process").strip()
            return (
                False,
                f"Port {host}:{port} is used by a non-LocalClaw process (PID {pid}): {summary}",
            )

        print(f"Found existing LocalClaw server PID {pid} ({process_name or 'unknown'}). Stopping it...")
        if not _stop_process_by_pid(pid):
            return False, f"Failed to stop existing LocalClaw server PID {pid}."

    if not _wait_for_port_release(host, port):
        return False, f"Port {host}:{port} is still occupied after attempting to stop the old process."
    return True, ""


def main() -> int:
    host = os.getenv("LOCALCLAW_SERVER_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    port = _get_port()
    auto_restart = _env_truthy("LOCALCLAW_AUTO_RESTART", default=True)

    print("Python starting...")
    print(f"Python version: {sys.version}")
    print("Importing localclaw...")
    from localclaw.channels.web import create_app  # noqa: F401 - import check for early failures
    print("Import successful!")

    if not _is_port_available(host, port):
        if auto_restart:
            ok, message = _attempt_auto_stop_existing_server(host, port)
            if ok:
                print("Previous LocalClaw server stopped. Continuing with restart...")
            else:
                print(message)
                print(f"Port {host}:{port} is already in use.")
                print(f"If LocalClaw is already running, open http://{host}:{port}/ in your browser.")
                print(f"To inspect/stop the process on Windows: netstat -ano | findstr :{port}")
                return 1
        else:
            print(f"Port {host}:{port} is already in use.")
            print(f"If LocalClaw is already running, open http://{host}:{port}/ in your browser.")
            print(f"To inspect/stop the process on Windows: netstat -ano | findstr :{port}")
            return 1

    import uvicorn

    print(f"Starting server on http://{host}:{port} ...")
    uvicorn.run(
        "localclaw.channels.web:create_app",
        host=host,
        port=port,
        factory=True,
        use_colors=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

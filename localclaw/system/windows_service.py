"""Manage LocalClaw as a Windows background service."""

from __future__ import annotations

import ctypes
import locale
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVICE_NAME = os.getenv("LOCALCLAW_WINDOWS_SERVICE_NAME", "LocalClaw")
DEFAULT_DISPLAY_NAME = "LocalClaw Runtime"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_admin() -> bool:
    if not _is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_sc(arguments: list[str], timeout: float = 8.0) -> Tuple[int, str]:
    """Execute sc.exe and return (returncode, merged output)."""

    def _decode_stream(payload: bytes) -> str:
        if not payload:
            return ""

        candidates = []
        preferred = locale.getpreferredencoding(False)
        if preferred:
            candidates.append(preferred)
        # `mbcs` follows the active Windows ANSI code page.
        candidates.extend(["mbcs", "utf-8", "cp936", "gbk"])

        used = set()
        for encoding_name in candidates:
            lowered = encoding_name.lower()
            if lowered in used:
                continue
            used.add(lowered)
            try:
                return payload.decode(encoding_name)
            except Exception:
                continue

        return payload.decode("utf-8", errors="replace")

    completed = subprocess.run(
        ["sc.exe", *arguments],
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
    )
    stdout = _decode_stream(completed.stdout).strip()
    stderr = _decode_stream(completed.stderr).strip()
    merged_output = "\n".join(part for part in [stdout, stderr] if part).strip()
    return completed.returncode, merged_output


def _build_runtime_command() -> Tuple[str, str, str]:
    python_executable = str(Path(sys.executable).resolve())
    script_path = str((PROJECT_ROOT / "run_server.py").resolve())
    command = f'"{python_executable}" "{script_path}"'
    return python_executable, script_path, command


def _service_not_found(output: str) -> bool:
    lowered = (output or "").lower()
    return "1060" in lowered or "does not exist" in lowered


def _parse_sc_state(output: str) -> str:
    match = re.search(r"STATE\s*:\s*\d+\s+([A-Z_]+)", output or "")
    return match.group(1).strip().upper() if match else "UNKNOWN"


def _parse_sc_qc(output: str) -> Dict[str, str]:
    startup_type = "UNKNOWN"
    binary_path = ""
    display_name = ""

    startup_match = re.search(r"START_TYPE\s*:\s*\d+\s+([A-Z_]+)", output or "")
    if startup_match:
        startup_type = startup_match.group(1).strip().upper()

    binary_match = re.search(r"BINARY_PATH_NAME\s*:\s*(.+)", output or "")
    if binary_match:
        binary_path = binary_match.group(1).strip()

    display_match = re.search(r"DISPLAY_NAME\s*:\s*(.+)", output or "")
    if display_match:
        display_name = display_match.group(1).strip()

    return {
        "startup_type": startup_type,
        "binary_path": binary_path,
        "display_name": display_name,
    }


def get_background_service_status(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Return LocalClaw Windows service status for UI rendering."""

    python_executable, script_path, command = _build_runtime_command()
    status: Dict[str, Any] = {
        "supported": _is_windows(),
        "platform": sys.platform,
        "service_name": service_name,
        "display_name": DEFAULT_DISPLAY_NAME,
        "installed": False,
        "state": "UNSUPPORTED",
        "running": False,
        "startup_type": "UNKNOWN",
        "binary_path": "",
        "can_manage": _is_admin(),
        "python_executable": python_executable,
        "script_path": script_path,
        "command": command,
        "message": "",
    }

    if not status["supported"]:
        status["message"] = "Background service management is currently available on Windows only."
        return status

    query_rc, query_output = _run_sc(["query", service_name])
    if query_rc != 0:
        if _service_not_found(query_output):
            status["state"] = "NOT_INSTALLED"
            status["message"] = "Service is not installed."
            return status

        status["state"] = "UNKNOWN"
        status["message"] = query_output or "Failed to query service status."
        return status

    state = _parse_sc_state(query_output)
    status["installed"] = True
    status["state"] = state
    status["running"] = state == "RUNNING"

    qc_rc, qc_output = _run_sc(["qc", service_name])
    if qc_rc == 0:
        parsed = _parse_sc_qc(qc_output)
        status.update(parsed)
        if parsed.get("display_name"):
            status["display_name"] = parsed["display_name"]
    else:
        status["message"] = qc_output or "Service queried, but details could not be loaded."

    return status


def _build_action_result(
    action: str,
    ok: bool,
    changed: bool,
    message: str,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> Dict[str, Any]:
    return {
        "ok": ok,
        "action": action,
        "changed": changed,
        "message": message,
        "status": get_background_service_status(service_name=service_name),
    }


def install_background_service(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Install LocalClaw as a Windows auto-start service."""

    status = get_background_service_status(service_name=service_name)
    if not status["supported"]:
        return _build_action_result("install", False, False, status["message"], service_name=service_name)
    if not status["can_manage"]:
        return _build_action_result(
            "install",
            False,
            False,
            "Administrator privileges are required to install a Windows service.",
            service_name=service_name,
        )

    if status["installed"]:
        config_rc, config_output = _run_sc(["config", service_name, "start=", "auto"])
        if config_rc != 0:
            return _build_action_result(
                "install",
                False,
                False,
                config_output or "Service is installed but startup mode could not be updated.",
                service_name=service_name,
            )
        return _build_action_result(
            "install",
            True,
            False,
            "Service is already installed. Startup mode set to AUTO.",
            service_name=service_name,
        )

    command = status["command"]
    create_rc, create_output = _run_sc(
        [
            "create",
            service_name,
            "binPath=",
            command,
            "start=",
            "auto",
            "DisplayName=",
            DEFAULT_DISPLAY_NAME,
        ]
    )
    if create_rc != 0:
        return _build_action_result(
            "install",
            False,
            False,
            create_output or "Failed to create Windows service.",
            service_name=service_name,
        )

    _run_sc(["description", service_name, "LocalClaw local runtime web service"], timeout=5.0)
    return _build_action_result(
        "install",
        True,
        True,
        "Service installed successfully.",
        service_name=service_name,
    )


def start_background_service(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Start the LocalClaw Windows service."""

    status = get_background_service_status(service_name=service_name)
    if not status["supported"]:
        return _build_action_result("start", False, False, status["message"], service_name=service_name)
    if not status["installed"]:
        return _build_action_result(
            "start",
            False,
            False,
            "Service is not installed yet.",
            service_name=service_name,
        )
    if not status["can_manage"]:
        return _build_action_result(
            "start",
            False,
            False,
            "Administrator privileges are required to start the service.",
            service_name=service_name,
        )
    if status["running"]:
        return _build_action_result(
            "start",
            True,
            False,
            "Service is already running.",
            service_name=service_name,
        )

    start_rc, start_output = _run_sc(["start", service_name], timeout=12.0)
    if start_rc != 0:
        return _build_action_result(
            "start",
            False,
            False,
            start_output or "Failed to start service.",
            service_name=service_name,
        )

    return _build_action_result("start", True, True, "Service started.", service_name=service_name)


def stop_background_service(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Stop the LocalClaw Windows service."""

    status = get_background_service_status(service_name=service_name)
    if not status["supported"]:
        return _build_action_result("stop", False, False, status["message"], service_name=service_name)
    if not status["installed"]:
        return _build_action_result(
            "stop",
            False,
            False,
            "Service is not installed.",
            service_name=service_name,
        )
    if not status["can_manage"]:
        return _build_action_result(
            "stop",
            False,
            False,
            "Administrator privileges are required to stop the service.",
            service_name=service_name,
        )
    if not status["running"]:
        return _build_action_result(
            "stop",
            True,
            False,
            "Service is already stopped.",
            service_name=service_name,
        )

    stop_rc, stop_output = _run_sc(["stop", service_name], timeout=12.0)
    if stop_rc != 0:
        return _build_action_result(
            "stop",
            False,
            False,
            stop_output or "Failed to stop service.",
            service_name=service_name,
        )

    return _build_action_result("stop", True, True, "Service stopped.", service_name=service_name)


def uninstall_background_service(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Remove the LocalClaw Windows service."""

    status = get_background_service_status(service_name=service_name)
    if not status["supported"]:
        return _build_action_result("uninstall", False, False, status["message"], service_name=service_name)
    if not status["installed"]:
        return _build_action_result(
            "uninstall",
            True,
            False,
            "Service is already not installed.",
            service_name=service_name,
        )
    if not status["can_manage"]:
        return _build_action_result(
            "uninstall",
            False,
            False,
            "Administrator privileges are required to uninstall the service.",
            service_name=service_name,
        )

    if status["running"]:
        stop_rc, stop_output = _run_sc(["stop", service_name], timeout=12.0)
        if stop_rc != 0 and "1062" not in (stop_output or ""):
            return _build_action_result(
                "uninstall",
                False,
                False,
                stop_output or "Failed to stop service before uninstall.",
                service_name=service_name,
            )

    delete_rc, delete_output = _run_sc(["delete", service_name], timeout=10.0)
    if delete_rc != 0:
        return _build_action_result(
            "uninstall",
            False,
            False,
            delete_output or "Failed to delete service.",
            service_name=service_name,
        )

    return _build_action_result(
        "uninstall",
        True,
        True,
        "Service uninstalled.",
        service_name=service_name,
    )


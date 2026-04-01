"""Manage LocalClaw background auto-start on Windows."""

from __future__ import annotations

import ctypes
import json
import locale
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PREFERRED_ENCODING: str = locale.getpreferredencoding(False) or ""
_DECODE_CANDIDATES: List[str] = list(
    dict.fromkeys(([_PREFERRED_ENCODING] if _PREFERRED_ENCODING else []) + ["mbcs", "utf-8", "cp936", "gbk"])
)
DEFAULT_SERVICE_NAME = os.getenv("LOCALCLAW_WINDOWS_SERVICE_NAME", "LocalClaw")
DEFAULT_DISPLAY_NAME = "LocalClaw Runtime"
_LEGACY_SERVICE_HINT = (
    "Legacy SCM service detected. Reinstall from Settings to migrate to the "
    "Task Scheduler auto-start mode. The old service wrapper can fail with "
    "Windows error 1053 because LocalClaw runs as a regular Python process."
)


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_admin() -> bool:
    if not _is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _decode_stream(payload: bytes) -> str:
    """Decode Windows command output using the active locale when possible."""

    if not payload:
        return ""

    for encoding_name in _DECODE_CANDIDATES:
        try:
            return payload.decode(encoding_name)
        except (UnicodeDecodeError, LookupError):
            continue

    return payload.decode("utf-8", errors="replace")


def _run_command(executable: str, arguments: list[str], timeout: float = 8.0) -> Tuple[int, str]:
    """Execute a Windows helper command and return (returncode, merged output)."""

    completed = subprocess.run(
        [executable, *arguments],
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
    )
    stdout = _decode_stream(completed.stdout).strip()
    stderr = _decode_stream(completed.stderr).strip()
    merged_output = "\n".join(part for part in [stdout, stderr] if part).strip()
    return completed.returncode, merged_output


def _run_sc(arguments: list[str], timeout: float = 8.0) -> Tuple[int, str]:
    """Execute sc.exe and return (returncode, merged output)."""

    return _run_command("sc.exe", arguments, timeout=timeout)


def _run_powershell(script: str, timeout: float = 12.0) -> Tuple[int, str]:
    """Execute a PowerShell script block and return merged output."""

    return _run_command(
        "powershell.exe",
        [
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        timeout=timeout,
    )


def _build_runtime_command() -> Tuple[str, str, str]:
    python_executable = str(Path(sys.executable).resolve())
    script_path = str((PROJECT_ROOT / "run_server.py").resolve())
    command = f'"{python_executable}" "{script_path}"'
    return python_executable, script_path, command


def _ps_literal(value: str) -> str:
    """Render a safe PowerShell single-quoted literal."""

    return "'" + str(value).replace("'", "''") + "'"


def _service_not_found(output: str) -> bool:
    lowered = (output or "").lower()
    return "1060" in lowered or "does not exist" in lowered


def _scheduled_task_not_found(output: str) -> bool:
    lowered = (output or "").lower()
    return (
        "cannot find" in lowered
        or "no msft_scheduledtask" in lowered
        or "not found" in lowered
        or "\u627e\u4e0d\u5230" in output
        or ("msft_scheduledtask" in lowered and "taskname" in lowered)
    )


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


def _query_scheduled_task(task_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return Task Scheduler metadata for the given task, if present."""

    script = f"""
    try {{
        $task = Get-ScheduledTask -TaskName {_ps_literal(task_name)} -ErrorAction Stop
        $info = $task | Get-ScheduledTaskInfo
        $action = $task.Actions | Select-Object -First 1
        [pscustomobject]@{{
            task_name = $task.TaskName
            state = [string]$task.State
            last_task_result = [int]$info.LastTaskResult
            last_run_time = if ($info.LastRunTime) {{ $info.LastRunTime.ToString('o') }} else {{ '' }}
            next_run_time = if ($info.NextRunTime) {{ $info.NextRunTime.ToString('o') }} else {{ '' }}
            execute = if ($action) {{ [string]$action.Execute }} else {{ '' }}
            arguments = if ($action) {{ [string]$action.Arguments }} else {{ '' }}
            working_directory = if ($action) {{ [string]$action.WorkingDirectory }} else {{ '' }}
            user_id = [string]$task.Principal.UserId
            run_level = [string]$task.Principal.RunLevel
            trigger_kinds = @($task.Triggers | ForEach-Object {{ $_.CimClass.CimClassName }})
        }} | ConvertTo-Json -Compress -Depth 4
    }} catch {{
        Write-Output ("__LOCALCLAW_ERROR__:" + $_.Exception.Message)
        exit 2
    }}
    """
    rc, output = _run_powershell(script, timeout=12.0)
    if rc != 0:
        if output.startswith("__LOCALCLAW_ERROR__:"):
            output = output.split(":", 1)[1].strip()
        return None, output
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None, output or "Failed to decode scheduled-task status."
    return payload, None


def _build_task_binary_path(task_info: Dict[str, Any], fallback: str) -> str:
    execute = str(task_info.get("execute") or "").strip()
    arguments = str(task_info.get("arguments") or "").strip()
    if execute and arguments:
        return f'{execute} {arguments}'.strip()
    return execute or fallback


def _normalize_task_state(state: str) -> Tuple[str, bool]:
    normalized = str(state or "").strip().lower()
    if normalized == "running":
        return "RUNNING", True
    if normalized in {"ready", "queued"}:
        return "STOPPED", False
    if normalized == "disabled":
        return "DISABLED", False
    if normalized == "unknown":
        return "UNKNOWN", False
    return (normalized.upper() or "UNKNOWN"), False


def _scheduled_task_startup_type(task_info: Dict[str, Any]) -> str:
    trigger_kinds = [str(item or "") for item in (task_info.get("trigger_kinds") or [])]
    if any("BootTrigger" in item for item in trigger_kinds):
        return "AUTO_START"
    return "MANUAL"


def _build_task_status(
    task_name: str,
    task_info: Dict[str, Any],
    python_executable: str,
    script_path: str,
    command: str,
) -> Dict[str, Any]:
    state, running = _normalize_task_state(task_info.get("state", ""))
    last_task_result = int(task_info.get("last_task_result", 0) or 0)
    message = ""
    if state == "UNKNOWN" and last_task_result:
        message = f"Task Scheduler reports last run result 0x{last_task_result & 0xFFFFFFFF:08X}."

    return {
        "supported": True,
        "platform": sys.platform,
        "service_name": task_name,
        "display_name": DEFAULT_DISPLAY_NAME,
        "installed": True,
        "state": state,
        "running": running,
        "startup_type": _scheduled_task_startup_type(task_info),
        "binary_path": _build_task_binary_path(task_info, command),
        "can_manage": _is_admin(),
        "python_executable": python_executable,
        "script_path": script_path,
        "command": command,
        "message": message,
    }


def _query_legacy_service_status(
    service_name: str,
    python_executable: str,
    script_path: str,
    command: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return legacy SCM service status, if present."""

    query_rc, query_output = _run_sc(["query", service_name])
    if query_rc != 0:
        if _service_not_found(query_output):
            return None, None
        return None, query_output or "Failed to query service status."

    state = _parse_sc_state(query_output)
    status: Dict[str, Any] = {
        "supported": True,
        "platform": sys.platform,
        "service_name": service_name,
        "display_name": DEFAULT_DISPLAY_NAME,
        "installed": True,
        "state": state,
        "running": state == "RUNNING",
        "startup_type": "UNKNOWN",
        "binary_path": command,
        "can_manage": _is_admin(),
        "python_executable": python_executable,
        "script_path": script_path,
        "command": command,
        "message": _LEGACY_SERVICE_HINT,
    }

    qc_rc, qc_output = _run_sc(["qc", service_name])
    if qc_rc == 0:
        parsed = _parse_sc_qc(qc_output)
        status.update(parsed)
        if parsed.get("display_name"):
            status["display_name"] = parsed["display_name"]
    elif qc_output:
        status["message"] = f"{_LEGACY_SERVICE_HINT}\n\n{qc_output}"

    return status, None


def get_background_service_status(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Return LocalClaw Windows auto-start status for UI rendering."""

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

    task_info, task_error = _query_scheduled_task(service_name)
    if task_info is not None:
        return _build_task_status(service_name, task_info, python_executable, script_path, command)
    if task_error and not _scheduled_task_not_found(task_error):
        status["state"] = "UNKNOWN"
        status["message"] = task_error
        return status

    legacy_status, legacy_error = _query_legacy_service_status(
        service_name,
        python_executable,
        script_path,
        command,
    )
    if legacy_status is not None:
        return legacy_status
    if legacy_error:
        status["state"] = "UNKNOWN"
        status["message"] = legacy_error
        return status

    status["state"] = "NOT_INSTALLED"
    status["message"] = "Background auto-start is not installed."
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
    """Install LocalClaw as a Windows auto-start scheduled task."""

    status = get_background_service_status(service_name=service_name)
    if not status["supported"]:
        return _build_action_result("install", False, False, status["message"], service_name=service_name)
    if not status["can_manage"]:
        return _build_action_result(
            "install",
            False,
            False,
            "Administrator privileges are required to install auto-start.",
            service_name=service_name,
        )

    task_info, task_error = _query_scheduled_task(service_name)
    if task_info is not None:
        return _build_action_result(
            "install",
            True,
            False,
            "Task Scheduler auto-start is already installed.",
            service_name=service_name,
        )
    if task_error and not _scheduled_task_not_found(task_error):
        return _build_action_result("install", False, False, task_error, service_name=service_name)

    python_executable, script_path, _ = _build_runtime_command()
    register_script = f"""
    try {{
        $action = New-ScheduledTaskAction -Execute {_ps_literal(python_executable)} -Argument {_ps_literal(f'"{script_path}"')} -WorkingDirectory {_ps_literal(str(PROJECT_ROOT))}
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
        Register-ScheduledTask -TaskName {_ps_literal(service_name)} -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description {_ps_literal('LocalClaw local runtime web service')} -Force | Out-Null
    }} catch {{
        Write-Output ("__LOCALCLAW_ERROR__:" + $_.Exception.Message)
        exit 2
    }}
    """
    register_rc, register_output = _run_powershell(register_script, timeout=20.0)
    if register_rc != 0:
        return _build_action_result(
            "install",
            False,
            False,
            register_output or "Failed to register the Windows startup task.",
            service_name=service_name,
        )

    legacy_status, _ = _query_legacy_service_status(service_name, python_executable, script_path, status["command"])
    migrated = False
    if legacy_status is not None:
        delete_rc, _ = _run_sc(["delete", service_name], timeout=10.0)
        migrated = delete_rc == 0

    return _build_action_result(
        "install",
        True,
        True,
        (
            "Task Scheduler auto-start installed successfully. Legacy service entry was removed."
            if migrated
            else "Task Scheduler auto-start installed successfully."
        ),
        service_name=service_name,
    )


def start_background_service(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Start the LocalClaw background task."""

    status = get_background_service_status(service_name=service_name)
    if not status["supported"]:
        return _build_action_result("start", False, False, status["message"], service_name=service_name)
    if not status["installed"]:
        return _build_action_result(
            "start",
            False,
            False,
            "Background auto-start is not installed yet.",
            service_name=service_name,
        )
    if not status["can_manage"]:
        return _build_action_result(
            "start",
            False,
            False,
            "Administrator privileges are required to start the background task.",
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

    task_info, task_error = _query_scheduled_task(service_name)
    if task_info is not None:
        start_script = f"""
        try {{
            Start-ScheduledTask -TaskName {_ps_literal(service_name)} -ErrorAction Stop
        }} catch {{
        Write-Output ("__LOCALCLAW_ERROR__:" + $_.Exception.Message)
        exit 2
    }}
    """
        start_rc, start_output = _run_powershell(start_script, timeout=12.0)
        if start_rc != 0:
            return _build_action_result(
                "start",
                False,
                False,
                start_output or "Failed to start the background task.",
                service_name=service_name,
            )
        return _build_action_result(
            "start",
            True,
            True,
            "Background task started.",
            service_name=service_name,
        )

    if task_error and not _scheduled_task_not_found(task_error):
        return _build_action_result(
            "start",
            False,
            False,
            task_error,
            service_name=service_name,
        )

    start_rc, start_output = _run_sc(["start", service_name], timeout=12.0)
    if start_rc != 0:
        if "1053" in str(start_output or ""):
            return _build_action_result(
                "start",
                False,
                False,
                _LEGACY_SERVICE_HINT,
                service_name=service_name,
            )
        return _build_action_result(
            "start",
            False,
            False,
            start_output or "Failed to start legacy service.",
            service_name=service_name,
        )

    return _build_action_result("start", True, True, "Legacy service started.", service_name=service_name)


def stop_background_service(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Stop the LocalClaw background task."""

    status = get_background_service_status(service_name=service_name)
    if not status["supported"]:
        return _build_action_result("stop", False, False, status["message"], service_name=service_name)
    if not status["installed"]:
        return _build_action_result(
            "stop",
            False,
            False,
            "Background auto-start is not installed.",
            service_name=service_name,
        )
    if not status["can_manage"]:
        return _build_action_result(
            "stop",
            False,
            False,
            "Administrator privileges are required to stop the background task.",
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

    task_info, task_error = _query_scheduled_task(service_name)
    if task_info is not None:
        stop_script = f"""
        try {{
            Stop-ScheduledTask -TaskName {_ps_literal(service_name)} -ErrorAction Stop
        }} catch {{
            Write-Output ("__LOCALCLAW_ERROR__:" + $_.Exception.Message)
            exit 2
        }}
        """
        stop_rc, stop_output = _run_powershell(stop_script, timeout=12.0)
        if stop_rc != 0:
            return _build_action_result(
                "stop",
                False,
                False,
                stop_output or "Failed to stop the background task.",
                service_name=service_name,
            )
        return _build_action_result("stop", True, True, "Background task stopped.", service_name=service_name)

    if task_error and not _scheduled_task_not_found(task_error):
        return _build_action_result(
            "stop",
            False,
            False,
            task_error,
            service_name=service_name,
        )

    stop_rc, stop_output = _run_sc(["stop", service_name], timeout=12.0)
    if stop_rc != 0:
        return _build_action_result(
            "stop",
            False,
            False,
            stop_output or "Failed to stop legacy service.",
            service_name=service_name,
        )

    return _build_action_result("stop", True, True, "Legacy service stopped.", service_name=service_name)


def uninstall_background_service(service_name: str = DEFAULT_SERVICE_NAME) -> Dict[str, Any]:
    """Remove LocalClaw background auto-start."""

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
            "Administrator privileges are required to uninstall auto-start.",
            service_name=service_name,
        )

    task_info, task_error = _query_scheduled_task(service_name)
    task_removed = False
    legacy_removed = False

    if task_info is not None:
        if status["running"]:
            stop_script = f"""
            try {{
                Stop-ScheduledTask -TaskName {_ps_literal(service_name)} -ErrorAction SilentlyContinue
            }} catch {{
            }}
            """
            _run_powershell(stop_script, timeout=10.0)

        unregister_script = f"""
        try {{
            Unregister-ScheduledTask -TaskName {_ps_literal(service_name)} -Confirm:$false -ErrorAction Stop
        }} catch {{
            Write-Output ("__LOCALCLAW_ERROR__:" + $_.Exception.Message)
            exit 2
        }}
        """
        unregister_rc, unregister_output = _run_powershell(unregister_script, timeout=12.0)
        if unregister_rc != 0:
            return _build_action_result(
                "uninstall",
                False,
                False,
                unregister_output or "Failed to remove the background task.",
                service_name=service_name,
            )
        task_removed = True
    elif task_error and not _scheduled_task_not_found(task_error):
        return _build_action_result(
            "uninstall",
            False,
            False,
            task_error,
            service_name=service_name,
        )

    legacy_status, legacy_error = _query_legacy_service_status(
        service_name,
        status["python_executable"],
        status["script_path"],
        status["command"],
    )
    if legacy_status is not None:
        if legacy_status["running"]:
            stop_rc, stop_output = _run_sc(["stop", service_name], timeout=12.0)
            if stop_rc != 0 and "1062" not in (stop_output or ""):
                return _build_action_result(
                    "uninstall",
                    False,
                    False,
                    stop_output or "Failed to stop legacy service before uninstall.",
                    service_name=service_name,
                )

        delete_rc, delete_output = _run_sc(["delete", service_name], timeout=10.0)
        if delete_rc != 0:
            return _build_action_result(
                "uninstall",
                False,
                False,
                delete_output or "Failed to delete legacy service.",
                service_name=service_name,
            )
        legacy_removed = True
    elif legacy_error:
        return _build_action_result("uninstall", False, False, legacy_error, service_name=service_name)

    return _build_action_result(
        "uninstall",
        True,
        task_removed or legacy_removed,
        (
            "Background auto-start removed."
            if task_removed or legacy_removed
            else "Background auto-start was already not installed."
        ),
        service_name=service_name,
    )

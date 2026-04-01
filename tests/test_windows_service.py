"""Tests for Windows background auto-start helpers."""

from localclaw.system import windows_service


def test_decode_stream_uses_gbk_fallback(monkeypatch):
    """GBK output should still decode when UTF-8 is not the active locale."""

    monkeypatch.setattr(windows_service, "_DECODE_CANDIDATES", ["utf-8", "gbk"])

    expected = "\u4e2d\u6587"
    assert windows_service._decode_stream(expected.encode("gbk")) == expected


def test_decode_stream_falls_back_to_utf8_replace(monkeypatch):
    """Invalid bytes should still produce a readable string via replacement."""

    monkeypatch.setattr(windows_service, "_DECODE_CANDIDATES", ["definitely-not-a-codec"])

    assert windows_service._decode_stream(b"\xff") == "\ufffd"


def test_status_reports_not_installed_when_task_and_service_are_missing(monkeypatch):
    """Missing scheduled task and legacy service should map to NOT_INSTALLED."""

    monkeypatch.setattr(windows_service, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_service, "_is_admin", lambda: True)
    monkeypatch.setattr(
        windows_service,
        "_build_runtime_command",
        lambda: ("C:/Python/python.exe", "G:/LocalClaw/run_server.py", '"C:/Python/python.exe" "G:/LocalClaw/run_server.py"'),
    )
    monkeypatch.setattr(windows_service, "_query_scheduled_task", lambda name: (None, "not found"))
    monkeypatch.setattr(
        windows_service,
        "_query_legacy_service_status",
        lambda service_name, python_executable, script_path, command: (None, None),
    )

    status = windows_service.get_background_service_status(service_name="LocalClaw")

    assert status["supported"] is True
    assert status["installed"] is False
    assert status["state"] == "NOT_INSTALLED"


def test_status_prefers_scheduled_task_details(monkeypatch):
    """Scheduled task metadata should populate running/startup/path fields."""

    monkeypatch.setattr(windows_service, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_service, "_is_admin", lambda: True)
    monkeypatch.setattr(
        windows_service,
        "_build_runtime_command",
        lambda: ("C:/Python/python.exe", "G:/LocalClaw/run_server.py", '"C:/Python/python.exe" "G:/LocalClaw/run_server.py"'),
    )

    monkeypatch.setattr(
        windows_service,
        "_query_scheduled_task",
        lambda name: (
            {
                "task_name": name,
                "state": "Running",
                "last_task_result": 0,
                "execute": "C:/Python/python.exe",
                "arguments": '"G:/LocalClaw/run_server.py"',
                "working_directory": "G:/LocalClaw",
                "trigger_kinds": ["MSFT_TaskBootTrigger"],
            },
            None,
        ),
    )

    status = windows_service.get_background_service_status(service_name="LocalClaw")

    assert status["installed"] is True
    assert status["running"] is True
    assert status["state"] == "RUNNING"
    assert status["startup_type"] == "AUTO_START"
    assert "run_server.py" in status["binary_path"]


def test_install_requires_admin_permissions(monkeypatch):
    """Install action should fail with a helpful message when not elevated."""

    monkeypatch.setattr(
        windows_service,
        "get_background_service_status",
        lambda service_name=windows_service.DEFAULT_SERVICE_NAME: {
            "supported": True,
            "platform": "win32",
            "service_name": service_name,
            "display_name": "LocalClaw Runtime",
            "installed": False,
            "state": "NOT_INSTALLED",
            "running": False,
            "startup_type": "UNKNOWN",
            "binary_path": "",
            "can_manage": False,
            "python_executable": "C:/Python/python.exe",
            "script_path": "G:/LocalClaw/run_server.py",
            "command": '"C:/Python/python.exe" "G:/LocalClaw/run_server.py"',
            "message": "",
        },
    )

    result = windows_service.install_background_service(service_name="LocalClaw")

    assert result["ok"] is False
    assert "Administrator" in result["message"]


def test_install_registers_scheduled_task_when_missing(monkeypatch):
    """Install action should register the scheduled task and return changed=True."""

    ps_scripts = []
    query_count = {"value": 0}

    def fake_status(service_name=windows_service.DEFAULT_SERVICE_NAME):
        query_count["value"] += 1
        installed = query_count["value"] > 1
        return {
            "supported": True,
            "platform": "win32",
            "service_name": service_name,
            "display_name": "LocalClaw Runtime",
            "installed": installed,
            "state": "STOPPED" if installed else "NOT_INSTALLED",
            "running": False,
            "startup_type": "AUTO_START" if installed else "UNKNOWN",
            "binary_path": "",
            "can_manage": True,
            "python_executable": "C:/Python/python.exe",
            "script_path": "G:/LocalClaw/run_server.py",
            "command": '"C:/Python/python.exe" "G:/LocalClaw/run_server.py"',
            "message": "",
        }

    monkeypatch.setattr(windows_service, "get_background_service_status", fake_status)
    monkeypatch.setattr(windows_service, "_query_scheduled_task", lambda name: (None, "not found"))
    monkeypatch.setattr(
        windows_service,
        "_query_legacy_service_status",
        lambda service_name, python_executable, script_path, command: (None, None),
    )
    monkeypatch.setattr(
        windows_service,
        "_run_powershell",
        lambda script, timeout=12.0: (ps_scripts.append(script) or 0, ""),
    )

    result = windows_service.install_background_service(service_name="LocalClaw")

    assert result["ok"] is True
    assert result["changed"] is True
    assert ps_scripts
    assert "Register-ScheduledTask" in ps_scripts[0]
    assert "LocalClaw" in ps_scripts[0]


def test_start_legacy_service_1053_returns_migration_hint(monkeypatch):
    """Legacy service start failures should explain that this is not a winsock problem."""

    monkeypatch.setattr(
        windows_service,
        "get_background_service_status",
        lambda service_name=windows_service.DEFAULT_SERVICE_NAME: {
            "supported": True,
            "platform": "win32",
            "service_name": service_name,
            "display_name": "LocalClaw Runtime",
            "installed": True,
            "state": "STOPPED",
            "running": False,
            "startup_type": "AUTO_START",
            "binary_path": '"C:/Python/python.exe" "G:/LocalClaw/run_server.py"',
            "can_manage": True,
            "python_executable": "C:/Python/python.exe",
            "script_path": "G:/LocalClaw/run_server.py",
            "command": '"C:/Python/python.exe" "G:/LocalClaw/run_server.py"',
            "message": windows_service._LEGACY_SERVICE_HINT,
        },
    )
    monkeypatch.setattr(windows_service, "_query_scheduled_task", lambda name: (None, "not found"))
    monkeypatch.setattr(
        windows_service,
        "_run_sc",
        lambda args, timeout=8.0: (1, "StartService FAILED 1053"),
    )

    result = windows_service.start_background_service(service_name="LocalClaw")

    assert result["ok"] is False
    assert "1053" in result["message"] or "Task Scheduler" in result["message"]

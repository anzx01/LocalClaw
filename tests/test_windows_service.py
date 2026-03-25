"""Tests for Windows background service helpers."""

from localclaw.system import windows_service


def test_status_reports_not_installed_when_service_missing(monkeypatch):
    """Query code 1060 should map to a clean NOT_INSTALLED status."""

    monkeypatch.setattr(windows_service, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_service, "_is_admin", lambda: True)
    monkeypatch.setattr(
        windows_service,
        "_build_runtime_command",
        lambda: ("C:/Python/python.exe", "G:/LocalClaw/run_server.py", '"C:/Python/python.exe" "G:/LocalClaw/run_server.py"'),
    )
    monkeypatch.setattr(windows_service, "_run_sc", lambda args, timeout=8.0: (1060, "FAILED 1060"))

    status = windows_service.get_background_service_status(service_name="LocalClaw")

    assert status["supported"] is True
    assert status["installed"] is False
    assert status["state"] == "NOT_INSTALLED"


def test_status_parses_running_service_details(monkeypatch):
    """Running service output should populate state/startup/path metadata."""

    monkeypatch.setattr(windows_service, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_service, "_is_admin", lambda: True)
    monkeypatch.setattr(
        windows_service,
        "_build_runtime_command",
        lambda: ("C:/Python/python.exe", "G:/LocalClaw/run_server.py", '"C:/Python/python.exe" "G:/LocalClaw/run_server.py"'),
    )

    def fake_run_sc(args, timeout=8.0):
        if args[0] == "query":
            return 0, "STATE              : 4  RUNNING"
        if args[0] == "qc":
            return 0, "\n".join(
                [
                    "START_TYPE         : 2   AUTO_START",
                    'BINARY_PATH_NAME   : "C:/Python/python.exe" "G:/LocalClaw/run_server.py"',
                    "DISPLAY_NAME       : LocalClaw Runtime",
                ]
            )
        return 0, ""

    monkeypatch.setattr(windows_service, "_run_sc", fake_run_sc)

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


def test_install_creates_service_when_missing(monkeypatch):
    """Install action should call sc create and return changed=True."""

    calls = []
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

    def fake_run_sc(args, timeout=8.0):
        calls.append(args)
        return 0, "ok"

    monkeypatch.setattr(windows_service, "get_background_service_status", fake_status)
    monkeypatch.setattr(windows_service, "_run_sc", fake_run_sc)

    result = windows_service.install_background_service(service_name="LocalClaw")

    assert result["ok"] is True
    assert result["changed"] is True
    assert any(call and call[0] == "create" for call in calls)


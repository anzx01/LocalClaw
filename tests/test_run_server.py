"""Tests for run_server auto-restart behavior."""

from __future__ import annotations

import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_RUN_SERVER_PATH = Path(__file__).resolve().parents[1] / "run_server.py"
_RUN_SERVER_SPEC = spec_from_file_location("run_server_under_test", _RUN_SERVER_PATH)
assert _RUN_SERVER_SPEC and _RUN_SERVER_SPEC.loader
run_server = module_from_spec(_RUN_SERVER_SPEC)
_RUN_SERVER_SPEC.loader.exec_module(run_server)


def test_looks_like_localclaw_process_by_script_name():
    assert run_server._looks_like_localclaw_process(
        "python.exe",
        'C:\\Python\\python.exe "G:\\myaist\\LocalClaw\\run_server.py"',
    )


def test_looks_like_localclaw_process_rejects_other_python_apps():
    assert not run_server._looks_like_localclaw_process(
        "python.exe",
        'C:\\Python\\python.exe "D:\\other\\app.py"',
    )


def test_find_port_owner_pids_parses_netstat(monkeypatch):
    sample = (
        "  TCP    127.0.0.1:8016         0.0.0.0:0              LISTENING       2416\n"
        "  TCP    127.0.0.1:9000         0.0.0.0:0              LISTENING       2222\n"
    )

    class Completed:
        returncode = 0
        stdout = sample

    monkeypatch.setattr(run_server, "_is_windows", lambda: True)
    monkeypatch.setattr(run_server.subprocess, "run", lambda *args, **kwargs: Completed())

    pids = run_server._find_port_owner_pids("127.0.0.1", 8016)
    assert pids == [2416]


def test_attempt_auto_stop_existing_server_stops_localclaw_pid(monkeypatch):
    stops: list[int] = []

    monkeypatch.setattr(run_server, "_is_windows", lambda: True)
    monkeypatch.setattr(run_server, "_find_port_owner_pids", lambda host, port: [2416])
    monkeypatch.setattr(
        run_server,
        "_get_process_details",
        lambda pid: ("python.exe", 'python.exe "G:\\myaist\\LocalClaw\\run_server.py"'),
    )
    monkeypatch.setattr(run_server, "_stop_process_by_pid", lambda pid: stops.append(pid) or True)
    monkeypatch.setattr(run_server, "_wait_for_port_release", lambda host, port: True)

    ok, message = run_server._attempt_auto_stop_existing_server("127.0.0.1", 8016)
    assert ok is True
    assert message == ""
    assert stops == [2416]


def test_attempt_auto_stop_existing_server_rejects_non_localclaw(monkeypatch):
    monkeypatch.setattr(run_server, "_is_windows", lambda: True)
    monkeypatch.setattr(run_server, "_find_port_owner_pids", lambda host, port: [9876])
    monkeypatch.setattr(
        run_server,
        "_get_process_details",
        lambda pid: ("python.exe", 'python.exe "D:\\other\\app.py"'),
    )
    monkeypatch.setattr(run_server, "_stop_process_by_pid", lambda pid: True)

    ok, message = run_server._attempt_auto_stop_existing_server("127.0.0.1", 8016)
    assert ok is False
    assert "non-LocalClaw process" in message


def test_main_continues_after_auto_restart(monkeypatch):
    availability_checks = iter([False, True])
    called = {"uvicorn": False}

    fake_web_module = types.SimpleNamespace(create_app=lambda: None)
    fake_uvicorn_module = types.SimpleNamespace(
        run=lambda *args, **kwargs: called.__setitem__("uvicorn", True)
    )

    monkeypatch.setattr(run_server, "_is_port_available", lambda host, port: next(availability_checks))
    monkeypatch.setattr(run_server, "_attempt_auto_stop_existing_server", lambda host, port: (True, ""))
    monkeypatch.setattr(run_server, "_get_port", lambda: 8016)
    monkeypatch.setenv("LOCALCLAW_AUTO_RESTART", "true")
    monkeypatch.setitem(__import__("sys").modules, "localclaw.channels.web", fake_web_module)
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn_module)

    exit_code = run_server.main()
    assert exit_code == 0
    assert called["uvicorn"] is True

"""Tests for the launch_app tool."""

import platform

import pytest

from localclaw.tools.launch_app_tool import LaunchAppTool


@pytest.mark.asyncio
async def test_launch_app_resolves_windows_alias(monkeypatch):
    """The launcher should resolve a common Windows alias through PATH lookup."""

    launched = {}

    class DummyProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr("localclaw.tools.launch_app_tool.shutil.which", lambda name: "C:/Tools/Code.exe" if name.lower() in {"code.exe", "code"} else None)
    monkeypatch.setattr("localclaw.tools.launch_app_tool.subprocess.Popen", fake_popen)

    tool = LaunchAppTool()
    result = await tool.run(target="vscode")

    assert result.status == "success"
    assert result.data["resolved_target"] == "C:/Tools/Code.exe"
    assert launched["command"][:4] == ["cmd", "/c", "start", ""]
    assert launched["command"][4] == "C:/Tools/Code.exe"


@pytest.mark.asyncio
async def test_launch_app_supports_url_targets(monkeypatch):
    """URL targets should be launched directly via platform opener."""

    launched = {}

    class DummyProcess:
        pid = 999

    def fake_popen(command, **kwargs):
        launched["command"] = command
        return DummyProcess()

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr("localclaw.tools.launch_app_tool.subprocess.Popen", fake_popen)

    tool = LaunchAppTool()
    result = await tool.run(target="https://example.com")

    assert result.status == "success"
    assert result.data["resolved_target"] == "https://example.com"
    assert launched["command"] == ["cmd", "/c", "start", "", "https://example.com"]


@pytest.mark.asyncio
async def test_launch_app_requires_target():
    """Missing target should fail input validation."""

    tool = LaunchAppTool()
    result = await tool.run()

    assert result.status == "error"
    assert "Missing required parameter: target" in result.message

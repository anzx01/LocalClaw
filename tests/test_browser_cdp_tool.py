"""Tests for the LocalClaw browser CDP integration."""

import asyncio
from pathlib import Path

from localclaw.skills.loader import SkillLoader
from localclaw.skills.security_review import review_skill_installation
from localclaw.tools.browser_cdp_tool import BrowserCDPTool


def test_bundled_web_access_skill_loads_as_localclaw_workflow(monkeypatch):
    """The adapted web-access bundle should load as a normal LocalClaw skill."""

    monkeypatch.setattr("localclaw.skills.loader.shutil.which", lambda name: "C:/node.exe" if name == "node" else None)

    skill_path = Path("bundled_skills") / "web-access"
    skill = SkillLoader().load_from_file(skill_path)

    assert skill is not None
    definition = skill.get_definition()
    assert definition.name == "web-access"
    assert "browser_cdp" in definition.tools
    assert definition.metadata["skill_key"] == "web-access"


def test_security_review_flags_browser_cdp_capability():
    """Third-party skills using browser_cdp should be flagged as browser automation."""

    report = review_skill_installation(
        skill_id="web-access",
        detail={"name": "web-access"},
        bundle={
            "name": "web-access",
            "description": "Use a logged-in Chrome session for dynamic web tasks.",
            "tools": ["browser_cdp"],
            "actions": [{"type": "tool_call", "tool": "browser_cdp", "params": {"request": "{{request}}"}}],
        },
    )

    finding_keys = {finding["key"] for finding in report["findings"]}
    assert "browser_control_capability" in finding_keys
    assert "network_request_capability" in finding_keys
    assert report["checks"]["browser_control_detected"] is True


def test_browser_cdp_returns_actionable_message_when_chrome_is_not_ready(monkeypatch):
    """Agent mode should stop with a concrete Chrome setup hint when the proxy is disconnected."""

    class FakeProvider:
        async def is_available(self):
            return True

    monkeypatch.setattr("localclaw.tools.browser_cdp_tool.get_llm_provider", lambda: FakeProvider())

    tool = BrowserCDPTool()

    async def fake_ready(*, start_proxy: bool):
        assert start_proxy is True
        return {
            "ok": True,
            "connected": False,
            "started_proxy": False,
            "message": "Chrome 远程调试尚未连接。",
        }

    monkeypatch.setattr(tool, "_ensure_proxy_ready", fake_ready)

    result = asyncio.run(tool.run(request="帮我看看 https://example.com"))

    assert result.status == "error"
    assert "Chrome 远程调试" in result.message

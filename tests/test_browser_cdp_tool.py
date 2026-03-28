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
    """Agent mode should return an actionable hint when the proxy is disconnected."""

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
            "message": "Chrome remote debugging is not connected.",
        }

    monkeypatch.setattr(tool, "_ensure_proxy_ready", fake_ready)

    result = asyncio.run(tool.run(request="Please inspect https://example.com"))

    assert result.status == "success"
    assert "remote debugging" in result.message
    assert result.data["needs_user_action"] is True
    assert result.data["action_required"] == "enable_chrome_remote_debugging"


def test_browser_cdp_safe_health_accepts_status_ok_payload(monkeypatch):
    """Proxy health payloads using {status: ok} should be treated as healthy."""

    async def fake_health(self):
        return {"status": "ok", "connected": None, "chromePort": 9222}

    monkeypatch.setattr("localclaw.tools.browser_cdp_tool._BrowserProxyClient.health", fake_health)

    tool = BrowserCDPTool()
    health = asyncio.run(tool._safe_health())

    assert health["ok"] is True
    assert health["connected"] is True


def test_browser_cdp_safe_health_handles_disconnected_status_ok(monkeypatch):
    """When proxy reports status ok but no port/session, it should be healthy but disconnected."""

    async def fake_health(self):
        return {"status": "ok", "connected": None, "chromePort": None}

    monkeypatch.setattr("localclaw.tools.browser_cdp_tool._BrowserProxyClient.health", fake_health)

    tool = BrowserCDPTool()
    health = asyncio.run(tool._safe_health())

    assert health["ok"] is True
    assert health["connected"] is False


def test_browser_cdp_ensure_proxy_ready_probes_connection_when_health_is_idle(monkeypatch):
    """Proxy health may be idle; ensure_proxy_ready should probe /targets before failing."""

    tool = BrowserCDPTool()

    async def fake_safe_health():
        return {"ok": True, "connected": False}

    async def fake_probe():
        return True

    monkeypatch.setattr(tool, "_safe_health", fake_safe_health)
    monkeypatch.setattr(tool, "_probe_proxy_connection", fake_probe)

    readiness = asyncio.run(tool._ensure_proxy_ready(start_proxy=True))

    assert readiness["ok"] is True
    assert readiness["connected"] is True


def test_browser_cdp_agent_bootstraps_tab_when_model_omits_target():
    """Agent mode should auto-open a seed tab instead of failing with missing target."""

    class FakeProxy:
        def __init__(self):
            self.new_tab_calls = []
            self.info_calls = []

        async def new_tab(self, url):
            self.new_tab_calls.append(url)
            return {"targetId": "seed-tab"}

        async def info(self, target):
            self.info_calls.append(target)
            return {"targetId": target, "url": self.new_tab_calls[-1] if self.new_tab_calls else ""}

    tool = BrowserCDPTool()
    proxy = FakeProxy()
    created_targets = []

    step = asyncio.run(
        tool._run_agent_action(
            proxy=proxy,
            action_plan={"action": "info"},
            created_targets=created_targets,
            current_target=None,
            request="今天周几？",
        )
    )

    assert created_targets == ["seed-tab"]
    assert proxy.new_tab_calls
    assert "bing.com/search" in proxy.new_tab_calls[0]
    assert proxy.info_calls == ["seed-tab", "seed-tab"]
    assert step["current_target"] == "seed-tab"
    assert step["observation"]["kind"] == "info"
    assert step["observation"]["bootstrap"]["kind"] == "bootstrap_tab"


def test_browser_cdp_action_planner_falls_back_when_model_returns_state(monkeypatch):
    """When the model echoes state instead of action, planner should fall back deterministically."""

    class FakeProvider:
        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            del prompt, max_tokens, temperature

            class Response:
                content = '{"request":"今天周几？","managed_targets":[]}'

            return Response()

    monkeypatch.setattr("localclaw.tools.browser_cdp_tool.get_llm_provider", lambda: FakeProvider())

    tool = BrowserCDPTool()
    action = asyncio.run(
        tool._ask_model_for_action(
            request="今天周几？",
            state={
                "request": "今天周几？",
                "current_target": None,
                "managed_targets": [],
                "explicit_urls": [],
                "recent_observations": [],
            },
            step_index=1,
            max_steps=6,
        )
    )

    assert action["action"] == "new_tab"
    assert "bing.com/search" in action["url"]


def test_browser_cdp_clock_query_uses_fast_path():
    """Clock/date questions should return immediately without browser-agent loops."""

    tool = BrowserCDPTool()
    result = asyncio.run(tool.run(request="今天周几？"))

    assert result.status == "success"
    assert result.data["fast_path"] == "clock_query"
    assert "今天是" in result.message


def test_browser_cdp_fact_question_uses_web_search_fast_path(monkeypatch):
    """General factual questions should use quick web snippets before long browser loops."""

    tool = BrowserCDPTool()

    async def fake_fetch(url, mode):
        assert "duckduckgo.com" in url
        assert mode == "jina"
        return {
            "mode": "jina",
            "url": url,
            "status_code": 200,
            "body": (
                "1.   beijing-marathon.com [![Image 1](https://example.com/icon.ico)]"
                "(https://duckduckgo.com/?q=site:www.beijing-marathon.com) "
                "2025年11月2日（星期日）07:30 开跑。\n"
                "2.   news.bjd.com.cn [![Image 2](https://example.com/icon.ico)]"
                "(https://duckduckgo.com/?q=site:news.bjd.com.cn) "
                "2025中国银行北京马拉松将于11月2日举行。"
            ),
        }

    async def unexpected_agent(*, request, url, max_steps):
        del request, url, max_steps
        raise AssertionError("agent loop should not run when web_search fast path succeeds")

    monkeypatch.setattr(tool, "_fetch_url", fake_fetch)
    monkeypatch.setattr(tool, "_execute_agent_mode", unexpected_agent)

    result = asyncio.run(tool.run(request="明天北京有马拉松比赛吗？"))

    assert result.status == "success"
    assert result.data["fast_path"] == "web_search"
    assert result.data["highlights"]


def test_browser_cdp_generic_event_query_does_not_use_race_wording(monkeypatch):
    """Vague event questions should not return race-specific fallback text."""

    tool = BrowserCDPTool()

    async def fake_fetch(url, mode):
        assert "duckduckgo.com" in url
        assert mode == "jina"
        return {
            "mode": "jina",
            "url": url,
            "status_code": 200,
            "body": (
                "1.   weather.com.cn [![Image 1](https://example.com/icon.ico)]"
                "(https://duckduckgo.com/?q=site:weather.com.cn) 2026年3月25日 西安天气预报。\n"
                "2.   nmc.cn [![Image 2](https://example.com/icon.ico)]"
                "(https://duckduckgo.com/?q=site:nmc.cn) 西安天气数据。"
            ),
        }

    monkeypatch.setattr(tool, "_fetch_url", fake_fetch)

    result = asyncio.run(tool.run(request="西安明天有啥好事？"))

    assert result.status == "success"
    assert result.data["fast_path"] == "web_search"
    assert "活动" in result.data["search_query"]
    assert "race schedule" not in result.message.lower()

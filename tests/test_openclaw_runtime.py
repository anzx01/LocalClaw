"""Tests for the OpenClaw-style local runtime."""

import io
import json
import zipfile

import pytest

from localclaw.core.models import AgentDecisionMode, ExecutionResult, Message, RiskLevel
from localclaw.core.openclaw_runtime import OpenClawRuntime
from localclaw.skills.loader import SkillLoader
from localclaw.skills.registry import SkillRegistry
from localclaw.tools.base import Tool, ToolRegistry
from localclaw.skills.registry.clawhub import ClawHubClient


class DummyTool(Tool):
    """Simple tool used to populate the runtime tool catalog."""

    name = "safe_shell"
    description = "Run a routine shell command"
    risk_level = RiskLevel.LOW
    inputs = {"command": "string"}
    outputs = {"stdout": "string"}

    async def execute(self, **kwargs):
        return ExecutionResult.success(data={"stdout": "ok"})


class DummyHttpGetTool(Tool):
    """Low-risk HTTP GET tool used by the news-feed guardrail tests."""

    name = "http_get"
    description = "Fetch public HTTP content"
    risk_level = RiskLevel.LOW
    inputs = {"url": "string"}
    outputs = {"status_code": "integer", "body": "string"}

    async def execute(self, **kwargs):
        return ExecutionResult.success(data={"status_code": 200, "body": ""})


@pytest.mark.asyncio
async def test_openclaw_runtime_can_return_direct_answer(monkeypatch):
    """Plain conversational requests should be answerable without planner fallback."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = json.dumps(
                    {
                        "mode": "answer",
                        "answer": "我可以聊天、读写文件、执行命令，也可以在需要时调用已安装的 skill。",
                    },
                    ensure_ascii=False,
                )

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="你会干啥？"))

    assert decision.mode == AgentDecisionMode.ANSWER
    assert "skill" in decision.answer


@pytest.mark.asyncio
async def test_openclaw_runtime_refines_skill_with_skill_markdown(monkeypatch, tmp_path):
    """Selected skills should get a second pass with SKILL.md context."""

    skill_dir = tmp_path / "repo_fs"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: repo.fs
version: 1.0.0
description: Read and list workspace files
type: workflow
inputs:
  action: string
  path: string
tools:
  - file_read
  - file_list
actions:
  - type: tool_call
    tool: file_read
    condition: "{{action == 'read'}}"
    params:
      path: "{{path}}"
---

# Repo FS

Use this skill for reading or listing files inside the current workspace.
""",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)
    skill = loader.load_from_file(skill_dir)
    assert skill is not None
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())

    captured = {"calls": 0, "refine_prompt": ""}

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            captured["calls"] += 1
            if captured["calls"] == 1:
                class Response:
                    content = '{"mode":"skill","skill":"repo.fs","params":{}}'

                return Response()

            captured["refine_prompt"] = prompt

            class Response:
                content = '{"mode":"skill","skill":"repo.fs","params":{"action":"read","path":"README.md"}}'

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(registry, tool_registry)
    decision = await runtime.decide(Message(content="看看 README.md"))

    assert decision.mode == AgentDecisionMode.SKILL
    assert decision.skill_name == "repo.fs"
    assert decision.params == {"action": "read", "path": "README.md"}
    assert captured["calls"] == 2
    assert "Use this skill for reading or listing files inside the current workspace." in captured["refine_prompt"]


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_generic_live_news_to_http_get_feed(monkeypatch, tmp_path):
    """Generic latest-news requests should prefer the low-risk RSS feed route."""

    skill_dir = tmp_path / "web_access"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: web-access
version: 1.0.0
description: Read live web information
type: workflow
inputs:
  request: string
tools:
  - browser_cdp
actions:
  - type: tool_call
    tool: browser_cdp
    params:
      request: "{{request}}"
---
# Web Access

Use this skill for live web information and browsing.
""",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)
    skill = loader.load_from_file(skill_dir)
    assert skill is not None
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"help","params":{}}'

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(registry, tool_registry)
    decision = await runtime.decide(Message(content="从网上给我10个最新新闻"))

    assert decision.mode == AgentDecisionMode.TOOL
    assert decision.tool_name == "http_get"
    assert decision.params["request"] == "从网上给我10个最新新闻"
    assert decision.params["limit"] == 10
    assert decision.params["format_hint"] == "rss_news"
    assert decision.params["url"] == "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    assert decision.source == "openclaw_runtime_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_explicit_news_url_still_prefers_web_skill(monkeypatch, tmp_path):
    """Specific news pages should keep using the browsing-oriented skill path."""

    skill_dir = tmp_path / "web_access"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: web-access
version: 1.0.0
description: Read live web information
type: workflow
inputs:
  request: string
tools:
  - browser_cdp
actions:
  - type: tool_call
    tool: browser_cdp
    params:
      request: "{{request}}"
---
# Web Access

Use this skill for live web information and browsing.
""",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)
    skill = loader.load_from_file(skill_dir)
    assert skill is not None
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"help","params":{}}'

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(registry, tool_registry)
    decision = await runtime.decide(Message(content="看看 https://news.google.com 里的最新新闻"))

    assert decision.mode == AgentDecisionMode.SKILL
    assert decision.skill_name == "web-access"
    assert decision.params["url"] == "https://news.google.com"


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_desktop_folder_listing(monkeypatch):
    """Desktop folder requests should route to a deterministic low-risk listing intent."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="看看我桌面有那些文件夹？"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "list_folders"
    assert decision.params["path"] == "~/Desktop"
    assert decision.params["folders_only"] is True
    assert decision.source == "openclaw_runtime_guardrail"


def test_clawhub_client_preserves_skill_markdown_bundle(tmp_path):
    """Markdown skills should stay in OpenClaw-style directory format when installed."""

    client = ClawHubClient()
    target_dir = tmp_path / "managed"
    bundle = {
        "name": "repo.fs",
        "version": "1.0.0",
        "description": "Read files",
        "type": "workflow",
        "inputs": {"action": "string", "path": "string"},
        "actions": [
            {
                "type": "tool_call",
                "tool": "file_read",
                "params": {"path": "{{path}}"},
            }
        ],
        "metadata": {
            "source_format": "skill_markdown",
            "documentation": "# Repo FS\n\nRead files from the workspace.",
            "source_path": "/tmp/repo.fs/SKILL.md",
        },
        "files": {"scripts/helper.py": "print('ok')\n"},
    }

    saved = client.save_skill_bundle("repo.fs", bundle, target_dir)

    assert saved is True
    assert (target_dir / "repo.fs" / "SKILL.md").exists()
    assert not (target_dir / "repo.fs" / "repo.fs.json").exists()
    assert (target_dir / "repo.fs" / "scripts" / "helper.py").exists()
    assert "Read files from the workspace." in (target_dir / "repo.fs" / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_clawhub_client_search_normalizes_real_search_payload():
    """Real ClawHub search payloads should normalize into marketplace listings."""

    client = ClawHubClient(base_url="https://clawhub.example")

    async def fake_get_json(path, params=None):
        assert path == "/api/v1/search"
        assert params == {"q": "weather", "limit": "20"}
        return {
            "results": [
                {
                    "slug": "weather.forecast",
                    "displayName": "Weather Forecast",
                    "summary": "Forecast weather via wttr.in",
                    "version": "2.1.0",
                    "updatedAt": 1234567890,
                    "score": 0.97,
                }
            ]
        }

    client._get_json = fake_get_json

    skills = await client.search_skills("weather")

    assert skills == [
        {
            "id": "weather.forecast",
            "name": "Weather Forecast",
            "version": "2.1.0",
            "description": "Forecast weather via wttr.in",
            "author": "ClawHub",
            "homepage": "https://clawhub.example/skills/weather.forecast",
            "repository": "",
            "category": "remote",
            "tags": ["clawhub", "remote"],
            "source": "remote",
            "source_label": "ClawHub",
            "updated_at": 1234567890,
            "score": 0.97,
        }
    ]

    await client.close()


@pytest.mark.asyncio
async def test_clawhub_client_search_records_http_failures():
    """Non-200 registry responses should be surfaced to the caller."""

    client = ClawHubClient(base_url="https://clawhub.example")

    async def fake_get_json(path, params=None):
        assert path == "/api/v1/search"
        client.last_request_error = "/api/v1/search returned 429: Rate limit exceeded"
        return None

    client._get_json = fake_get_json

    skills = await client.search_skills("weather")

    assert skills == []
    assert client.last_search_error == "/api/v1/search returned 429: Rate limit exceeded"

    await client.close()


@pytest.mark.asyncio
async def test_clawhub_client_fetches_and_canonicalizes_real_skill_archive(tmp_path):
    """Downloaded ClawHub archives should support OpenClaw-style skill.md variants."""

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle_zip:
        bundle_zip.writestr(
            "weather.forecast/skill.md",
            """---
name: weather.forecast
version: 2.1.0
description: Forecast weather via wttr.in
type: workflow
inputs:
  location: string
tools:
  - http_get
actions:
  - type: tool_call
    tool: http_get
    params:
      url: "https://wttr.in/{{location}}?format=j1"
---

# Weather Forecast

Fetches weather data from wttr.in.
""",
        )
        bundle_zip.writestr("weather.forecast/scripts/fetch.py", "print('forecast')\n")

    client = ClawHubClient(base_url="https://clawhub.example")

    async def fake_get_skill_detail(skill_id):
        assert skill_id == "weather.forecast"
        return {
            "id": "weather.forecast",
            "name": "Weather Forecast",
            "version": "2.1.0",
            "author": "ClawHub Team",
            "homepage": "https://clawhub.example/skills/weather.forecast",
        }

    async def fake_download(slug, version):
        assert slug == "weather.forecast"
        assert version == "2.1.0"
        return archive.getvalue()

    client.get_skill_detail = fake_get_skill_detail
    client._download_skill_archive = fake_download

    bundle = await client.fetch_skill_bundle("weather.forecast")
    assert bundle is not None
    assert bundle["name"] == "weather.forecast"
    assert bundle["metadata"]["source_format"] == "skill_markdown"
    assert bundle["metadata"]["clawhub"]["slug"] == "weather.forecast"
    assert bundle["files"]["scripts/fetch.py"] == "print('forecast')\n"
    assert bundle["files"]["skill.md"].startswith("---\n")

    bundle["metadata"]["localclaw_guard"] = {"mode": "disable_high_risk"}
    target_dir = tmp_path / "managed"
    saved = client.save_skill_bundle("weather.forecast", bundle, target_dir)

    assert saved is True
    installed_dir = target_dir / "weather.forecast"
    installed_names = {path.name for path in installed_dir.iterdir()}
    assert "SKILL.md" in installed_names
    assert "skill.md" not in installed_names
    assert (installed_dir / "scripts" / "fetch.py").exists()
    assert "disable_high_risk" in (installed_dir / "SKILL.md").read_text(encoding="utf-8")

    await client.close()

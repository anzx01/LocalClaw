"""Tests for the OpenClaw-style local runtime."""

import io
import json
import zipfile

import pytest

from localclaw.core.models import AgentDecisionMode, ExecutionResult, Message, RiskLevel
from localclaw.core.openclaw_runtime import OpenClawRuntime
from localclaw.skills.base import create_skill_from_dict
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


class DummyDiskUsageTool(Tool):
    """Low-risk disk usage tool used by drive-capacity guardrail tests."""

    name = "disk_usage"
    description = "Check disk capacity for a path"
    risk_level = RiskLevel.LOW
    inputs = {"path": "string"}
    outputs = {"free_bytes": "integer", "total_bytes": "integer"}

    async def execute(self, **kwargs):
        return ExecutionResult.success(data={"path": "C:/", "free_bytes": 1, "total_bytes": 2})


class DummyBrowserTool(Tool):
    """Browser tool placeholder used by web-fallback routing tests."""

    name = "browser_cdp"
    description = "Browser automation"
    risk_level = RiskLevel.HIGH
    inputs = {"request": "string"}
    outputs = {"message": "string"}

    async def execute(self, **kwargs):
        return ExecutionResult.success(data={"message": "ok"})


class DummyLaunchAppTool(Tool):
    """Desktop launcher placeholder used by instruction-skill tests."""

    name = "launch_app"
    description = "Launch a desktop app"
    risk_level = RiskLevel.LOW
    inputs = {"target": "string"}
    outputs = {"message": "string"}

    async def execute(self, **kwargs):
        return ExecutionResult.success(data={"message": "ok"})


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
async def test_openclaw_runtime_extracts_last_json_object_from_reasoning(monkeypatch):
    """Reasoning-heavy local models should still resolve the final JSON decision."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            del prompt, max_tokens, temperature

            class Response:
                content = (
                    'I considered {"mode":"unknown"} first.\n'
                    "</think>\n"
                    '{"mode":"intent","intent":"check_weather","params":{"location":"北京","day_offset":1}}'
                )

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(
        SkillRegistry(),
        ToolRegistry(),
        refine_skill_decision=False,
        enable_request_guardrails=False,
    )
    decision = await runtime.decide(Message(content="明天北京天气怎么样"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "check_weather"
    assert decision.params == {"location": "北京", "day_offset": 1}


@pytest.mark.asyncio
async def test_openclaw_runtime_prompt_mentions_drive_directory_queries(monkeypatch):
    """The runtime prompt should teach the local model how to route drive-root listings."""

    captured = {"prompt": ""}

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            captured["prompt"] = prompt

            class Response:
                content = '{"mode":"intent","intent":"list_folders","params":{"path":"D:/","folders_only":true}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="我d盘有哪些目录？"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "list_folders"
    assert decision.params["path"] == "D:/"
    assert decision.params["folders_only"] is True
    assert "我D盘有哪些目录" in captured["prompt"]


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
async def test_openclaw_runtime_instruction_skill_next_action_uses_skill_markdown(monkeypatch, tmp_path):
    """Instruction-only skills should expose SKILL.md guidance to the next-action prompt."""

    skill_dir = tmp_path / "app_launcher"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: app-launcher
version: 1.0.0
description: Open a desktop application
type: workflow
inputs:
  target: string
tools:
  - launch_app
---

# App Launcher

Use the launch_app tool to open a requested desktop application.
""",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)
    skill = loader.load_from_file(skill_dir)
    assert skill is not None
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(DummyLaunchAppTool())

    captured = {"prompt": ""}

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            captured["prompt"] = prompt

            class Response:
                content = '{"mode":"tool","tool":"launch_app","params":{"target":"vscode"}}'

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(registry, tool_registry)
    decision = await runtime.decide_instruction_skill_next_action(
        "app-launcher",
        Message(content="打开 VS Code"),
    )

    assert runtime.is_instruction_skill("app-launcher") is True
    assert decision.mode == AgentDecisionMode.TOOL
    assert decision.tool_name == "launch_app"
    assert decision.params == {"target": "vscode"}
    assert "Use the launch_app tool to open a requested desktop application." in captured["prompt"]


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


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_desktop_file_listing(monkeypatch):
    """Desktop file requests should stay on the general file-listing path."""

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
    decision = await runtime.decide(Message(content="列出桌面文件"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "file_list"
    assert decision.params["path"] == "~/Desktop"
    assert decision.params["folders_only"] is False
    assert decision.source == "openclaw_runtime_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_desktop_file_read(monkeypatch):
    """Desktop file open requests should route to read_file."""

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
    decision = await runtime.decide(Message(content="打开桌面的AI量化工具.txt文件"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "read_file"
    assert decision.params["path"] == "~/Desktop/AI量化工具.txt"
    assert decision.source == "openclaw_runtime_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_desktop_file_read_prefers_repo_fs_skill(monkeypatch):
    """Desktop file open requests should prefer repo.fs when the skill is available."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    registry = SkillRegistry()
    skill = create_skill_from_dict(
        {
            "name": "workspace_fs",
            "description": "Read workspace files",
            "inputs": {"action": "string", "path": "string"},
            "actions": [{"type": "transform", "template": "reading {{path}}"}],
            "metadata": {"skill_key": "repo.fs"},
        }
    )
    skill.enable()
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(registry, tool_registry)
    decision = await runtime.decide(Message(content="打开桌面的AI量化工具.txt文件"))

    assert decision.mode == AgentDecisionMode.SKILL
    assert decision.skill_name == "workspace_fs"
    assert decision.params == {"action": "read", "path": "~/Desktop/AI量化工具.txt"}
    assert decision.source == "openclaw_runtime_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_file_request_overrides_model_date_query(monkeypatch):
    """File-opening requests should override an incorrect date_query model decision."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"date_query","params":{}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="打开桌面的 AI量化工具.txt"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "read_file"
    assert decision.params["path"] == "~/Desktop/AI量化工具.txt"
    assert decision.rationale == "filesystem_read_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_file_request_overrides_browser_tool(monkeypatch):
    """File-opening requests should override an incorrect browser_cdp tool decision."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"tool","tool":"browser_cdp","params":{"request":"打开桌面的 AI量化工具.txt"}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="打开桌面的 AI量化工具.txt"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "read_file"
    assert decision.params["path"] == "~/Desktop/AI量化工具.txt"
    assert decision.rationale == "filesystem_read_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_file_request_overrides_web_skill_with_repo_fs(monkeypatch):
    """File-opening requests should prefer repo.fs over an incorrect web-access skill decision."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"skill","skill":"web-access","params":{"request":"打开桌面的 AI量化工具.txt"}}'

            return Response()

    registry = SkillRegistry()
    web_access = create_skill_from_dict(
        {
            "name": "web-access",
            "description": "Browser skill",
            "inputs": {"request": "string"},
            "actions": [{"type": "transform", "template": "web {{request}}"}],
        }
    )
    web_access.enable()
    registry.register(web_access)
    repo_fs = create_skill_from_dict(
        {
            "name": "workspace_fs",
            "description": "Read workspace files",
            "inputs": {"action": "string", "path": "string"},
            "actions": [{"type": "transform", "template": "reading {{path}}"}],
            "metadata": {"skill_key": "repo.fs"},
        }
    )
    repo_fs.enable()
    registry.register(repo_fs)

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(registry, tool_registry)
    decision = await runtime.decide(Message(content="打开桌面的 AI量化工具.txt"))

    assert decision.mode == AgentDecisionMode.SKILL
    assert decision.skill_name == "workspace_fs"
    assert decision.params == {"action": "read", "path": "~/Desktop/AI量化工具.txt"}
    assert decision.rationale == "filesystem_read_skill_guardrail"


def test_openclaw_runtime_clock_guardrail_rejects_desktop_file_request():
    """File-opening requests should never be treated as clock queries."""

    runtime = OpenClawRuntime(SkillRegistry(), ToolRegistry())

    assert runtime._looks_like_clock_query("打开桌面的 AI量化工具.txt") is False


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_weather_questions(monkeypatch):
    """Weather-like questions should route to the live weather intent path."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"answer","answer":"今天天气看起来不错。"}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="\u5916\u9762\u7684\u5929\u84dd\u4e0d\uff1f"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "check_weather"
    assert decision.params["location"] == ""
    assert decision.params["day_offset"] == 0
    assert decision.params["day_label"] == "\u4eca\u5929"
    assert decision.source == "openclaw_runtime_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_weather_hot_question(monkeypatch):
    """Colloquial weather questions like '今天热吗' should map to weather intent."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="今天热吗？"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "check_weather"
    assert decision.params["day_offset"] == 0
    assert decision.params["day_label"] == "今天"


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_weather_hot_bu_question(monkeypatch):
    """Short colloquial prompts like '西安热不' should still map to weather intent."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="西安热不？"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "check_weather"
    assert decision.params["location"] == "西安"
    assert decision.params["day_offset"] == 0
    assert decision.params["day_label"] == "今天"


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_weather_day_after_after_tomorrow(monkeypatch):
    """Queries like '大后天冷不' should be treated as weather intent with day_offset=3."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="大后天冷不？"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "check_weather"
    assert decision.params["location"] == ""
    assert decision.params["day_offset"] == 3
    assert decision.params["day_label"] == "大后天"


@pytest.mark.asyncio
async def test_openclaw_runtime_can_disable_request_guardrails(monkeypatch):
    """When guardrails are disabled, runtime should keep the model decision."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"answer","answer":"模型直答"}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(
        SkillRegistry(),
        tool_registry,
        enable_request_guardrails=False,
    )
    decision = await runtime.decide(Message(content="今天热吗？"))

    assert decision.mode == AgentDecisionMode.ANSWER
    assert decision.answer == "模型直答"
    assert decision.source == "openclaw_runtime"


@pytest.mark.asyncio
async def test_openclaw_runtime_fallback_prefers_web_skill_for_unknown_questions(monkeypatch):
    """Unknown factual questions should prefer web-access before other web-search skills."""

    class FakeProvider:
        async def is_available(self):
            return False

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    registry = SkillRegistry()
    web_access = create_skill_from_dict(
        {
            "name": "web-access",
            "version": "1.0.0",
            "description": "Browse and fetch live web information",
            "type": "workflow",
            "inputs": {"request": "string"},
            "actions": [{"type": "transform", "template": "browse {{request}}"}],
        }
    )
    web_access.enable()
    registry.register(web_access)

    skill = create_skill_from_dict(
        {
            "name": "tavily-web-search",
            "version": "1.0.0",
            "description": "Search the web for current information",
            "type": "workflow",
            "inputs": {"query": "string"},
            "actions": [{"type": "transform", "template": "search {{query}}"}],
        }
    )
    skill.enable()
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())

    runtime = OpenClawRuntime(registry, tool_registry)
    decision = await runtime.fallback_to_chat_answer(Message(content="量子纠缠是什么意思？"))

    assert decision.mode == AgentDecisionMode.SKILL
    assert decision.skill_name == "web-access"
    assert decision.params["request"] == "量子纠缠是什么意思？"
    assert decision.params["query"] == "量子纠缠是什么意思？"
    assert decision.source == "openclaw_runtime_web_fallback"


@pytest.mark.asyncio
async def test_openclaw_runtime_clock_queries_fallback_to_web_access(monkeypatch):
    """Date/time questions should use web-access/browser fallback when planning fails."""

    class FakeProvider:
        async def is_available(self):
            return False

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    registry = SkillRegistry()
    web_access = create_skill_from_dict(
        {
            "name": "web-access",
            "version": "1.0.0",
            "description": "Browse and fetch live web information",
            "type": "workflow",
            "inputs": {"request": "string"},
            "actions": [{"type": "transform", "template": "browse {{request}}"}],
        }
    )
    web_access.enable()
    registry.register(web_access)

    runtime = OpenClawRuntime(registry, ToolRegistry())
    date_decision = await runtime.fallback_to_chat_answer(Message(content="今天周几？"))
    time_decision = await runtime.fallback_to_chat_answer(Message(content="现在几点了？"))

    tomorrow_decision = await runtime.fallback_to_chat_answer(Message(content="\u660e\u5929\u5468\u51e0\uff1f"))

    assert date_decision.mode == AgentDecisionMode.SKILL
    assert date_decision.skill_name == "web-access"
    assert date_decision.source == "openclaw_runtime_web_fallback"

    assert time_decision.mode == AgentDecisionMode.SKILL
    assert time_decision.skill_name == "web-access"
    assert time_decision.source == "openclaw_runtime_web_fallback"

    assert tomorrow_decision.mode == AgentDecisionMode.SKILL
    assert tomorrow_decision.skill_name == "web-access"
    assert tomorrow_decision.source == "openclaw_runtime_web_fallback"


@pytest.mark.asyncio
async def test_openclaw_runtime_disabled_guardrails_skip_deterministic_web_fallback(monkeypatch):
    """When guardrails are disabled, chat fallback should not inject hardcoded web-skill routing."""

    class ChatFallbackProvider:
        async def is_available(self):
            return True

        async def chat(self, messages, max_tokens=None, temperature=None):
            class Response:
                content = "I cannot verify live information here."

            return Response()

        async def generate(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
            class Response:
                content = "I cannot verify live information here."

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: ChatFallbackProvider())

    registry = SkillRegistry()
    web_access = create_skill_from_dict(
        {
            "name": "web-access",
            "version": "1.0.0",
            "description": "Browse and fetch live web information",
            "type": "workflow",
            "inputs": {"request": "string"},
            "actions": [{"type": "transform", "template": "browse {{request}}"}],
        }
    )
    web_access.enable()
    registry.register(web_access)

    runtime = OpenClawRuntime(registry, ToolRegistry(), enable_request_guardrails=False)
    decision = await runtime.fallback_to_chat_answer(Message(content="今天周几？"))

    assert decision.mode == AgentDecisionMode.ANSWER
    assert decision.source == "openclaw_runtime_chat_fallback"
    assert "verify" in decision.answer.lower()


@pytest.mark.asyncio
async def test_openclaw_runtime_clock_queries_override_model_answers(monkeypatch):
    """Clock-like requests should prefer web-access over direct model answers."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"answer","answer":"今天是周一"}'

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    registry = SkillRegistry()
    web_access = create_skill_from_dict(
        {
            "name": "web-access",
            "version": "1.0.0",
            "description": "Browse and fetch live web information",
            "type": "workflow",
            "inputs": {"request": "string"},
            "actions": [{"type": "transform", "template": "browse {{request}}"}],
        }
    )
    web_access.enable()
    registry.register(web_access)

    runtime = OpenClawRuntime(registry, ToolRegistry())
    decision = await runtime.decide(Message(content="今天周几？"))

    assert decision.mode == AgentDecisionMode.SKILL
    assert decision.skill_name == "web-access"
    assert decision.source == "openclaw_runtime_guardrail"
    assert decision.rationale == "clock_query_web_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_clock_intent_is_upgraded_to_web_skill(monkeypatch):
    """date_query/time_now intents from the model should still route via web-access."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"date_query","params":{}}'

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    registry = SkillRegistry()
    web_access = create_skill_from_dict(
        {
            "name": "web-access",
            "version": "1.0.0",
            "description": "Browse and fetch live web information",
            "type": "workflow",
            "inputs": {"request": "string"},
            "actions": [{"type": "transform", "template": "browse {{request}}"}],
        }
    )
    web_access.enable()
    registry.register(web_access)

    runtime = OpenClawRuntime(registry, ToolRegistry())
    decision = await runtime.decide(Message(content="\u4eca\u5929\u5468\u51e0\uff1f"))

    assert decision.mode == AgentDecisionMode.SKILL
    assert decision.skill_name == "web-access"
    assert decision.source == "openclaw_runtime_guardrail"
    assert decision.rationale == "clock_query_web_guardrail"


@pytest.mark.asyncio
async def test_openclaw_runtime_non_clock_query_is_not_forced_into_clock_guardrail(monkeypatch):
    """If the model mislabels a non-clock question as date_query, prefer web fallback routing."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"date_query","params":{}}'

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyBrowserTool())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="明天北京有马拉松比赛吗？"))

    assert decision.mode == AgentDecisionMode.TOOL
    assert decision.tool_name == "browser_cdp"
    assert decision.source == "openclaw_runtime_web_fallback"
    assert decision.rationale == "unknown_question_web_fallback"


@pytest.mark.asyncio
async def test_openclaw_runtime_weather_location_strips_day_prefix(monkeypatch):
    """Weather guardrails should not include relative day words inside the city name."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)

    beijing = await runtime.decide(Message(content="后天北京天气？"))
    xian = await runtime.decide(Message(content="明天西安天气？"))

    assert beijing.mode == AgentDecisionMode.INTENT
    assert beijing.intent_name == "check_weather"
    assert beijing.params["location"] == "北京"
    assert beijing.params["day_offset"] == 2

    assert xian.mode == AgentDecisionMode.INTENT
    assert xian.intent_name == "check_weather"
    assert xian.params["location"] == "西安"
    assert xian.params["day_offset"] == 1


@pytest.mark.asyncio
async def test_openclaw_runtime_weather_prefers_installed_weather_skill(monkeypatch):
    """Weather-like prompts should route to an installed weather skill when available."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    registry = SkillRegistry()
    weather_skill = create_skill_from_dict(
        {
            "name": "weather",
            "version": "1.0.0",
            "description": "weather",
            "type": "workflow",
            "inputs": {"location": "string"},
            "actions": [{"type": "transform", "template": "weather {{location}}"}],
        }
    )
    weather_skill.enable()
    registry.register(weather_skill)

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyHttpGetTool())

    runtime = OpenClawRuntime(registry, tool_registry)
    decision = await runtime.decide(Message(content="\u660e\u5929\u5317\u4eac\u5929\u6c14\uff1f"))

    assert decision.mode == AgentDecisionMode.SKILL
    assert decision.skill_name == "weather"
    assert decision.source == "openclaw_runtime_guardrail"
    assert decision.rationale == "weather_skill_guardrail"
    assert decision.params["location"] == "\u5317\u4eac"
    assert decision.params["day_offset"] == 1


@pytest.mark.asyncio
async def test_openclaw_runtime_guardrails_disk_space_questions(monkeypatch):
    """Drive free-space requests should route to a deterministic disk-usage intent."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    tool_registry = ToolRegistry()
    tool_registry.register(DummyTool())
    tool_registry.register(DummyDiskUsageTool())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    runtime = OpenClawRuntime(SkillRegistry(), tool_registry)
    decision = await runtime.decide(Message(content="我c盘空间还剩多少？"))

    assert decision.mode == AgentDecisionMode.INTENT
    assert decision.intent_name == "check_disk_space"
    assert decision.params["path"] == "C:/"
    assert decision.params["drive"] == "C"
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
    """Real ClawHub search payloads should normalize and enrich marketplace stats."""

    client = ClawHubClient(base_url="https://clawhub.example")

    async def fake_get_json(path, params=None):
        if path == "/api/v1/search":
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
        if path == "/api/v1/skills/weather.forecast":
            return {
                "skill": {
                    "slug": "weather.forecast",
                    "displayName": "Weather Forecast",
                    "summary": "Forecast weather via wttr.in",
                    "updatedAt": 1234567890,
                    "stats": {
                        "downloads": 20543,
                        "installsCurrent": 234,
                        "installsAllTime": 242,
                        "stars": 46,
                    },
                },
                "owner": {"displayName": "Forecast Team"},
                "latestVersion": {"version": "2.1.0"},
            }
        raise AssertionError(f"Unexpected path: {path}")

    client._get_json = fake_get_json

    skills = await client.search_skills("weather")

    assert skills == [
        {
            "id": "weather.forecast",
            "name": "Weather Forecast",
            "version": "2.1.0",
            "description": "Forecast weather via wttr.in",
            "author": "Forecast Team",
            "homepage": "https://clawhub.example/skills/weather.forecast",
            "repository": "",
            "category": "remote",
            "tags": ["clawhub", "remote"],
            "source": "remote",
            "source_label": "ClawHub",
            "updated_at": 1234567890,
            "score": 0.97,
            "stars": 46,
            "downloads": 20543,
            "current_installs": 234,
            "all_time_installs": 242,
        }
    ]

    await client.close()


def test_clawhub_client_detail_normalizes_marketplace_stats():
    """Skill detail payloads should expose ClawHub marketplace counters."""

    client = ClawHubClient(base_url="https://clawhub.example")

    detail = client._normalize_skill_detail(
        {
            "skill": {
                "slug": "weather.forecast",
                "displayName": "Weather Forecast",
                "summary": "Forecast weather via wttr.in",
                "createdAt": 1234500000,
                "updatedAt": 1234567890,
                "stats": {
                    "downloads": 20543,
                    "installsCurrent": 234,
                    "installsAllTime": 242,
                    "stars": 46,
                },
                "tags": {"weather": True},
            },
            "owner": {"displayName": "Forecast Team", "handle": "forecast"},
            "latestVersion": {"version": "2.1.0"},
        }
    )

    assert detail is not None
    assert detail["stars"] == 46
    assert detail["downloads"] == 20543
    assert detail["current_installs"] == 234
    assert detail["all_time_installs"] == 242


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
async def test_clawhub_client_uses_threaded_dns_resolver(monkeypatch):
    """ClawHub HTTP sessions should prefer the threaded resolver for Windows-friendly DNS."""

    captured = {}

    class FakeThreadedResolver:
        pass

    class FakeClientSession:
        def __init__(self, connector=None, **kwargs):
            captured["connector"] = connector

        async def close(self):
            return None

    monkeypatch.setattr("localclaw.skills.registry.clawhub.aiohttp.ThreadedResolver", FakeThreadedResolver)
    monkeypatch.setattr("localclaw.skills.registry.clawhub.aiohttp.ClientSession", FakeClientSession)

    def fake_tcp_connector(*, resolver=None, **kwargs):
        captured["resolver"] = resolver
        captured["connector_kwargs"] = kwargs
        return {"resolver": resolver, **kwargs}

    monkeypatch.setattr("localclaw.skills.registry.clawhub.aiohttp.TCPConnector", fake_tcp_connector)

    client = ClawHubClient(base_url="https://clawhub.example")
    session = await client._ensure_session()

    assert isinstance(captured["resolver"], FakeThreadedResolver)
    assert isinstance(session, FakeClientSession)
    assert captured["connector"] == {"resolver": captured["resolver"]}

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

"""Tests for the engine module."""

import asyncio
import pytest

from localclaw.config.settings import Settings
from localclaw.core.engine import ExecutionEngine, get_engine
from localclaw.core.models import (
    AgentDecision,
    AgentDecisionMode,
    ExecutionResult,
    Intent,
    Message,
    Plan,
    Step,
    StepType,
    TaskState,
)
from localclaw.core.openclaw_runtime import OpenClawRuntime
from localclaw.core.parser import create_default_parser
from localclaw.core.planner import Planner, create_default_planner
from localclaw.core.verifier import create_default_verifier
from localclaw.skills.base import create_skill_from_dict
from localclaw.skills.loader import SkillLoader
from localclaw.skills.registry import SkillRegistry
from localclaw.llm.provider import LLMConfig, LLMProvider, LLMProviderType, set_llm_provider
from localclaw.tools.base import Tool, ToolRegistry
from localclaw.tools.file_tool import FileListTool
from localclaw.tools.local_model_tool import LocalModelPromptTool


@pytest.fixture
def engine():
    """Create an engine instance for testing."""
    from localclaw.tools.base import Tool, register_tool
    
    class TestTool(Tool):
        name = "test_tool"
        description = "Test tool"
        inputs = {}
        outputs = {"result": "string"}
        
        async def execute(self, **kwargs):
            from localclaw.core.models import ExecutionResult
            return ExecutionResult.success(data={"result": "test_ok"})
    
    register_tool(TestTool())

    registry = SkillRegistry()
    for skill_data in (
        {
            "name": "hello",
            "version": "1.0.0",
            "description": "Say hello to someone",
            "type": "atomic",
            "inputs": {"name": "string"},
            "outputs": {"message": "string"},
            "actions": [{"type": "transform", "template": "Hello, {{name}}!"}],
        },
        {
            "name": "echo",
            "version": "1.0.0",
            "description": "Echo back a message",
            "type": "atomic",
            "inputs": {"text": "string"},
            "outputs": {"message": "string"},
            "actions": [{"type": "transform", "template": "{{text}}"}],
        },
    ):
        registry.register(create_skill_from_dict(skill_data))

    return ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=False, llm_parse_only=False),
        parser=create_default_parser(llm_enabled=False, llm_parse_only=False),
        skill_registry=registry,
    )


@pytest.mark.asyncio
async def test_engine_process_greeting(engine):
    """Test processing a greeting message."""
    message = Message(content="hello world", user_id="test", channel="test")
    
    task = await engine.process_message(message)
    
    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "greeting"


@pytest.mark.asyncio
async def test_engine_process_help(engine):
    """Test processing a help message."""
    message = Message(content="help", user_id="test", channel="test")
    
    task = await engine.process_message(message)
    
    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "help"


@pytest.mark.asyncio
async def test_engine_process_chinese_help(engine):
    """Chinese help-style prompts should also map to the help plan."""
    message = Message(content="你会干啥？", user_id="test", channel="test")

    task = await engine.process_message(message)

    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "help"


@pytest.mark.asyncio
async def test_engine_local_model_mode_bypasses_parser():
    """When local-model-only mode is enabled, the runtime may answer directly."""

    class DirectRuntime(OpenClawRuntime):
        def __init__(self):
            self.called = False

        async def decide(self, message, context=None):
            self.called = True
            return AgentDecision(
                mode=AgentDecisionMode.ANSWER,
                answer="Local model handled this message directly.",
                confidence=0.9,
                source="openclaw_runtime",
                raw_message=message.content,
            )

    runtime = DirectRuntime()
    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=True, llm_parse_only=True),
        verifier=create_default_verifier(),
        tool_registry=ToolRegistry(),
        skill_registry=SkillRegistry(),
        openclaw_runtime=runtime,
    )

    task = await engine.process_message(Message(content="你会干啥？", user_id="u1", channel="test"))

    assert runtime.called is True
    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "answer"
    assert task.result.data["result"] == "Local model handled this message directly."


@pytest.mark.asyncio
async def test_engine_local_model_decision_timeout_falls_back_to_deterministic_parser():
    """Runtime-decision timeout should fall back to the deterministic parser path."""

    class SlowRuntime(OpenClawRuntime):
        def __init__(self):
            pass

        async def decide(self, message, context=None):
            await asyncio.sleep(0.2)
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime",
                raw_message=message.content,
            )

    engine = ExecutionEngine(
        settings=Settings(
            _env_file=None,
            llm_enabled=True,
            llm_parse_only=True,
            default_timeout=0.05,
        ),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=ToolRegistry(),
        skill_registry=SkillRegistry(),
        openclaw_runtime=SlowRuntime(),
    )

    task = await engine.process_message(Message(content="help", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "help"


@pytest.mark.asyncio
async def test_engine_local_model_unknown_does_not_use_legacy_parser_fallback(monkeypatch):
    """In llm_parse_only mode, planner retries should stay on the local-model path."""

    class UnknownRuntime(OpenClawRuntime):
        def __init__(self):
            pass

        async def decide(self, message, context=None):
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime",
                raw_message=message.content,
            )

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"intent":"unknown","params":{}}'

            return Response()

    class UnavailableChatProvider:
        async def is_available(self):
            return False

    monkeypatch.setattr("localclaw.core.planner.get_llm_provider", lambda: FakeProvider())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: UnavailableChatProvider())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=True, llm_parse_only=True),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=ToolRegistry(),
        skill_registry=SkillRegistry(),
        openclaw_runtime=UnknownRuntime(),
    )

    task = await engine.process_message(Message(content="你会干啥？", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "unknown"
    assert task.intent.source == "planner_llm"


@pytest.mark.asyncio
async def test_engine_local_model_unknown_falls_back_to_free_chat(monkeypatch):
    """If planning still resolves to unknown, LocalClaw should use the local model's free-chat answer."""

    class UnknownRuntime(OpenClawRuntime):
        def __init__(self):
            pass

        async def decide(self, message, context=None):
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime",
                raw_message=message.content,
            )

    class PlannerProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"intent":"unknown","params":{}}'

            return Response()

    class ChatFallbackProvider:
        async def is_available(self):
            return True

        async def chat(self, messages, max_tokens=None, temperature=None):
            class Response:
                content = "\u8fd9\u4e2a\u8bf7\u6c42\u76ee\u524d\u6ca1\u6709\u5bf9\u5e94\u7684\u52a8\u4f5c\uff0c\u4f46\u6211\u53ef\u4ee5\u5148\u7528\u81ea\u7531\u804a\u5929\u65b9\u5f0f\u56de\u7b54\u4f60\u3002"

            return Response()

        async def generate(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
            class Response:
                content = "\u8fd9\u4e2a\u8bf7\u6c42\u76ee\u524d\u6ca1\u6709\u5bf9\u5e94\u7684\u52a8\u4f5c\uff0c\u4f46\u6211\u53ef\u4ee5\u5148\u7528\u81ea\u7531\u804a\u5929\u65b9\u5f0f\u56de\u7b54\u4f60\u3002"

            return Response()

    monkeypatch.setattr("localclaw.core.planner.get_llm_provider", lambda: PlannerProvider())
    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: ChatFallbackProvider())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=True, llm_parse_only=True),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=ToolRegistry(),
        skill_registry=SkillRegistry(),
        openclaw_runtime=UnknownRuntime(),
    )

    task = await engine.process_message(Message(content="\u968f\u4fbf\u804a\u804a\u4f60\u600e\u4e48\u770b\u5f85\u8fd9\u4ef6\u4e8b", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "answer"
    assert task.intent.source == "openclaw_runtime_chat_fallback"
    assert (
        task.result.data["result"]
        == "\u8fd9\u4e2a\u8bf7\u6c42\u76ee\u524d\u6ca1\u6709\u5bf9\u5e94\u7684\u52a8\u4f5c\uff0c\u4f46\u6211\u53ef\u4ee5\u5148\u7528\u81ea\u7531\u804a\u5929\u65b9\u5f0f\u56de\u7b54\u4f60\u3002"
    )


@pytest.mark.asyncio
async def test_engine_local_model_pseudo_chat_intent_falls_back_to_free_chat(monkeypatch):
    """Pseudo conversational intents should still route to chat fallback answers."""

    class PseudoIntentRuntime(OpenClawRuntime):
        def __init__(self):
            pass

        async def decide(self, message, context=None):
            return AgentDecision(
                mode=AgentDecisionMode.INTENT,
                intent_name="chat/help/general knowledge",
                params={},
                confidence=0.7,
                source="openclaw_runtime",
                raw_message=message.content,
            )

    class ChatFallbackProvider:
        async def is_available(self):
            return True

        async def chat(self, messages, max_tokens=None, temperature=None):
            class Response:
                content = "Common side effects include muscle pain, nausea, and headache."

            return Response()

        async def generate(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
            class Response:
                content = "Common side effects include muscle pain, nausea, and headache."

            return Response()

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: ChatFallbackProvider())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=True, llm_parse_only=True),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=ToolRegistry(),
        skill_registry=SkillRegistry(),
        openclaw_runtime=PseudoIntentRuntime(),
    )

    task = await engine.process_message(
        Message(content="What are the side effects of rosuvastatin?", user_id="u1", channel="test")
    )

    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "answer"
    assert task.intent.source == "openclaw_runtime_chat_fallback"
    assert "muscle pain" in task.result.data["result"]


@pytest.mark.asyncio
async def test_engine_local_model_can_plan_drive_directory_listing(monkeypatch):
    """Drive-root folder questions should be handled by the local-model planner path."""

    class UnknownRuntime(OpenClawRuntime):
        def __init__(self):
            pass

        async def decide(self, message, context=None):
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime",
                raw_message=message.content,
            )

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"intent":"list_folders","params":{"path":"D:/","folders_only":true}}'

            return Response()

    class MockFileListTool(Tool):
        name = "file_list"
        description = "Mock file list tool"
        inputs = {"path": "string"}
        outputs = {"directories": "list"}

        async def execute(self, path: str = ".", folders_only: bool = False, **kwargs):
            return ExecutionResult.success(
                data={
                    "path": path,
                    "directories": ["Projects", "Temp"],
                    "files": ["notes.txt"],
                    "folders_only": folders_only,
                }
            )

    monkeypatch.setattr("localclaw.core.planner.get_llm_provider", lambda: FakeProvider())

    tool_registry = ToolRegistry()
    tool_registry.register(MockFileListTool())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=True, llm_parse_only=True),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=tool_registry,
        skill_registry=SkillRegistry(),
        openclaw_runtime=UnknownRuntime(),
    )

    task = await engine.process_message(Message(content="我d盘有哪些目录？", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "list_folders"
    assert task.intent.source == "planner_llm"
    step_id = task.plan.steps[0].id
    assert task.result.data[step_id]["path"] == "D:/"
    assert task.result.data[step_id]["directories"] == ["Projects", "Temp"]


@pytest.mark.asyncio
async def test_engine_local_model_can_check_drive_disk_space(monkeypatch):
    """Drive free-space questions should execute through the disk_usage tool."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = '{"mode":"intent","intent":"unknown","params":{}}'

            return Response()

    class MockDiskUsageTool(Tool):
        name = "disk_usage"
        description = "Mock disk usage tool"
        inputs = {"path": "string"}
        outputs = {"free_bytes": "integer", "total_bytes": "integer"}

        async def execute(self, path: str = ".", **kwargs):
            return ExecutionResult.success(
                data={
                    "path": path,
                    "free_bytes": 400,
                    "total_bytes": 1000,
                    "used_bytes": 600,
                    "free": "400 GB",
                    "used": "600 GB",
                    "total": "1.0 TB",
                    "free_percent": 40.0,
                    "used_percent": 60.0,
                }
            )

    monkeypatch.setattr("localclaw.core.openclaw_runtime.get_llm_provider", lambda: FakeProvider())

    tool_registry = ToolRegistry()
    tool_registry.register(MockDiskUsageTool())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=True, llm_parse_only=True),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=tool_registry,
        skill_registry=SkillRegistry(),
        openclaw_runtime=OpenClawRuntime(SkillRegistry(), tool_registry),
    )

    task = await engine.process_message(Message(content="我c盘空间还剩多少？", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "check_disk_space"
    step_id = task.plan.steps[0].id
    assert task.result.data[step_id]["path"] == "C:/"
    assert task.result.data[step_id]["free"] == "400 GB"
    assert task.result.data[step_id]["free_percent"] == 40.0


@pytest.mark.asyncio
async def test_engine_requires_local_model_when_llm_is_disabled():
    """Without an explicit parser override, disabling the local model should hard-fail."""

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=False, llm_parse_only=False),
        verifier=create_default_verifier(),
        tool_registry=ToolRegistry(),
        skill_registry=SkillRegistry(),
    )

    task = await engine.process_message(Message(content="help", user_id="u1", channel="test"))

    assert task.state == TaskState.FAILED
    assert task.error_type is not None
    assert task.error_type.value == "parse_error"
    assert task.error is not None
    assert "请先安装并启用本地大模型" in task.error


@pytest.mark.asyncio
async def test_engine_process_echo(engine):
    """Test processing an echo message."""
    message = Message(content="echo test message", user_id="test", channel="test")
    
    task = await engine.process_message(message)
    
    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "echo"


@pytest.mark.asyncio
async def test_engine_process_dsl(engine):
    """Test processing a DSL command."""
    message = Message(content="/hello name=test", user_id="test", channel="test")
    
    task = await engine.process_message(message)
    
    assert task.state == TaskState.COMPLETED
    assert task.intent is not None
    assert task.intent.intent == "skill.hello"


@pytest.mark.asyncio
async def test_engine_process_openclaw_skill_key():
    """Planner should resolve OpenClaw skill keys returned by the local model."""

    class OpenClawSkillParser:
        async def parse(self, message):
            return Intent(intent="skill.repo.fs", params={"path": "README.md"})

    registry = SkillRegistry()
    skill = create_skill_from_dict(
        {
            "name": "workspace_fs",
            "description": "Read a path",
            "inputs": {"path": "string"},
            "actions": [{"type": "transform", "template": "reading {{path}}"}],
            "metadata": {"skill_key": "repo.fs"},
        }
    )
    skill.enable()
    registry.register(skill)

    engine = ExecutionEngine(
        parser=OpenClawSkillParser(),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=ToolRegistry(),
        skill_registry=registry,
    )

    task = await engine.process_message(Message(content="readme", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    assert task.plan is not None
    assert task.plan.skill_name == "workspace_fs"
    step_id = task.plan.steps[0].id
    assert task.result.data[step_id]["result"] == "reading README.md"


@pytest.mark.asyncio
async def test_engine_executes_openclaw_style_fs_skill(tmp_path):
    """OpenClaw-style file workflow skills should execute with inputs, conditions, and tool names."""

    class FsSkillParser:
        async def parse(self, message):
            return Intent(intent="skill.fs", params={"action": "list", "path": str(tmp_path)})

    (tmp_path / "alpha.txt").write_text("demo", encoding="utf-8")
    skill_file = tmp_path / "fs.json"
    skill_file.write_text(
        """{
  "name": "fs",
  "version": "2.0.0",
  "description": "File system workflow",
  "type": "workflow",
  "inputs": {
    "action": "string",
    "path": "string",
    "all": "boolean",
    "long": "boolean"
  },
  "outputs": {
    "result": "string",
    "data": "object"
  },
  "actions": [
    {
      "type": "tool_call",
      "name": "file_list",
      "condition": "{{action == 'list'}}",
      "inputs": {
        "path": "{{path}}",
        "all": "{{all}}",
        "long": "{{long}}"
      },
      "outputs": {
        "result": "Directory listed successfully",
        "data": {
          "files": "{{files}}",
          "directories": "{{directories}}",
          "details": "{{details}}"
        }
      }
    }
  ]
}""",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)
    skill = loader.load_from_file(skill_file)
    assert skill is not None
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(FileListTool())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=False, llm_parse_only=False),
        parser=FsSkillParser(),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=tool_registry,
        skill_registry=registry,
    )

    task = await engine.process_message(Message(content="list", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    assert task.plan is not None
    assert any(step.type == StepType.CONDITION for step in task.plan.steps)
    assert any(step.status.value == "completed" for step in task.plan.steps)
    assert any(
        isinstance(step_result, dict) and "files" in step_result
        for step_result in task.result.data.values()
    )


@pytest.mark.asyncio
async def test_engine_task_history(engine):
    """Test task history tracking."""
    message = Message(content="help", user_id="test", channel="test")
    
    await engine.process_message(message)
    
    history = engine.get_task_history()
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_engine_approval_resume_flow():
    """High-risk steps should wait for approval and resume afterwards."""

    class ApprovalParser:
        async def parse(self, message):
            return Intent(intent="tool.shell", params={"command": "echo ok"})

    class MockShellTool(Tool):
        name = "shell"
        description = "Mock shell tool"
        inputs = {"command": "string"}
        outputs = {"stdout": "string"}

        async def execute(self, command: str, **kwargs):
            return ExecutionResult.success(data={"stdout": f"executed: {command}"})

    tool_registry = ToolRegistry()
    tool_registry.register(MockShellTool())

    engine = ExecutionEngine(
        parser=ApprovalParser(),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=tool_registry,
        skill_registry=SkillRegistry(),
    )

    task = await engine.process_message(Message(content="run shell", user_id="u1", channel="test"))

    assert task.state == TaskState.VERIFYING
    step_id = task.plan.steps[0].id

    resumed = await engine.approve_and_resume_step(task.id, step_id)

    assert resumed is not None
    assert resumed.state == TaskState.COMPLETED
    assert resumed.result.data[step_id]["stdout"] == "executed: echo ok"
    assert any(history_task.id == task.id for history_task in engine.get_task_history())


@pytest.mark.asyncio
async def test_engine_process_safe_command():
    """Routine commands should run through safe_shell without approval."""

    class SafeCommandParser:
        async def parse(self, message):
            return Intent(intent="run_command", params={"command": "git status"})

    class MockSafeShellTool(Tool):
        name = "safe_shell"
        description = "Mock safe shell tool"
        inputs = {"command": "string"}
        outputs = {"stdout": "string"}

        async def execute(self, command: str, **kwargs):
            return ExecutionResult.success(
                data={"stdout": f"safe: {command}", "exit_code": 0, "command": command}
            )

    tool_registry = ToolRegistry()
    tool_registry.register(MockSafeShellTool())

    engine = ExecutionEngine(
        parser=SafeCommandParser(),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=tool_registry,
        skill_registry=SkillRegistry(),
    )

    task = await engine.process_message(
        Message(content="执行命令 git status", user_id="u1", channel="test")
    )

    assert task.state == TaskState.COMPLETED
    step_id = task.plan.steps[0].id
    assert task.result.data[step_id]["stdout"] == "safe: git status"


@pytest.mark.asyncio
async def test_engine_blocks_post_install_protected_tool():
    """Skills installed with disable_high_risk protection should not run blocked tools."""

    class ProtectedSkillParser:
        async def parse(self, message):
            return Intent(intent="skill.protected_reader", params={})

    class MockFileReadTool(Tool):
        name = "file_read"
        description = "Mock file read tool"
        inputs = {"path": "string"}
        outputs = {"content": "string"}

        async def execute(self, path: str, **kwargs):
            return ExecutionResult.success(data={"content": "secret"})

    registry = SkillRegistry()
    skill = create_skill_from_dict(
        {
            "name": "protected_reader",
            "description": "Reads a file",
            "type": "workflow",
            "actions": [{"type": "tool_call", "tool": "file_read", "params": {"path": ".env"}}],
            "metadata": {
                "localclaw_guard": {
                    "mode": "disable_high_risk",
                    "blocked_tools": ["file_read"],
                    "approval_required_tools": [],
                }
            },
        }
    )
    skill.enable()
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(MockFileReadTool())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=False, llm_parse_only=False),
        parser=ProtectedSkillParser(),
        planner=create_default_planner(),
        verifier=create_default_verifier(settings=Settings(_env_file=None), skill_registry=registry),
        tool_registry=tool_registry,
        skill_registry=registry,
    )

    task = await engine.process_message(Message(content="read", user_id="u1", channel="test"))

    assert task.state == TaskState.FAILED
    assert task.error is not None
    assert "blocked by post-install protection" in task.error


@pytest.mark.asyncio
async def test_engine_allows_bundled_catalog_skill_even_with_legacy_guard():
    """Bundled catalog installs should not be blocked by legacy third-party guard metadata."""

    class BundledSkillParser:
        async def parse(self, message):
            return Intent(intent="skill.repo.fs", params={"action": "list", "path": "."})

    class MockFileListTool(Tool):
        name = "file_list"
        description = "Mock file list tool"
        inputs = {"path": "string"}
        outputs = {"directories": "list", "files": "list"}

        async def execute(self, path: str = ".", **kwargs):
            return ExecutionResult.success(data={"path": path, "directories": ["src"], "files": ["README.md"]})

    registry = SkillRegistry()
    skill = create_skill_from_dict(
        {
            "name": "repo.fs",
            "description": "Bundled repo fs helper",
            "type": "workflow",
            "inputs": {"action": "string", "path": "string"},
            "actions": [
                {
                    "type": "tool_call",
                    "tool": "file_list",
                    "condition": "{{action == 'list'}}",
                    "params": {"path": "{{path}}"},
                }
            ],
            "metadata": {
                "catalog_source": "bundled",
                "localclaw_guard": {
                    "mode": "disable_high_risk",
                    "blocked_tools": ["file_list"],
                    "approval_required_tools": [],
                },
            },
        }
    )
    skill.enable()
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(MockFileListTool())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=False, llm_parse_only=False),
        parser=BundledSkillParser(),
        planner=create_default_planner(),
        verifier=create_default_verifier(settings=Settings(_env_file=None), skill_registry=registry),
        tool_registry=tool_registry,
        skill_registry=registry,
    )

    task = await engine.process_message(Message(content="list repo", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    step_id = next(iter(task.result.data.keys()))
    assert task.result.data[step_id]["directories"] == ["src"]


@pytest.mark.asyncio
async def test_engine_isolated_skill_requires_approval_then_resumes():
    """Isolated skills should require approval before protected tool execution."""

    class IsolatedSkillParser:
        async def parse(self, message):
            return Intent(intent="skill.isolated_reader", params={})

    class MockFileReadTool(Tool):
        name = "file_read"
        description = "Mock file read tool"
        inputs = {"path": "string"}
        outputs = {"content": "string"}

        async def execute(self, path: str, **kwargs):
            return ExecutionResult.success(data={"content": "ok", "path": path})

    registry = SkillRegistry()
    skill = create_skill_from_dict(
        {
            "name": "isolated_reader",
            "description": "Reads a file in isolate mode",
            "type": "workflow",
            "actions": [{"type": "tool_call", "tool": "file_read", "params": {"path": "README.md"}}],
            "metadata": {
                "localclaw_guard": {
                    "mode": "isolate",
                    "blocked_tools": [],
                    "approval_required_tools": ["file_read"],
                }
            },
        }
    )
    skill.enable()
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(MockFileReadTool())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=False, llm_parse_only=False),
        parser=IsolatedSkillParser(),
        planner=create_default_planner(),
        verifier=create_default_verifier(settings=Settings(_env_file=None), skill_registry=registry),
        tool_registry=tool_registry,
        skill_registry=registry,
    )

    task = await engine.process_message(Message(content="read", user_id="u1", channel="test"))

    assert task.state == TaskState.VERIFYING
    step_id = task.plan.steps[0].id

    resumed = await engine.approve_and_resume_step(task.id, step_id)

    assert resumed is not None
    assert resumed.state == TaskState.COMPLETED
    assert resumed.result.data[step_id]["content"] == "ok"


@pytest.mark.asyncio
async def test_engine_executes_internal_local_model_prompt_skill():
    """Installed skills should be able to call the internal local model prompt tool."""

    class PromptSkillParser:
        async def parse(self, message):
            return Intent(intent="skill.humanizer", params={"text": "This is robotic.", "tone": "friendly"})

    class FakeProvider(LLMProvider):
        def __init__(self):
            super().__init__(LLMConfig(provider_type=LLMProviderType.MOCK, model="fake-local"))

        async def generate(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
            from localclaw.llm.provider import LLMResponse

            assert "This is robotic." in prompt
            assert "friendly" in prompt
            return LLMResponse(content="This sounds much more natural.", model="fake-local", provider="mock")

        async def chat(self, messages, max_tokens=None, temperature=None):
            from localclaw.llm.provider import LLMResponse

            return LLMResponse(content="unused", model="fake-local", provider="mock")

        async def is_available(self):
            return True

    set_llm_provider(FakeProvider())

    registry = SkillRegistry()
    skill = create_skill_from_dict(
        {
            "name": "humanizer",
            "description": "Rewrite text naturally",
            "type": "workflow",
            "inputs": {"text": "string", "tone": "string"},
            "actions": [
                {
                    "type": "tool_call",
                    "tool": "_local_model_prompt",
                    "params": {
                        "prompt": "Rewrite this text in a {{tone}} tone:\n\n{{text}}",
                        "max_tokens": 300,
                    },
                }
            ],
        }
    )
    skill.enable()
    registry.register(skill)

    tool_registry = ToolRegistry()
    tool_registry.register(LocalModelPromptTool())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=True, llm_parse_only=False),
        parser=PromptSkillParser(),
        planner=create_default_planner(),
        verifier=create_default_verifier(),
        tool_registry=tool_registry,
        skill_registry=registry,
    )

    task = await engine.process_message(Message(content="humanize", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    step_id = task.plan.steps[0].id
    assert task.result.data[step_id]["content"] == "This sounds much more natural."

"""Tests for the engine module."""

import pytest

from localclaw.core.engine import ExecutionEngine, get_engine
from localclaw.core.models import Message, TaskState


@pytest.fixture
def engine():
    """Create an engine instance for testing."""
    from localclaw.tools.base import get_tool_registry
    from localclaw.skills.loader import register_builtin_skills
    
    registry = get_tool_registry()
    
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
    
    register_builtin_skills()
    
    return get_engine()


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
async def test_engine_task_history(engine):
    """Test task history tracking."""
    message = Message(content="help", user_id="test", channel="test")
    
    await engine.process_message(message)
    
    history = engine.get_task_history()
    assert len(history) >= 1

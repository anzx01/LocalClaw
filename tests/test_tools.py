"""Tests for tools."""

import pytest

from localclaw.core.models import RiskLevel
from localclaw.tools.base import Tool, ToolRegistry, ToolError
from localclaw.core.models import ExecutionResult, ErrorType


class MockTool(Tool):
    """Mock tool for testing."""
    
    name = "mock_tool"
    description = "A mock tool for testing"
    risk_level = RiskLevel.LOW
    inputs = {"input": "string"}
    outputs = {"output": "string"}
    
    async def execute(self, input: str, **kwargs):
        return ExecutionResult.success(
            message=f"Processed: {input}",
            data={"output": input.upper()},
        )


class FailingTool(Tool):
    """Tool that always fails."""
    
    name = "failing_tool"
    description = "A tool that always fails"
    inputs = {}
    outputs = {}
    
    async def execute(self, **kwargs):
        raise ToolError("Intentional failure", ErrorType.TOOL_ERROR)


@pytest.mark.asyncio
async def test_tool_execute():
    """Test tool execution."""
    tool = MockTool()
    result = await tool.run(input="test")
    
    assert result.status == "success"
    assert result.data["output"] == "TEST"


@pytest.mark.asyncio
async def test_tool_validation():
    """Test tool input validation."""
    tool = MockTool()
    result = await tool.run()
    
    assert result.status == "error"
    assert "Missing required parameter" in result.message


@pytest.mark.asyncio
async def test_tool_error():
    """Test tool error handling."""
    tool = FailingTool()
    result = await tool.run()
    
    assert result.status == "error"
    assert "Intentional failure" in result.message


def test_tool_registry():
    """Test tool registry."""
    registry = ToolRegistry()
    tool = MockTool()
    
    registry.register(tool)
    
    assert registry.get("mock_tool") is tool
    assert "mock_tool" in registry.list_tools()
    
    registry.unregister("mock_tool")
    assert registry.get("mock_tool") is None


@pytest.mark.asyncio
async def test_registry_execute():
    """Test registry execute method."""
    registry = ToolRegistry()
    tool = MockTool()
    registry.register(tool)
    
    result = await registry.execute("mock_tool", input="hello")
    
    assert result.status == "success"
    assert result.data["output"] == "HELLO"


@pytest.mark.asyncio
async def test_registry_execute_not_found():
    """Test registry execute with non-existent tool."""
    registry = ToolRegistry()
    
    result = await registry.execute("nonexistent")
    
    assert result.status == "error"
    assert "not found" in result.message.lower()

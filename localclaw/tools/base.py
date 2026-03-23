"""Base Tool class and registry."""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, ClassVar, Dict, List, Optional, Type

from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel


logger = logging.getLogger(__name__)


class ToolError(Exception):
    """Exception raised by tool execution."""
    
    def __init__(self, message: str, error_type: ErrorType = ErrorType.TOOL_ERROR) -> None:
        super().__init__(message)
        self.error_type = error_type


class Tool(ABC):
    """Abstract base class for all tools."""
    
    name: ClassVar[str] = "base_tool"
    description: ClassVar[str] = "Base tool class"
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    inputs: ClassVar[Dict[str, str]] = {}
    outputs: ClassVar[Dict[str, str]] = {}
    
    def __init__(self) -> None:
        self._logger = logging.getLogger(f"localclaw.tools.{self.name}")
    
    @abstractmethod
    async def execute(self, **kwargs: Any) -> ExecutionResult:
        """Execute the tool with given parameters."""
        pass
    
    def validate_inputs(self, kwargs: Dict[str, Any]) -> List[str]:
        """Validate input parameters against declared inputs schema."""
        errors: List[str] = []
        
        for key, expected_type in self.inputs.items():
            if key not in kwargs:
                errors.append(f"Missing required parameter: {key}")
                continue
            
            type_map = {
                "string": str,
                "integer": int,
                "float": (int, float),
                "boolean": bool,
                "list": list,
                "dict": dict,
            }
            
            expected_python_type = type_map.get(expected_type, str)
            if not isinstance(kwargs[key], expected_python_type):
                errors.append(f"Parameter '{key}' has wrong type")
        
        return errors
    
    async def run(self, **kwargs: Any) -> ExecutionResult:
        """Run the tool with validation and logging."""
        self._logger.info(f"Tool '{self.name}' starting execution", extra={"inputs": kwargs})
        
        errors = self.validate_inputs(kwargs)
        if errors:
            self._logger.warning(f"Tool '{self.name}' validation failed", extra={"errors": errors})
            return ExecutionResult.from_error(
                message=f"Validation failed: {'; '.join(errors)}",
                error_type=ErrorType.VALIDATION_ERROR,
            )
        
        try:
            result = await self.execute(**kwargs)
            self._logger.info(f"Tool '{self.name}' completed", extra={"status": result.status})
            return result
        except ToolError as e:
            self._logger.error(f"Tool '{self.name}' error: {e}", extra={"error_type": e.error_type})
            return ExecutionResult.from_error(message=str(e), error_type=e.error_type)
        except asyncio.TimeoutError:
            self._logger.error(f"Tool '{self.name}' timeout")
            return ExecutionResult.from_error(message="Operation timed out", error_type=ErrorType.TIMEOUT_ERROR)
        except Exception as e:
            self._logger.error(f"Tool '{self.name}' unexpected error: {e}")
            return ExecutionResult.from_error(message=str(e), error_type=ErrorType.SYSTEM_ERROR)
    
    def get_info(self) -> Dict[str, Any]:
        """Get tool information."""
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "inputs": self.inputs,
            "outputs": self.outputs,
        }


class ToolRegistry:
    """Registry for managing tools."""
    
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self._logger = logging.getLogger("localclaw.tools.registry")
    
    def register(self, tool: Tool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool
        self._logger.info(f"Registered tool: {tool.name}")
    
    def register_class(self, tool_class: Type[Tool]) -> None:
        """Register a tool class by instantiating it."""
        tool = tool_class()
        self.register(tool)
    
    def unregister(self, name: str) -> bool:
        """Unregister a tool by name."""
        if name in self._tools:
            del self._tools[name]
            self._logger.info(f"Unregistered tool: {name}")
            return True
        return False
    
    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())
    
    def get_all_info(self) -> List[Dict[str, Any]]:
        """Get information about all registered tools."""
        return [tool.get_info() for tool in self._tools.values()]
    
    async def execute(self, name: str, **kwargs: Any) -> ExecutionResult:
        """Execute a tool by name."""
        tool = self.get(name)
        if tool is None:
            return ExecutionResult.from_error(
                message=f"Tool not found: {name}",
                error_type=ErrorType.VALIDATION_ERROR,
            )
        return await tool.run(**kwargs)


_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get the global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def register_tool(tool: Tool) -> None:
    """Register a tool in the global registry."""
    get_tool_registry().register(tool)


def get_tool(name: str) -> Optional[Tool]:
    """Get a tool from the global registry."""
    return get_tool_registry().get(name)

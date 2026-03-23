"""Sandbox execution for isolated skill/tool execution."""

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel


logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution."""
    enabled: bool = True
    timeout: float = 30.0
    max_memory_mb: int = 256
    max_output_size: int = 1024 * 1024
    allowed_imports: list = field(default_factory=lambda: [
        "json", "re", "datetime", "math", "random", "string",
        "collections", "itertools", "functools", "typing",
    ])
    forbidden_functions: list = field(default_factory=lambda: [
        "eval", "exec", "compile", "open", "input",
        "__import__", "globals", "locals", "vars",
    ])


class SandboxExecutor:
    """Executes code in a sandboxed environment."""
    
    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        self._config = config or SandboxConfig()
        self._logger = logging.getLogger("localclaw.security.sandbox")
    
    async def execute_python(
        self,
        code: str,
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> ExecutionResult:
        """Execute Python code in a sandbox."""
        if not self._config.enabled:
            return await self._execute_direct(code, context)
        
        timeout = timeout or self._config.timeout
        
        try:
            result = await asyncio.wait_for(
                self._execute_in_subprocess(code, context or {}),
                timeout=timeout,
            )
            return result
        except asyncio.TimeoutError:
            return ExecutionResult.from_error(
                "Sandbox execution timed out",
                ErrorType.TIMEOUT_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(
                f"Sandbox error: {e}",
                ErrorType.SYSTEM_ERROR,
            )
    
    async def _execute_in_subprocess(
        self,
        code: str,
        context: Dict[str, Any],
    ) -> ExecutionResult:
        """Execute code in a subprocess for isolation."""
        context_json = json.dumps(context)
        
        sandbox_script = f'''
import sys
import json
import resource

def limit_memory():
    try:
        resource.setrlimit(resource.RLIMIT_AS, ({self._config.max_memory_mb * 1024 * 1024}, {self._config.max_memory_mb * 1024 * 1024}))
    except:
        pass

limit_memory()

context = json.loads(sys.argv[1])
result = {{}}

try:
    exec_globals = {{
        "__builtins__": {{
            k: __builtins__[k] for k in __builtins__
            if k not in {self._config.forbidden_functions}
        }}
    }}
    exec_globals.update(context)
    exec_locals = {{}}
    
    exec("""{code}""", exec_globals, exec_locals)
    
    result = {{
        "status": "success",
        "data": exec_locals.get("result", exec_locals),
    }}
except Exception as e:
    result = {{
        "status": "error",
        "error": str(e),
    }}

print(json.dumps(result))
'''
        
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(sandbox_script)
            script_path = f.name
        
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                script_path,
                context_json,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return ExecutionResult.from_error(
                    f"Sandbox process failed: {stderr.decode()}",
                    ErrorType.SYSTEM_ERROR,
                )
            
            output = stdout.decode()
            
            if len(output) > self._config.max_output_size:
                output = output[:self._config.max_output_size]
            
            try:
                result_data = json.loads(output)
                if result_data.get("status") == "success":
                    return ExecutionResult.success(
                        message="Sandbox execution completed",
                        data=result_data.get("data", {}),
                    )
                else:
                    return ExecutionResult.from_error(
                        result_data.get("error", "Unknown sandbox error"),
                        ErrorType.SYSTEM_ERROR,
                    )
            except json.JSONDecodeError:
                return ExecutionResult.from_error(
                    "Invalid sandbox output",
                    ErrorType.SYSTEM_ERROR,
                )
        finally:
            Path(script_path).unlink(missing_ok=True)
    
    async def _execute_direct(
        self,
        code: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """Execute code directly (no sandbox)."""
        try:
            exec_globals = {"__builtins__": __builtins__}
            if context:
                exec_globals.update(context)
            
            exec_locals: Dict[str, Any] = {}
            exec(code, exec_globals, exec_locals)
            
            return ExecutionResult.success(
                message="Direct execution completed",
                data=exec_locals.get("result", exec_locals),
            )
        except Exception as e:
            return ExecutionResult.from_error(
                f"Execution error: {e}",
                ErrorType.SYSTEM_ERROR,
            )
    
    def validate_code(self, code: str) -> tuple[bool, str]:
        """Validate code for safety."""
        import ast
        
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module not in self._config.allowed_imports:
                        return False, f"Import not allowed: {module}"
            
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    if module not in self._config.allowed_imports:
                        return False, f"Import not allowed: {module}"
            
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in self._config.forbidden_functions:
                        return False, f"Function not allowed: {node.func.id}"
        
        return True, ""


_executor: Optional[SandboxExecutor] = None


def get_sandbox_executor() -> SandboxExecutor:
    """Get the global sandbox executor."""
    global _executor
    if _executor is None:
        _executor = SandboxExecutor()
    return _executor

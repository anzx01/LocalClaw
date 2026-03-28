"""Shell command execution tool."""

import asyncio
import platform
import shlex
from typing import Any, ClassVar, List, Optional

from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel
from localclaw.tools.base import Tool, ToolError, register_tool


class ShellTool(Tool):
    """Tool for executing shell commands."""
    
    name: ClassVar[str] = "shell"
    description: ClassVar[str] = "Execute shell commands"
    risk_level: ClassVar[RiskLevel] = RiskLevel.CRITICAL
    inputs: ClassVar[dict] = {"command": "string"}
    outputs: ClassVar[dict] = {"stdout": "string", "stderr": "string", "exit_code": "integer"}
    
    BLOCKED_COMMANDS = {
        "rm -rf /",
        "mkfs",
        "dd if=",
        ":(){ :|:& };:",
        "chmod -R 777 /",
        "chown -R",
        "> /dev/sda",
        "mv /* /dev/null",
    }
    
    def __init__(
        self,
        allowed_commands: Optional[List[str]] = None,
        blocked_commands: Optional[List[str]] = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__()
        self._allowed_commands = set(allowed_commands) if allowed_commands else None
        self._blocked_commands = set(blocked_commands) if blocked_commands else self.BLOCKED_COMMANDS
        self._timeout = timeout
    
    def _validate_command(self, command: str) -> None:
        """Validate the command for security."""
        try:
            cmd_parts = shlex.split(command, posix=platform.system() != "Windows")
        except ValueError:
            cmd_parts = command.strip().split()

        if self._blocked_commands and cmd_parts:
            base_cmd = cmd_parts[0].lower()
            for blocked in self._blocked_commands:
                blocked_parts = blocked.lower().split()
                if not blocked_parts:
                    continue
                # Match on base command token first, then check remaining tokens
                if base_cmd == blocked_parts[0] and all(
                    p in [t.lower() for t in cmd_parts] for p in blocked_parts[1:]
                ):
                    raise ToolError(f"Blocked command detected: {blocked}", ErrorType.PERMISSION_ERROR)

        if self._allowed_commands is not None:
            if cmd_parts:
                base_cmd = cmd_parts[0]
                if base_cmd not in self._allowed_commands:
                    raise ToolError(
                        f"Command not in allowed list: {base_cmd}",
                        ErrorType.PERMISSION_ERROR,
                    )
    
    async def execute(
        self,
        command: str,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Execute a shell command."""
        try:
            self._validate_command(command)
            
            actual_timeout = timeout or self._timeout
            
            if platform.system() == "Windows":
                shell_args = ["cmd", "/c", command]
            else:
                shell_args = ["sh", "-c", command]
            
            process = await asyncio.create_subprocess_exec(
                *shell_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=actual_timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise ToolError(
                    f"Command timed out after {actual_timeout}s",
                    ErrorType.TIMEOUT_ERROR,
                )
            
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")
            
            return ExecutionResult.success(
                message=f"Command executed with exit code {process.returncode}",
                data={
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "exit_code": process.returncode,
                    "command": command,
                },
            )
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Failed to execute command: {e}", ErrorType.TOOL_ERROR)


class SafeShellTool(Tool):
    """A safer shell tool with restricted command set."""
    
    name: ClassVar[str] = "safe_shell"
    description: ClassVar[str] = "Execute safe shell commands (restricted set)"
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    inputs: ClassVar[dict] = {"command": "string"}
    outputs: ClassVar[dict] = {"stdout": "string", "stderr": "string", "exit_code": "integer"}
    
    SAFE_COMMANDS = {
        "echo", "pwd", "whoami", "date", "hostname", "uname",
        "ls", "dir", "cat", "type", "head", "tail", "wc", "grep", "findstr",
        "python", "python3", "pip", "pip3", "pytest", "uv", "poetry",
        "git", "npm", "npx", "pnpm", "yarn", "node",
        "ruff", "mypy", "powershell", "pwsh",
    }
    
    def __init__(self, timeout: float = 30.0) -> None:
        super().__init__()
        self._shell_tool = ShellTool(
            allowed_commands=list(self.SAFE_COMMANDS),
            timeout=timeout,
        )
    
    async def execute(self, command: str, **kwargs: Any) -> ExecutionResult:
        """Execute a safe shell command."""
        return await self._shell_tool.execute(command, **kwargs)


def register_shell_tools(timeout: float = 30.0) -> None:
    """Register shell tools."""
    register_tool(ShellTool(timeout=timeout))
    register_tool(SafeShellTool(timeout=timeout))

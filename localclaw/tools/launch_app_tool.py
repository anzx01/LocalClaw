"""Desktop application launcher tool."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, ClassVar, Dict, List
from urllib.parse import urlparse

from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel
from localclaw.tools.base import Tool, ToolError, register_tool


class LaunchAppTool(Tool):
    """Launch a desktop app by alias, executable name, path, or URL."""

    name: ClassVar[str] = "launch_app"
    description: ClassVar[str] = "Launch a desktop app by alias, executable name, path, or URL"
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    inputs: ClassVar[Dict[str, str]] = {"target": "string"}
    outputs: ClassVar[Dict[str, str]] = {
        "target": "string",
        "resolved_target": "string",
        "method": "string",
    }

    _WINDOWS_ALIASES: ClassVar[Dict[str, List[str]]] = {
        "code": ["Code.exe", "code"],
        "vscode": ["Code.exe", "code"],
        "vs code": ["Code.exe", "code"],
        "visual studio code": ["Code.exe", "code"],
        "visual studio": ["devenv.exe"],
        "vs": ["devenv.exe", "Code.exe", "code"],
        "claude code": ["claude.exe", "claude"],
        "notepad": ["notepad.exe"],
        "chrome": ["chrome.exe"],
        "google chrome": ["chrome.exe"],
        "edge": ["msedge.exe"],
        "microsoft edge": ["msedge.exe"],
        "word": ["WINWORD.EXE"],
        "excel": ["EXCEL.EXE"],
        "powerpoint": ["POWERPNT.EXE"],
        "wps": ["wps.exe"],
        "wps office": ["wps.exe"],
        "cmd": ["cmd.exe"],
        "powershell": ["powershell.exe", "pwsh.exe"],
        "terminal": ["powershell.exe", "cmd.exe"],
    }
    _POSIX_ALIASES: ClassVar[Dict[str, List[str]]] = {
        "code": ["code"],
        "vscode": ["code"],
        "vs code": ["code"],
        "claude code": ["claude"],
        "chrome": ["google-chrome", "chromium", "chrome"],
        "edge": ["microsoft-edge", "msedge"],
        "terminal": ["x-terminal-emulator", "gnome-terminal", "konsole"],
    }

    async def execute(
        self,
        target: str,
        args: Any = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Launch the requested target with optional arguments."""

        del kwargs

        normalized_target = str(target or "").strip()
        if not normalized_target:
            raise ToolError("Missing required parameter: target", ErrorType.VALIDATION_ERROR)

        normalized_args = self._normalize_args(args)
        resolved_target = self._resolve_target(normalized_target)
        launch_method = self._detect_launch_method()
        command = self._build_launch_command(launch_method, resolved_target, normalized_args)

        try:
            process = subprocess.Popen(
                command,
                cwd=cwd or None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ToolError(
                f"Unable to launch '{normalized_target}'. Resolved target '{resolved_target}' was not found.",
                ErrorType.TOOL_ERROR,
            ) from exc
        except OSError as exc:
            raise ToolError(f"Failed to launch '{normalized_target}': {exc}", ErrorType.TOOL_ERROR) from exc

        message = f"Launched {normalized_target}"
        return ExecutionResult.success(
            message=message,
            data={
                "result": message,
                "message": message,
                "target": normalized_target,
                "resolved_target": resolved_target,
                "args": normalized_args,
                "method": launch_method,
                "launcher_pid": process.pid,
            },
        )

    def _resolve_target(self, target: str) -> str:
        """Resolve a user-facing target into an executable, path, or URL."""

        if self._looks_like_url(target):
            return target

        target_path = Path(target).expanduser()
        if target_path.exists():
            return str(target_path.resolve())

        candidates = self._candidate_targets(target)
        for candidate in candidates:
            if self._looks_like_url(candidate):
                return candidate

            candidate_path = Path(candidate).expanduser()
            if candidate_path.exists():
                return str(candidate_path.resolve())

            resolved = shutil.which(candidate)
            if resolved:
                return resolved

        return candidates[0] if candidates else target

    def _candidate_targets(self, target: str) -> List[str]:
        """Return launch candidates in priority order."""

        normalized = target.strip()
        lowered = normalized.lower()
        aliases = self._WINDOWS_ALIASES if platform.system() == "Windows" else self._POSIX_ALIASES
        candidates = aliases.get(lowered, [])

        ordered: List[str] = []
        for candidate in [*candidates, normalized]:
            if candidate not in ordered:
                ordered.append(candidate)
        return ordered

    @staticmethod
    def _normalize_args(args: Any) -> List[str]:
        """Normalize optional launch arguments into a flat string list."""

        if args is None:
            return []
        if isinstance(args, str):
            stripped = args.strip()
            return [stripped] if stripped else []
        if isinstance(args, (list, tuple)):
            normalized = [str(item).strip() for item in args if str(item).strip()]
            return normalized
        return [str(args).strip()] if str(args).strip() else []

    @staticmethod
    def _looks_like_url(target: str) -> bool:
        """Return True when the target is a URL."""

        parsed = urlparse(target)
        return parsed.scheme in {"http", "https"}

    @staticmethod
    def _detect_launch_method() -> str:
        """Return the platform-specific launch method."""

        if platform.system() == "Windows":
            return "cmd_start"
        if platform.system() == "Darwin":
            return "open"
        return "xdg_open"

    def _build_launch_command(self, method: str, target: str, args: List[str]) -> List[str]:
        """Build the actual process command for the current platform."""

        if method == "cmd_start":
            return ["cmd", "/c", "start", "", target, *args]
        if method == "open":
            return ["open", target, *args]
        return ["xdg-open", target, *args]


def register_launch_app_tools() -> None:
    """Register desktop app launcher tools."""

    register_tool(LaunchAppTool())

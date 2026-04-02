"""System information tools shared by CLI and web channels."""

from typing import Any

from localclaw.config.settings import get_settings
from localclaw.core.models import ExecutionResult
from localclaw.tools.base import Tool, register_tool


class SystemStatusTool(Tool):
    """Tool for getting system status."""

    name = "system_status"
    description = "Get system status information"
    inputs = {}
    outputs = {"status": "string", "version": "string"}

    async def execute(self, **kwargs: Any) -> ExecutionResult:
        from localclaw import __version__

        settings = get_settings()
        return ExecutionResult.success(
            message="System status",
            data={
                "status": "running",
                "version": __version__,
                "mode": settings.mode.value,
                "model_provider": settings.model_provider.value,
                "model_name": settings.model_name,
            },
        )


class ListSkillsTool(Tool):
    """Tool for listing skills."""

    name = "list_skills"
    description = "List all available skills"
    inputs = {}
    outputs = {"skills": "list"}

    async def execute(self, **kwargs: Any) -> ExecutionResult:
        from localclaw.skills.registry import get_skill_registry

        skill_info = get_skill_registry().get_all_info()
        available = [s["name"] for s in skill_info if s.get("availability") == "available"]
        blocked = [s["name"] for s in skill_info if s.get("availability") == "blocked"]

        response = "当前可用能力：\n"
        response += "- 问候、回显、日期和天气查询\n"
        response += "- 文件、HTTP 和命令行工具（高风险操作需要审批）\n"
        response += f"- 已加载技能 {len(skill_info)} 个，可用 {len(available)} 个"
        if blocked:
            response += f"，受限 {len(blocked)} 个"

        return ExecutionResult.success(
            message="已列出技能",
            data={"skills": skill_info, "result": response},
        )


def register_system_tools() -> None:
    """Register system tools."""
    register_tool(SystemStatusTool())
    register_tool(ListSkillsTool())

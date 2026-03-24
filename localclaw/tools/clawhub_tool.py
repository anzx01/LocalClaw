"""ClawHub tool for skill management."""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel
from localclaw.tools.base import Tool, register_tool
from localclaw.skills.registry.clawhub import get_clawhub_client, get_local_registry
from localclaw.skills.loader import SkillLoader
from localclaw.skills.registry.registry import get_skill_registry


logger = logging.getLogger(__name__)


class ClawHubSearchTool(Tool):
    """Tool for searching skills in ClawHub."""

    name = "clawhub_search"
    description = "Search for skills in ClawHub"
    risk_level = RiskLevel.LOW
    inputs = {"query": "string"}
    outputs = {"skills": "list"}

    async def execute(self, query: str = "", category: Optional[str] = None, **kwargs) -> ExecutionResult:
        """Execute ClawHub search."""
        try:
            client = get_clawhub_client()
            skills = await client.search_skills(query, category)
            await client.close()

            return ExecutionResult.success(
                message="Skills search completed",
                data={"skills": skills},
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)


class ClawHubInstallTool(Tool):
    """Tool for installing skills from ClawHub."""

    name = "clawhub_install"
    description = "Install a skill from ClawHub"
    risk_level = RiskLevel.MEDIUM
    inputs = {"skill_id": "string"}
    outputs = {"installed": "boolean", "skill_path": "string"}

    async def execute(self, skill_id: str, **kwargs) -> ExecutionResult:
        """Execute ClawHub install."""
        try:
            local_registry = get_local_registry()
            if local_registry.is_skill_installed(skill_id):
                return ExecutionResult.from_error(
                    f"Skill {skill_id} is already installed",
                    ErrorType.VALIDATION_ERROR,
                )

            client = get_clawhub_client()
            success = await client.download_skill(skill_id, local_registry.skills_dir)
            await client.close()

            if success:
                # Load the skill into the registry
                skill_path = local_registry.get_skill_path(skill_id)
                loader = SkillLoader()
                skills = loader.load_from_directory(skill_path, recursive=True)
                if skills:
                    for skill in skills:
                        availability = skill.get_definition().metadata.get("availability", {})
                        get_skill_registry().register(skill, enable=availability.get("status") != "blocked")
                    return ExecutionResult.success(
                        message=f"Skill {skill_id} installed successfully",
                        data={"installed": True, "skill_path": str(skill_path)},
                    )
                else:
                    return ExecutionResult.from_error(
                        f"Failed to load skill {skill_id}",
                        ErrorType.SYSTEM_ERROR,
                    )
            else:
                return ExecutionResult.from_error(
                    f"Failed to download skill {skill_id}",
                    ErrorType.SYSTEM_ERROR,
                )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)


class ClawHubRemoveTool(Tool):
    """Tool for removing installed skills."""

    name = "clawhub_remove"
    description = "Remove an installed skill"
    risk_level = RiskLevel.MEDIUM
    inputs = {"skill_id": "string"}
    outputs = {"removed": "boolean"}

    async def execute(self, skill_id: str, **kwargs) -> ExecutionResult:
        """Execute ClawHub remove."""
        try:
            local_registry = get_local_registry()
            if not local_registry.is_skill_installed(skill_id):
                return ExecutionResult.from_error(
                    f"Skill {skill_id} is not installed",
                    ErrorType.VALIDATION_ERROR,
                )

            # Unregister the skill
            get_skill_registry().unregister(skill_id)

            # Remove the skill directory
            removed = local_registry.remove_skill(skill_id)

            if removed:
                return ExecutionResult.success(
                    message=f"Skill {skill_id} removed successfully",
                    data={"removed": True},
                )
            else:
                return ExecutionResult.from_error(
                    f"Failed to remove skill {skill_id}",
                    ErrorType.SYSTEM_ERROR,
                )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)


class ClawHubListTool(Tool):
    """Tool for listing locally installed skills."""

    name = "clawhub_list"
    description = "List locally installed skills"
    risk_level = RiskLevel.LOW
    inputs = {}
    outputs = {"skills": "list"}

    async def execute(self, **kwargs) -> ExecutionResult:
        """Execute ClawHub list."""
        try:
            local_registry = get_local_registry()
            skills = local_registry.list_local_skills()

            return ExecutionResult.success(
                message="Local skills listed",
                data={"skills": skills},
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)


def register_clawhub_tools() -> None:
    """Register ClawHub tools."""
    register_tool(ClawHubSearchTool())
    register_tool(ClawHubInstallTool())
    register_tool(ClawHubRemoveTool())
    register_tool(ClawHubListTool())

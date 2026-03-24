"""ClawHub tool for skill management."""

import logging
from typing import Dict, List, Optional, Any

from localclaw.config.settings import get_settings
from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel
from localclaw.skills.security_review import (
    apply_post_install_guard,
    build_post_install_guard,
    review_skill_installation,
)
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


class ClawHubScanTool(Tool):
    """Tool for performing a safety review before skill installation."""

    name = "clawhub_scan"
    description = "Review a ClawHub skill for risky installation patterns"
    risk_level = RiskLevel.LOW
    inputs = {"skill_id": "string"}
    outputs = {"scan": "dict"}

    async def execute(self, skill_id: str, **kwargs) -> ExecutionResult:
        """Execute a pre-installation security scan."""
        try:
            client = get_clawhub_client()
            detail = await client.get_skill_detail(skill_id)
            bundle = await client.fetch_skill_bundle(skill_id)
            await client.close()

            if detail is None and bundle is None:
                return ExecutionResult.from_error(
                    f"Failed to fetch skill metadata for {skill_id}",
                    ErrorType.SYSTEM_ERROR,
                )

            scan = review_skill_installation(skill_id=skill_id, detail=detail, bundle=bundle)
            return ExecutionResult.success(
                message="Skill security review completed",
                data={"scan": scan},
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)


class ClawHubInstallTool(Tool):
    """Tool for installing skills from ClawHub."""

    name = "clawhub_install"
    description = "Install a skill from ClawHub"
    risk_level = RiskLevel.MEDIUM
    inputs = {"skill_id": "string"}
    outputs = {"installed": "boolean", "skill_path": "string", "scan": "dict"}

    async def execute(self, skill_id: str, decision: Optional[str] = None, **kwargs) -> ExecutionResult:
        """Execute ClawHub install."""
        try:
            local_registry = get_local_registry()
            if local_registry.is_skill_installed(skill_id):
                return ExecutionResult.from_error(
                    f"Skill {skill_id} is already installed",
                    ErrorType.VALIDATION_ERROR,
                )

            client = get_clawhub_client()
            detail = await client.get_skill_detail(skill_id)
            bundle = await client.fetch_skill_bundle(skill_id)
            if detail is None and bundle is None:
                await client.close()
                return ExecutionResult.from_error(
                    f"Failed to fetch skill metadata for {skill_id}",
                    ErrorType.SYSTEM_ERROR,
                )
            scan = review_skill_installation(skill_id=skill_id, detail=detail, bundle=bundle)

            normalized_decision = (decision or "").strip().lower()
            if normalized_decision != "proceed":
                await client.close()
                return ExecutionResult.from_error(
                    f"Skill {skill_id} requires a security review decision before installation",
                    ErrorType.VALIDATION_ERROR,
                    data={
                        "installed": False,
                        "requires_review": True,
                        "scan": scan,
                        "allowed_decisions": ["cancel", "proceed"],
                    },
                )

            if bundle is None:
                await client.close()
                return ExecutionResult.from_error(
                    f"Failed to download skill {skill_id}",
                    ErrorType.SYSTEM_ERROR,
                    data={"scan": scan},
                )

            settings = get_settings()
            guard = build_post_install_guard(
                bundle=bundle,
                scan=scan,
                protection_mode=settings.skill_install_protection_mode.value,
                isolation_require_approval=settings.skill_isolation_require_approval,
                isolation_block_critical=settings.skill_isolation_block_critical,
            )
            protected_bundle = apply_post_install_guard(bundle, guard)

            saved = client.save_skill_bundle(skill_id, protected_bundle, local_registry.skills_dir)
            await client.close()
            if not saved:
                return ExecutionResult.from_error(
                    f"Failed to save skill {skill_id}",
                    ErrorType.SYSTEM_ERROR,
                    data={"scan": scan, "guard": guard},
                )

            skill_path = local_registry.get_skill_path(skill_id)
            loader = SkillLoader()
            skills = loader.load_from_directory(skill_path, recursive=True)
            if skills:
                for skill in skills:
                    availability = skill.get_definition().metadata.get("availability", {})
                    get_skill_registry().register(skill, enable=availability.get("status") != "blocked")
                return ExecutionResult.success(
                    message=f"Skill {skill_id} installed successfully",
                    data={"installed": True, "skill_path": str(skill_path), "scan": scan, "guard": guard},
                )

            return ExecutionResult.from_error(
                f"Failed to load skill {skill_id}",
                ErrorType.SYSTEM_ERROR,
                data={"scan": scan, "guard": guard},
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
    register_tool(ClawHubScanTool())
    register_tool(ClawHubInstallTool())
    register_tool(ClawHubRemoveTool())
    register_tool(ClawHubListTool())

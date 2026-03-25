"""Internal tool for prompting the configured local model from skills."""

import logging
from typing import Optional

from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel
from localclaw.llm.provider import get_llm_provider
from localclaw.tools.base import Tool, register_tool


logger = logging.getLogger(__name__)


class LocalModelPromptTool(Tool):
    """Skill-only tool for running a direct prompt against the local model."""

    name = "_local_model_prompt"
    description = "Internal skill-only local model prompt execution"
    risk_level = RiskLevel.LOW
    inputs = {"prompt": "string"}
    outputs = {"content": "string", "model": "string", "provider": "string"}

    async def execute(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 768,
        temperature: float = 0.2,
        **kwargs,
    ) -> ExecutionResult:
        """Run a direct prompt against the configured local model."""

        del kwargs

        provider = get_llm_provider()
        if not await provider.is_available():
            return ExecutionResult.from_error(
                "Local model is unavailable for this skill",
                ErrorType.SYSTEM_ERROR,
            )

        try:
            response = await provider.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            logger.error("Local model prompt execution failed: %s", exc)
            return ExecutionResult.from_error(str(exc), ErrorType.SYSTEM_ERROR)

        content = response.content.strip()
        return ExecutionResult.success(
            message=content or "Local model completed the skill prompt",
            data={
                "content": content,
                "message": content,
                "model": response.model,
                "provider": response.provider,
                "tokens_used": response.tokens_used,
                "finish_reason": response.finish_reason,
            },
        )


def register_local_model_tools() -> None:
    """Register internal local-model-backed tools."""

    register_tool(LocalModelPromptTool())

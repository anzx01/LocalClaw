"""System initialization and bootstrap shared by CLI and web channels."""

import logging
from typing import Optional

from localclaw.config.settings import Settings, get_settings
from localclaw.core.engine import ExecutionEngine
from localclaw.llm.provider import initialize_llm_provider
from localclaw.security.audit import configure_audit_logger
from localclaw.skills.loader import load_skills_from_settings
from localclaw.tools.base import get_tool_registry
from localclaw.tools.browser_cdp_tool import register_browser_cdp_tools
from localclaw.tools.clawhub_tool import register_clawhub_tools
from localclaw.tools.file_tool import register_file_tools
from localclaw.tools.http_tool import register_http_tools
from localclaw.tools.launch_app_tool import register_launch_app_tools
from localclaw.tools.local_model_tool import register_local_model_tools
from localclaw.tools.shell_tool import register_shell_tools
from localclaw.tools.system_tools import register_system_tools


logger = logging.getLogger(__name__)


def initialize_system(settings: Optional[Settings] = None) -> ExecutionEngine:
    """Initialize the LocalClaw system.

    This is the single shared entry point used by both CLI and web channels.

    Args:
        settings: Optional settings override. If None, uses get_settings().

    Returns:
        Initialized ExecutionEngine instance set as the global singleton.
    """
    settings = settings or get_settings()
    settings.ensure_directories()

    configure_audit_logger(settings.audit_log)

    if settings.llm_enabled:
        try:
            provider = initialize_llm_provider(settings)
            logger.info(
                "LLM provider initialized: %s (%s)",
                provider.get_config().provider_type.value,
                provider.get_config().model,
            )
        except Exception as e:
            logger.error(f"Failed to initialize LLM provider: {e}")
    else:
        logger.info("LLM is disabled")

    # Register all tools
    register_system_tools()
    register_file_tools()
    register_http_tools()
    register_launch_app_tools()
    register_local_model_tools()
    register_shell_tools()
    register_browser_cdp_tools()
    register_clawhub_tools()

    # Load skills
    load_skills_from_settings(settings)

    # Create verifier
    from localclaw.core.verifier import create_default_verifier
    from localclaw.skills.registry import get_skill_registry

    verifier = create_default_verifier(
        settings=settings,
        skill_registry=get_skill_registry(),
    )
    verifier.set_auto_approve_low(True)
    verifier.set_require_confirmation_high(True)

    # Create engine
    engine = ExecutionEngine(
        settings=settings,
        verifier=verifier,
    )

    # Set global engine instance
    import localclaw.core.engine as engine_module

    engine_module._engine = engine

    return engine

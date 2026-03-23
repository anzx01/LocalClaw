"""Agent management module."""

from localclaw.agents.manager import AgentManager, get_agent_manager
from localclaw.agents.config import AgentConfig, AgentLoader

__all__ = [
    "AgentManager",
    "AgentConfig",
    "AgentLoader",
    "get_agent_manager",
]

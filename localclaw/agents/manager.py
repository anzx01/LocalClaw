"""Agent manager for multi-agent support."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from localclaw.agents.config import AgentConfig, AgentLoader, create_default_agent
from localclaw.core.models import Context, ErrorType, ExecutionResult, Message, Task
from localclaw.core.engine import ExecutionEngine
from localclaw.gateway.router import AgentRouter, get_router


logger = logging.getLogger(__name__)


class AgentManager:
    """Manager for multiple agents."""
    
    def __init__(self, engine: Optional[ExecutionEngine] = None) -> None:
        self._agents: Dict[str, AgentConfig] = {}
        self._engine = engine
        self._router = get_router()
        self._loader = AgentLoader()
        self._logger = logging.getLogger("localclaw.agents.manager")
        
        self._register_default_agent()
    
    def _register_default_agent(self) -> None:
        """Register the default agent."""
        default = create_default_agent()
        self._agents[default.name] = default
        self._router.register_agent(default)
    
    def set_engine(self, engine: ExecutionEngine) -> None:
        """Set the execution engine."""
        self._engine = engine
    
    def register_agent(self, config: AgentConfig) -> None:
        """Register an agent configuration."""
        self._agents[config.name] = config
        self._router.register_agent(config)
        self._logger.info("Registered agent: %s", config.name)
    
    def unregister_agent(self, name: str) -> bool:
        """Unregister an agent."""
        if name == "default":
            self._logger.warning("Cannot unregister default agent")
            return False
        
        if name in self._agents:
            del self._agents[name]
            self._router.unregister_agent(name)
            self._logger.info("Unregistered agent: %s", name)
            return True
        return False
    
    def get_agent(self, name: str) -> Optional[AgentConfig]:
        """Get an agent configuration."""
        return self._agents.get(name)
    
    def list_agents(self) -> List[str]:
        """List all registered agent names."""
        return list(self._agents.keys())
    
    def get_all_agents(self) -> List[AgentConfig]:
        """Get all agent configurations."""
        return list(self._agents.values())
    
    def load_agents_from_dir(self, dir_path: Any) -> int:
        """Load agents from a directory."""
        from pathlib import Path
        path = Path(dir_path) if not isinstance(dir_path, Path) else dir_path
        
        configs = self._loader.load_from_dir(path)
        
        for config in configs:
            self.register_agent(config)
        
        return len(configs)
    
    def enable_agent(self, name: str) -> bool:
        """Enable an agent."""
        agent = self._agents.get(name)
        if agent:
            agent.enabled = True
            self._logger.info("Enabled agent: %s", name)
            return True
        return False
    
    def disable_agent(self, name: str) -> bool:
        """Disable an agent."""
        if name == "default":
            self._logger.warning("Cannot disable default agent")
            return False
        
        agent = self._agents.get(name)
        if agent:
            agent.enabled = False
            self._logger.info("Disabled agent: %s", name)
            return True
        return False
    
    def route_message(self, content: str, user_id: str) -> Optional[str]:
        """Route a message to an appropriate agent."""
        return self._router.route(content, user_id)
    
    async def execute_with_agent(
        self,
        agent_name: str,
        message: Message,
        context: Optional[Context] = None,
    ) -> Task:
        """Execute a message with a specific agent."""
        if self._engine is None:
            raise RuntimeError("Engine not set")
        
        agent = self._agents.get(agent_name)
        if not agent:
            return self._create_error_task(
                message,
                f"Agent not found: {agent_name}",
            )
        
        if not agent.enabled:
            return self._create_error_task(
                message,
                f"Agent is disabled: {agent_name}",
            )
        
        task = await self._engine.process_message(message)
        
        return task
    
    async def call_agent(
        self,
        agent_name: str,
        skill_name: str,
        params: Dict[str, Any],
        parent_context: Optional[Context] = None,
    ) -> ExecutionResult:
        """Call a skill on another agent (agent_call step)."""
        agent = self._agents.get(agent_name)
        
        if not agent:
            return ExecutionResult.from_error(
                f"Agent not found: {agent_name}",
                ErrorType.VALIDATION_ERROR,
            )
        
        if not agent.enabled:
            return ExecutionResult.from_error(
                f"Agent is disabled: {agent_name}",
                ErrorType.PERMISSION_ERROR,
            )
        
        if not agent.can_use_skill(skill_name):
            return ExecutionResult.from_error(
                f"Agent '{agent_name}' cannot use skill '{skill_name}'",
                ErrorType.PERMISSION_ERROR,
            )
        
        from localclaw.skills.registry import get_skill_registry
        
        registry = get_skill_registry()
        skill = registry.get(skill_name)
        
        if skill is None:
            return ExecutionResult.from_error(
                f"Skill not found: {skill_name}",
                ErrorType.VALIDATION_ERROR,
            )
        
        context = parent_context or Context()
        
        try:
            result = await skill.execute(context, **params)
            return result
        except Exception as e:
            self._logger.error(f"Agent call failed: {agent_name}.{skill_name}: {e}")
            return ExecutionResult.from_error(
                f"Agent call failed: {e}",
                ErrorType.SYSTEM_ERROR,
            )
    
    def _create_error_task(self, message: Message, error: str) -> Task:
        """Create an error task."""
        task = Task(
            message=message,
            user_id=message.user_id,
            channel=message.channel,
        )
        task.error = error
        task.error_type = ErrorType.VALIDATION_ERROR
        from localclaw.core.models import TaskState
        task.advance_state(TaskState.FAILED)
        return task
    
    def get_agent_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get detailed agent information."""
        agent = self._agents.get(name)
        if not agent:
            return None
        
        return {
            **agent.to_dict(),
            "state": "enabled" if agent.enabled else "disabled",
        }
    
    def get_all_info(self) -> List[Dict[str, Any]]:
        """Get information about all agents."""
        return [
            self.get_agent_info(name)
            for name in self._agents.keys()
        ]


_manager: Optional[AgentManager] = None


def get_agent_manager() -> AgentManager:
    """Get the global agent manager instance."""
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager


def initialize_agent_manager(engine: ExecutionEngine, agents_dir: Optional[Any] = None) -> AgentManager:
    """Initialize the agent manager with engine and agents directory."""
    global _manager
    _manager = AgentManager(engine)
    
    if agents_dir:
        _manager.load_agents_from_dir(agents_dir)
    
    return _manager

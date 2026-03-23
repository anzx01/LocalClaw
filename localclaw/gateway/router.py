"""Agent router for message routing."""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)


@dataclass
class RoutingRule:
    """A routing rule for directing messages to agents."""
    name: str
    pattern: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    user_ids: List[str] = field(default_factory=list)
    agent_name: str = ""
    priority: int = 0
    
    def __post_init__(self) -> None:
        if self.pattern:
            self._compiled_pattern = re.compile(self.pattern, re.IGNORECASE)
        else:
            self._compiled_pattern = None
    
    def matches(self, content: str, user_id: str) -> bool:
        """Check if this rule matches the message."""
        if self.user_ids and user_id not in self.user_ids:
            return False
        
        if self._compiled_pattern:
            if self._compiled_pattern.search(content):
                return True
        
        if self.keywords:
            content_lower = content.lower()
            if any(kw.lower() in content_lower for kw in self.keywords):
                return True
        
        if not self.user_ids and not self.pattern and not self.keywords:
            return True
        
        return False


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    description: str = ""
    skills: List[str] = field(default_factory=list)
    permissions: Dict[str, Any] = field(default_factory=dict)
    max_risk_level: str = "medium"
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentRouter:
    """Routes messages to appropriate agents."""
    
    def __init__(self) -> None:
        self._agents: Dict[str, AgentConfig] = {}
        self._rules: List[RoutingRule] = []
        self._default_agent: Optional[str] = None
        self._user_bindings: Dict[str, str] = {}
        self._logger = logging.getLogger("localclaw.router")
    
    def register_agent(self, config: AgentConfig) -> None:
        """Register an agent configuration."""
        self._agents[config.name] = config
        self._logger.info(f"Registered agent: {config.name}")
    
    def unregister_agent(self, name: str) -> bool:
        """Unregister an agent."""
        if name in self._agents:
            del self._agents[name]
            self._logger.info(f"Unregistered agent: {name}")
            return True
        return False
    
    def get_agent(self, name: str) -> Optional[AgentConfig]:
        """Get an agent configuration."""
        return self._agents.get(name)
    
    def list_agents(self) -> List[str]:
        """List all registered agents."""
        return list(self._agents.keys())
    
    def add_rule(self, rule: RoutingRule) -> None:
        """Add a routing rule."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)
        self._logger.info(f"Added routing rule: {rule.name}")
    
    def remove_rule(self, name: str) -> bool:
        """Remove a routing rule."""
        for i, rule in enumerate(self._rules):
            if rule.name == name:
                del self._rules[i]
                return True
        return False
    
    def set_default_agent(self, agent_name: str) -> None:
        """Set the default agent for unmatched messages."""
        self._default_agent = agent_name
    
    def bind_user(self, user_id: str, agent_name: str) -> None:
        """Bind a user to a specific agent."""
        self._user_bindings[user_id] = agent_name
        self._logger.info(f"Bound user {user_id} to agent {agent_name}")
    
    def unbind_user(self, user_id: str) -> bool:
        """Remove a user binding."""
        if user_id in self._user_bindings:
            del self._user_bindings[user_id]
            return True
        return False
    
    def route(self, content: str, user_id: str) -> Optional[str]:
        """Route a message to an agent."""
        if user_id in self._user_bindings:
            agent_name = self._user_bindings[user_id]
            if agent_name in self._agents and self._agents[agent_name].enabled:
                return agent_name
        
        for rule in self._rules:
            if rule.agent_name not in self._agents:
                continue
            if not self._agents[rule.agent_name].enabled:
                continue
            
            if rule.matches(content, user_id):
                self._logger.debug(f"Rule '{rule.name}' matched, routing to {rule.agent_name}")
                return rule.agent_name
        
        if self._default_agent and self._default_agent in self._agents:
            if self._agents[self._default_agent].enabled:
                return self._default_agent
        
        for name, config in self._agents.items():
            if config.enabled:
                return name
        
        return None
    
    def get_agent_skills(self, agent_name: str) -> List[str]:
        """Get skills available to an agent."""
        agent = self._agents.get(agent_name)
        if agent:
            return agent.skills.copy()
        return []
    
    def can_use_skill(self, agent_name: str, skill_name: str) -> bool:
        """Check if an agent can use a specific skill."""
        agent = self._agents.get(agent_name)
        if not agent:
            return False
        
        if not agent.skills:
            return True
        
        return skill_name in agent.skills


def create_default_router() -> AgentRouter:
    """Create a router with default configuration."""
    router = AgentRouter()
    
    router.register_agent(AgentConfig(
        name="default",
        description="Default agent for general tasks",
        skills=[],
        max_risk_level="medium",
    ))
    
    router.set_default_agent("default")
    
    return router


_router: Optional[AgentRouter] = None


def get_router() -> AgentRouter:
    """Get the global router instance."""
    global _router
    if _router is None:
        _router = create_default_router()
    return _router

"""Agent configuration and loader."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    description: str = ""
    version: str = "1.0.0"
    skills: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    permissions: Dict[str, Any] = field(default_factory=dict)
    max_risk_level: str = "medium"
    enabled: bool = True
    is_default: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def can_use_skill(self, skill_name: str) -> bool:
        """Check if agent can use a skill."""
        if not self.skills:
            return True
        return skill_name in self.skills
    
    def can_use_tool(self, tool_name: str) -> bool:
        """Check if agent can use a tool."""
        if not self.tools:
            return True
        return tool_name in self.tools
    
    def get_max_risk_level_value(self) -> int:
        """Get numeric value for max risk level."""
        levels = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        return levels.get(self.max_risk_level.lower(), 2)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "skills": self.skills,
            "tools": self.tools,
            "permissions": self.permissions,
            "max_risk_level": self.max_risk_level,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentConfig":
        """Create from dictionary."""
        return cls(
            name=data.get("name", "unnamed"),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            skills=data.get("skills", []),
            tools=data.get("tools", []),
            permissions=data.get("permissions", {}),
            max_risk_level=data.get("max_risk_level", "medium"),
            enabled=data.get("enabled", True),
            is_default=data.get("is_default", False),
            metadata=data.get("metadata", {}),
        )


class AgentLoader:
    """Loader for agent configurations."""
    
    def __init__(self, agents_dir: Optional[Path] = None) -> None:
        self._agents_dir = agents_dir
        self._logger = logging.getLogger("localclaw.agents.loader")
    
    def load_from_file(self, file_path: Path) -> AgentConfig:
        """Load agent configuration from a file."""
        if not file_path.exists():
            raise FileNotFoundError(f"Agent config not found: {file_path}")
        
        suffix = file_path.suffix.lower()
        
        if suffix == ".json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        elif suffix in (".yaml", ".yml"):
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        else:
            raise ValueError(f"Unsupported agent config format: {suffix}")
        
        config = AgentConfig.from_dict(data)
        self._logger.info(f"Loaded agent config: {config.name} from {file_path}")
        return config
    
    def load_from_dir(self, dir_path: Optional[Path] = None) -> List[AgentConfig]:
        """Load all agent configurations from a directory."""
        agents_dir = dir_path or self._agents_dir
        if not agents_dir:
            return []
        
        if not agents_dir.exists():
            self._logger.warning(f"Agents directory not found: {agents_dir}")
            return []
        
        configs = []
        
        for file_path in agents_dir.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in (".json", ".yaml", ".yml"):
                try:
                    config = self.load_from_file(file_path)
                    configs.append(config)
                except Exception as e:
                    self._logger.error(f"Failed to load agent from {file_path}: {e}")
        
        return configs
    
    def save_to_file(self, config: AgentConfig, file_path: Path) -> None:
        """Save agent configuration to a file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        suffix = file_path.suffix.lower()
        data = config.to_dict()
        
        if suffix == ".json":
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        elif suffix in (".yaml", ".yml"):
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False)
        else:
            raise ValueError(f"Unsupported agent config format: {suffix}")
        
        self._logger.info(f"Saved agent config: {config.name} to {file_path}")


def create_default_agent() -> AgentConfig:
    """Create the default agent configuration."""
    return AgentConfig(
        name="default",
        description="Default agent for general tasks",
        version="1.0.0",
        skills=[],
        tools=[],
        max_risk_level="medium",
        enabled=True,
        is_default=True,
    )

"""Base Skill class and definitions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional

from localclaw.core.models import Context, ExecutionResult, RiskLevel


class SkillType(str, Enum):
    """Types of skills."""
    ATOMIC = "atomic"
    WORKFLOW = "workflow"
    AGENT = "agent"


class SkillState(str, Enum):
    """Lifecycle states of a skill."""
    INSTALLED = "installed"
    ENABLED = "enabled"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class SkillAction:
    """An action within a skill."""
    type: str
    name: Optional[str] = None
    tool: Optional[str] = None
    skill: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    template: Optional[str] = None
    condition: Optional[str] = None
    var: Optional[str] = None
    over: Optional[str] = None
    then: List["SkillAction"] = field(default_factory=list)
    actions: List["SkillAction"] = field(default_factory=list)
    timeout: float = 30.0
    retry_policy: Optional[Dict[str, Any]] = None
    error_policy: Optional[Dict[str, Any]] = None
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the action."""
        value = getattr(self, key, None)
        return value if value is not None else default


@dataclass
class SkillTrigger:
    """A trigger for a skill."""
    type: str
    pattern: Optional[str] = None
    schedule: Optional[str] = None
    event: Optional[str] = None


@dataclass
class SkillDefinition:
    """Definition of a skill."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    type: SkillType = SkillType.ATOMIC
    inputs: Dict[str, str] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)
    actions: List[SkillAction] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    permissions: Dict[str, Any] = field(default_factory=dict)
    triggers: List[SkillTrigger] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the skill definition."""
        return getattr(self, key, default)


class Skill(ABC):
    """Abstract base class for skills."""
    
    name: ClassVar[str] = "base_skill"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Base skill class"
    skill_type: ClassVar[SkillType] = SkillType.ATOMIC
    inputs: ClassVar[Dict[str, str]] = {}
    outputs: ClassVar[Dict[str, str]] = {}
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    tools: ClassVar[List[str]] = []
    
    def __init__(self) -> None:
        self._state = SkillState.INSTALLED
    
    @abstractmethod
    async def execute(self, context: Context, **kwargs: Any) -> ExecutionResult:
        """Execute the skill."""
        pass
    
    def get_definition(self) -> SkillDefinition:
        """Get the skill definition."""
        return SkillDefinition(
            name=self.name,
            version=self.version,
            description=self.description,
            type=self.skill_type,
            inputs=self.inputs,
            outputs=self.outputs,
            tools=self.tools,
            permissions={"risk_level": self.risk_level.value},
        )
    
    def enable(self) -> None:
        """Enable the skill."""
        self._state = SkillState.ENABLED
    
    def disable(self) -> None:
        """Disable the skill."""
        self._state = SkillState.STOPPED
    
    @property
    def state(self) -> SkillState:
        """Get the current state."""
        return self._state


class DeclarativeSkill(Skill):
    """A skill defined by a SkillDefinition."""
    
    def __init__(self, definition: SkillDefinition) -> None:
        self._definition = definition
        self._state = SkillState.INSTALLED
    
    @property
    def name(self) -> str:
        return self._definition.name
    
    @property
    def version(self) -> str:
        return self._definition.version
    
    @property
    def description(self) -> str:
        return self._definition.description
    
    @property
    def skill_type(self) -> SkillType:
        return self._definition.type
    
    @property
    def inputs(self) -> Dict[str, str]:
        return self._definition.inputs
    
    @property
    def outputs(self) -> Dict[str, str]:
        return self._definition.outputs
    
    @property
    def tools(self) -> List[str]:
        return self._definition.tools
    
    async def execute(self, context: Context, **kwargs: Any) -> ExecutionResult:
        """Execute the skill - this is handled by the engine for declarative skills."""
        return ExecutionResult.success(
            message="Declarative skill execution handled by engine",
            data={"actions": len(self._definition.actions)},
        )
    
    def get_definition(self) -> SkillDefinition:
        """Get the skill definition."""
        return self._definition


def create_skill_from_dict(data: Dict[str, Any]) -> DeclarativeSkill:
    """Create a skill from a dictionary definition."""
    actions = []
    for action_data in data.get("actions", []):
        actions.append(_parse_action(action_data))
    
    triggers = []
    for trigger_data in data.get("triggers", []):
        triggers.append(SkillTrigger(
            type=trigger_data.get("type", "manual"),
            pattern=trigger_data.get("pattern"),
            schedule=trigger_data.get("schedule"),
            event=trigger_data.get("event"),
        ))
    
    definition = SkillDefinition(
        name=data.get("name", "unknown"),
        version=data.get("version", "1.0.0"),
        description=data.get("description", ""),
        type=SkillType(data.get("type", "atomic")),
        inputs=data.get("inputs", {}),
        outputs=data.get("outputs", {}),
        actions=actions,
        tools=data.get("tools", []),
        permissions=data.get("permissions", {}),
        triggers=triggers,
        metadata=data.get("metadata", {}),
    )
    
    return DeclarativeSkill(definition)


def _parse_action(data: Dict[str, Any]) -> SkillAction:
    """Parse an action from a dictionary."""
    then_actions = [_parse_action(a) for a in data.get("then", [])]
    sub_actions = [_parse_action(a) for a in data.get("actions", [])]
    action_type = data.get("type", "transform")
    normalized_params = (
        data.get("params")
        or data.get("inputs")
        or data.get("args")
        or {}
    )
    normalized_tool = data.get("tool")
    if normalized_tool is None and action_type in {"tool", "tool_call", "command"}:
        normalized_tool = data.get("name") or data.get("command")

    normalized_skill = data.get("skill") or data.get("handler")
    if normalized_skill is None and action_type in {"skill", "skill_call", "handler"}:
        normalized_skill = data.get("name")
    
    return SkillAction(
        type=action_type,
        name=data.get("name"),
        tool=normalized_tool,
        skill=normalized_skill,
        params=normalized_params,
        template=data.get("template"),
        condition=data.get("condition"),
        var=data.get("var"),
        over=data.get("over"),
        then=then_actions,
        actions=sub_actions,
        timeout=data.get("timeout", 30.0),
        retry_policy=data.get("retry_policy"),
        error_policy=data.get("error_policy"),
    )

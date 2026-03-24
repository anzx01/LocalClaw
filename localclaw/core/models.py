"""Core data models for LocalClaw."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskState(str, Enum):
    """Task state machine states."""
    INIT = "init"
    PARSED = "parsed"
    PLANNED = "planned"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class StepType(str, Enum):
    """Types of execution steps."""
    TOOL_CALL = "tool_call"
    SKILL_CALL = "skill_call"
    AGENT_CALL = "agent_call"
    CONDITION = "condition"
    LOOP = "loop"
    TRANSFORM = "transform"
    PARALLEL = "parallel"


class StepStatus(str, Enum):
    """Status of a step execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RiskLevel(str, Enum):
    """Risk levels for operations."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorType(str, Enum):
    """Types of errors that can occur."""
    TOOL_ERROR = "tool_error"
    TIMEOUT_ERROR = "timeout_error"
    PERMISSION_ERROR = "permission_error"
    VALIDATION_ERROR = "validation_error"
    SYSTEM_ERROR = "system_error"
    PARSE_ERROR = "parse_error"
    PLAN_ERROR = "plan_error"


class Message(BaseModel):
    """Unified message format."""
    user_id: str = Field(default_factory=lambda: "default")
    channel: str = Field(default="cli")
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Intent(BaseModel):
    """Parser output representing user intent."""
    intent: str
    params: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(default="rule")
    raw_message: Optional[str] = None


class RetryPolicy(BaseModel):
    """Retry policy for steps."""
    max_retries: int = Field(default=3, ge=0)
    delay: float = Field(default=1.0, ge=0)
    backoff: float = Field(default=2.0, ge=1.0)
    retry_on: List[ErrorType] = Field(default_factory=lambda: [ErrorType.TOOL_ERROR, ErrorType.TIMEOUT_ERROR])


class ErrorPolicy(BaseModel):
    """Error handling policy."""
    on_failure: str = Field(default="abort")
    fallback_step: Optional[str] = None
    fallback_message: Optional[str] = None


class Step(BaseModel):
    """Execution step."""
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    type: StepType
    status: StepStatus = Field(default=StepStatus.PENDING)
    name: Optional[str] = None
    
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    
    tool_name: Optional[str] = None
    skill_name: Optional[str] = None
    agent_name: Optional[str] = None
    source_skill_name: Optional[str] = None
    
    condition: Optional[str] = None
    loop_var: Optional[str] = None
    loop_over: Optional[str] = None
    template: Optional[str] = None
    
    timeout: float = Field(default=30.0)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    error_policy: ErrorPolicy = Field(default_factory=ErrorPolicy)
    
    retry_count: int = Field(default=0, ge=0)
    error: Optional[str] = None
    error_type: Optional[ErrorType] = None
    
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    sub_steps: List["Step"] = Field(default_factory=list)
    parallel_steps: List["Step"] = Field(default_factory=list)


Step.model_rebuild()


class Plan(BaseModel):
    """Planner output containing execution steps."""
    steps: List[Step] = Field(default_factory=list)
    intent: Optional[Intent] = None
    skill_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class Context(BaseModel):
    """Execution context."""
    inputs: Dict[str, Any] = Field(default_factory=dict)
    memory: Dict[str, Any] = Field(default_factory=dict)
    step_outputs: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict)
    
    def get_step_output(self, step_id: str) -> Dict[str, Any]:
        """Get output from a specific step."""
        return self.step_outputs.get(step_id, {})
    
    def set_step_output(self, step_id: str, output: Dict[str, Any]) -> None:
        """Set output for a specific step."""
        self.step_outputs[step_id] = output
    
    def get_variable(self, name: str, default: Any = None) -> Any:
        """Get a variable value."""
        return self.variables.get(name, default)
    
    def set_variable(self, name: str, value: Any) -> None:
        """Set a variable value."""
        self.variables[name] = value


class Task(BaseModel):
    """Task representation with state machine."""
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    state: TaskState = Field(default=TaskState.INIT)
    message: Optional[Message] = None
    intent: Optional[Intent] = None
    plan: Optional[Plan] = None
    context: Context = Field(default_factory=Context)
    
    current_step_index: int = Field(default=0, ge=0)
    
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    error_type: Optional[ErrorType] = None
    
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    
    user_id: str = Field(default="default")
    channel: str = Field(default="cli")
    
    def advance_state(self, new_state: TaskState) -> None:
        """Advance to a new state."""
        self.state = new_state
        self.updated_at = datetime.now()
        if new_state in (TaskState.COMPLETED, TaskState.FAILED):
            self.completed_at = datetime.now()
    
    def get_current_step(self) -> Optional[Step]:
        """Get the current step being executed."""
        if self.plan and 0 <= self.current_step_index < len(self.plan.steps):
            return self.plan.steps[self.current_step_index]
        return None
    
    def advance_step(self) -> bool:
        """Advance to the next step. Returns True if there are more steps."""
        if self.plan and self.current_step_index < len(self.plan.steps) - 1:
            self.current_step_index += 1
            self.updated_at = datetime.now()
            return True
        return False


class ExecutionResult(BaseModel):
    """Standard execution result format."""
    status: str = Field(default="success")
    message: str = Field(default="")
    data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    error_type: Optional[ErrorType] = None
    
    @classmethod
    def success(cls, message: str = "", data: Optional[Dict[str, Any]] = None) -> "ExecutionResult":
        """Create a successful result."""
        return cls(status="success", message=message, data=data or {})
    
    @classmethod
    def from_error(cls, message: str, error_type: Optional[ErrorType] = None, data: Optional[Dict[str, Any]] = None) -> "ExecutionResult":
        """Create an error result."""
        return cls(status="error", message=message, error=message, error_type=error_type, data=data or {})

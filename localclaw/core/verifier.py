"""Verifier module for validation and security checks."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional

from localclaw.config.settings import Settings, get_settings
from localclaw.core.models import (
    Context,
    ExecutionResult,
    RiskLevel,
    Step,
)
from localclaw.skills.registry.registry import SkillRegistry, get_skill_registry


class VerificationDecision(str, Enum):
    """Decision after verification."""
    PASS = "pass"
    REJECT = "reject"
    ASK_HUMAN = "ask_human"


class VerificationResult:
    """Result of a verification check."""
    
    def __init__(
        self,
        decision: VerificationDecision,
        message: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.decision = decision
        self.message = message
        self.details = details or {}
    
    @classmethod
    def pass_result(cls, message: str = "Verification passed") -> "VerificationResult":
        """Create a passing result."""
        return cls(VerificationDecision.PASS, message)
    
    @classmethod
    def reject_result(cls, message: str, details: Optional[Dict[str, Any]] = None) -> "VerificationResult":
        """Create a rejection result."""
        return cls(VerificationDecision.REJECT, message, details)
    
    @classmethod
    def ask_human_result(cls, message: str, details: Optional[Dict[str, Any]] = None) -> "VerificationResult":
        """Create a result requiring human approval."""
        return cls(VerificationDecision.ASK_HUMAN, message, details)


class VerifierBackend(ABC):
    """Abstract base class for verification backends."""
    
    @abstractmethod
    async def verify_step(self, step: Step, context: Context) -> VerificationResult:
        """Verify a step before execution."""
        pass
    
    @abstractmethod
    async def verify_result(self, step: Step, result: ExecutionResult, context: Context) -> VerificationResult:
        """Verify a result after execution."""
        pass


class PermissionVerifier(VerifierBackend):
    """Verifies permissions for operations."""
    
    HIGH_RISK_TOOLS = {"shell", "http_post", "browser_cdp"}
    CRITICAL_TOOLS = {"shell"}
    
    def __init__(
        self,
        auto_approve_low: bool = True,
        require_confirmation_high: bool = True,
        settings: Optional[Settings] = None,
        skill_registry: Optional[SkillRegistry] = None,
    ) -> None:
        self._auto_approve_low = auto_approve_low
        self._require_confirmation_high = require_confirmation_high
        self._approved_operations: set = set()
        self._settings = settings or get_settings()
        self._skill_registry = skill_registry or get_skill_registry()

    def _get_skill_guard(self, step: Step) -> Dict[str, Any]:
        """Return post-install guard metadata for the source skill if present."""

        skill_name = (step.source_skill_name or "").strip()
        if not skill_name:
            return {}

        skill = self._skill_registry.get(skill_name)
        if skill is None:
            return {}

        definition = skill.get_definition() if hasattr(skill, "get_definition") else None
        metadata = definition.metadata if definition is not None else {}
        guard = metadata.get("localclaw_guard", {})
        return dict(guard) if isinstance(guard, dict) else {}
    
    def get_risk_level(self, step: Step) -> RiskLevel:
        """Determine the risk level of a step."""
        if step.type.value == "tool_call":
            tool_name = step.tool_name or ""
            if tool_name in self.CRITICAL_TOOLS:
                return RiskLevel.CRITICAL
            if tool_name in self.HIGH_RISK_TOOLS:
                return RiskLevel.HIGH
            return RiskLevel.LOW
        
        if step.type.value == "shell_tool":
            return RiskLevel.HIGH
        
        return RiskLevel.LOW
    
    async def verify_step(self, step: Step, context: Context) -> VerificationResult:
        """Verify a step before execution."""
        guard = self._get_skill_guard(step)
        if step.type.value == "tool_call" and guard:
            tool_name = (step.tool_name or "").strip()
            blocked_tools = set(guard.get("blocked_tools", []) or [])
            approval_required_tools = set(guard.get("approval_required_tools", []) or [])

            if tool_name in blocked_tools:
                return VerificationResult.reject_result(
                    f"Tool '{tool_name}' is blocked by post-install protection for skill '{step.source_skill_name}'",
                    {
                        "tool_name": tool_name,
                        "source_skill_name": step.source_skill_name,
                        "guard_mode": guard.get("mode", "off"),
                    },
                )

            if tool_name in approval_required_tools and not self.is_approved(step.id):
                return VerificationResult.ask_human_result(
                    f"Isolated skill '{step.source_skill_name}' requires approval before running '{tool_name}'",
                    {
                        "tool_name": tool_name,
                        "source_skill_name": step.source_skill_name,
                        "guard_mode": guard.get("mode", "isolate"),
                    },
                )

        risk_level = self.get_risk_level(step)

        if self.is_approved(step.id):
            return VerificationResult.pass_result("Operation approved by human")
        
        if risk_level == RiskLevel.LOW and self._auto_approve_low:
            return VerificationResult.pass_result(f"Auto-approved: low risk operation")
        
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            if self._require_confirmation_high:
                return VerificationResult.ask_human_result(
                    f"High-risk operation requires confirmation: {step.type.value}",
                    {"risk_level": risk_level.value, "step": step.model_dump()}
                )
        
        return VerificationResult.pass_result()
    
    async def verify_result(self, step: Step, result: ExecutionResult, context: Context) -> VerificationResult:
        """Verify a result after execution."""
        if result.status == "error":
            return VerificationResult.reject_result(
                f"Step execution failed: {result.message}",
                {"error": result.error, "error_type": result.error_type}
            )
        
        return VerificationResult.pass_result()
    
    def approve_operation(self, operation_id: str) -> None:
        """Mark an operation as approved."""
        self._approved_operations.add(operation_id)
    
    def is_approved(self, operation_id: str) -> bool:
        """Check if an operation is approved."""
        return operation_id in self._approved_operations


class SchemaVerifier(VerifierBackend):
    """Verifies data against schemas."""
    
    async def verify_step(self, step: Step, context: Context) -> VerificationResult:
        """Verify step input against schema if available."""
        return VerificationResult.pass_result()
    
    async def verify_result(self, step: Step, result: ExecutionResult, context: Context) -> VerificationResult:
        """Verify result data against output schema if available."""
        if result.status == "error":
            return VerificationResult.pass_result()
        
        return VerificationResult.pass_result()
    
    def _validate_schema(self, data: Dict[str, Any], schema: Dict[str, str]) -> List[str]:
        """Validate data against a simple schema."""
        errors: List[str] = []
        
        for key, expected_type in schema.items():
            if key not in data:
                errors.append(f"Missing required field: {key}")
                continue
            
            value = data[key]
            type_map = {
                "string": str,
                "integer": int,
                "float": (int, float),
                "boolean": bool,
                "list": list,
                "dict": dict,
            }
            
            expected_python_type = type_map.get(expected_type, str)
            if not isinstance(value, expected_python_type):
                errors.append(f"Field '{key}' has wrong type: expected {expected_type}, got {type(value).__name__}")
        
        return errors


class Verifier:
    """Main verifier combining multiple verification backends."""
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        skill_registry: Optional[SkillRegistry] = None,
    ) -> None:
        self._permission_verifier = PermissionVerifier(
            settings=settings,
            skill_registry=skill_registry,
        )
        self._schema_verifier = SchemaVerifier()
        self._pending_approvals: Dict[str, Step] = {}
    
    async def verify_before_execution(self, step: Step, context: Context) -> VerificationResult:
        """Run all pre-execution verifications."""
        results = []
        
        perm_result = await self._permission_verifier.verify_step(step, context)
        results.append(("permission", perm_result))
        
        schema_result = await self._schema_verifier.verify_step(step, context)
        results.append(("schema", schema_result))
        
        for name, result in results:
            if result.decision == VerificationDecision.REJECT:
                return result
            if result.decision == VerificationDecision.ASK_HUMAN:
                self._pending_approvals[step.id] = step
                return result
        
        return VerificationResult.pass_result()
    
    async def verify_after_execution(self, step: Step, result: ExecutionResult, context: Context) -> VerificationResult:
        """Run all post-execution verifications."""
        results = []
        
        perm_result = await self._permission_verifier.verify_result(step, result, context)
        results.append(("permission", perm_result))
        
        schema_result = await self._schema_verifier.verify_result(step, result, context)
        results.append(("schema", schema_result))
        
        for name, res in results:
            if res.decision == VerificationDecision.REJECT:
                return res
        
        return VerificationResult.pass_result()
    
    def approve_step(self, step_id: str) -> bool:
        """Approve a pending step."""
        if step_id in self._pending_approvals:
            self._permission_verifier.approve_operation(step_id)
            del self._pending_approvals[step_id]
            return True
        return False
    
    def get_pending_approvals(self) -> List[str]:
        """Get list of pending approval IDs."""
        return list(self._pending_approvals.keys())
    
    def set_auto_approve_low(self, value: bool) -> None:
        """Set auto-approve for low risk operations."""
        self._permission_verifier._auto_approve_low = value
    
    def set_require_confirmation_high(self, value: bool) -> None:
        """Set requirement for confirmation on high risk operations."""
        self._permission_verifier._require_confirmation_high = value


def create_default_verifier(
    settings: Optional[Settings] = None,
    skill_registry: Optional[SkillRegistry] = None,
) -> Verifier:
    """Create a verifier with default configuration."""
    return Verifier(settings=settings, skill_registry=skill_registry)

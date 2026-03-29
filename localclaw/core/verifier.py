"""Verifier module for validation and security checks."""

from abc import ABC, abstractmethod
from enum import Enum
import re
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
    LOW_RISK_BROWSER_PATTERNS = (
        r"(?:浠婂ぉ|浠婃棩|鏄庡ぉ|鍚庡ぉ|鍛ㄥ嚑|鏄熸湡|鍑犲彿|鏃ユ湡|鏃堕棿|鍑犵偣|澶╂皵|姘旀俯|娓╁害|涓嬮洦|涓嬮洩)",
        r"\b(?:today|tomorrow|yesterday|date|day|time|weekday|weather|forecast|temperature|rain|snow|hot|cold)\b",
    )
    INFO_LOOKUP_MARKERS = (
        "查询",
        "查一下",
        "查下",
        "查查",
        "搜索",
        "搜一下",
        "搜下",
        "看看",
        "看下",
        "了解",
        "告诉我",
        "帮我查",
        "help me check",
        "look up",
        "search",
        "find",
        "tell me",
        "check",
    )
    BROWSER_MUTATION_MARKERS = (
        "鐧诲綍",
        "娉ㄥ唽",
        "鏀粯",
        "璐拱",
        "涓嬪崟",
        "涓婁紶",
        "涓嬭浇",
        "鍒犻櫎",
        "鎻愪氦",
        "濉啓",
        "鐐瑰嚮",
        "click",
        "submit",
        "login",
        "sign in",
        "upload",
        "download",
        "delete",
        "remove",
        "buy",
        "purchase",
        "checkout",
        "transfer",
        "set_files",
        "eval",
        "script",
    )
    
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
        self._auto_approve_readonly_browser_cdp = bool(
            getattr(self._settings, "browser_cdp_auto_approve_readonly", True)
        )

    @staticmethod
    def _is_question_like(text: str) -> bool:
        """Best-effort detection for informational user questions."""

        if not text:
            return False
        stripped = text.strip()
        if "?" in text or "？" in text or "锛?" in text:
            return True
        lowered = stripped.lower()
        if any(token in lowered for token in ("what", "when", "how", "which", "who", "where", "is it", "can i")):
            return True
        return any(token in stripped for token in ("吗", "么", "嘛", "呢", "是否", "是不是", "可否"))

    def _is_information_lookup_like(self, text: str) -> bool:
        """Detect non-question lookup phrasing like '查下明天天气'."""

        stripped = (text or "").strip()
        if not stripped:
            return False
        lowered = stripped.lower()
        if any(marker in stripped for marker in self.INFO_LOOKUP_MARKERS):
            return True
        return any(marker in lowered for marker in self.INFO_LOOKUP_MARKERS)

    def _is_low_risk_browser_cdp_request(self, step: Step) -> bool:
        """Classify read-only browser queries that can skip confirmation prompts."""

        if (step.tool_name or "").strip() != "browser_cdp":
            return False

        payload = step.input or {}
        action = str(payload.get("action") or "agent").strip().lower()
        if action in {"check", "targets", "info", "fetch"}:
            return True
        if action != "agent":
            return False

        request_text = str(payload.get("request") or "").strip()
        url_text = str(payload.get("url") or "").strip()
        combined = f"{request_text} {url_text}".strip().lower()
        if not combined:
            return False

        if any(marker in combined for marker in self.BROWSER_MUTATION_MARKERS):
            return False

        if url_text and not re.match(r"^https?://", url_text, re.IGNORECASE):
            return False

        if not (self._is_question_like(request_text) or self._is_information_lookup_like(request_text)):
            return False

        # Fast-path: common low-risk "clock/weather" questions.
        chinese_low_risk_markers = (
            "今天",
            "明天",
            "后天",
            "周几",
            "星期",
            "日期",
            "时间",
            "几点",
            "天气",
            "气温",
            "温度",
            "下雨",
            "下雪",
        )
        if any(marker in request_text for marker in chinese_low_risk_markers):
            return True
        if any(re.search(pattern, combined, re.IGNORECASE) for pattern in self.LOW_RISK_BROWSER_PATTERNS):
            return True

        # General policy: informational lookups should not require approval.
        # High-risk/interactive intents are screened out earlier by mutation markers and URL validation.
        return True

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
        if str(metadata.get("catalog_source") or "").strip().lower() == "bundled":
            return {}
        guard = metadata.get("localclaw_guard", {})
        return dict(guard) if isinstance(guard, dict) else {}

    def _skill_requires_human_approval(self, step: Step) -> bool:
        """Return whether the source skill is configured to always ask for approval."""

        skill_name = (step.source_skill_name or "").strip()
        if not skill_name:
            return False
        return self._skill_registry.get_skill_approval_required(skill_name)
    
    def get_risk_level(self, step: Step) -> RiskLevel:
        """Determine the risk level of a step."""
        if step.type.value == "tool_call":
            tool_name = step.tool_name or ""
            if tool_name in self.CRITICAL_TOOLS:
                return RiskLevel.CRITICAL
            if tool_name == "browser_cdp":
                if self._auto_approve_readonly_browser_cdp and self._is_low_risk_browser_cdp_request(step):
                    return RiskLevel.LOW
                return RiskLevel.HIGH
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

        if (
            step.type.value == "tool_call"
            and self._skill_requires_human_approval(step)
            and not self.is_approved(step.id)
        ):
            return VerificationResult.ask_human_result(
                f"Skill '{step.source_skill_name}' is configured to require approval before running tools",
                {
                    "tool_name": step.tool_name,
                    "source_skill_name": step.source_skill_name,
                    "approval_policy": "per_skill",
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


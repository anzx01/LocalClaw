"""Human-In-The-Loop (HITL) approval system."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from localclaw.core.models import RiskLevel, Step


logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    """Status of an approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class ApprovalRequest:
    """A request for human approval."""
    request_id: str
    step: Step
    risk_level: RiskLevel
    reason: str
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_expired(self) -> bool:
        """Check if this request has expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at
    
    def approve(self, approver: Optional[str] = None) -> None:
        """Approve this request."""
        self.status = ApprovalStatus.APPROVED
        self.approved_by = approver
        self.approved_at = datetime.now()
    
    def reject(self, reason: Optional[str] = None, rejector: Optional[str] = None) -> None:
        """Reject this request."""
        self.status = ApprovalStatus.REJECTED
        self.rejection_reason = reason
        self.approved_by = rejector
        self.approved_at = datetime.now()
    
    def cancel(self) -> None:
        """Cancel this request."""
        self.status = ApprovalStatus.CANCELLED
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "request_id": self.request_id,
            "step_id": self.step.id,
            "step_name": self.step.name,
            "step_type": self.step.type.value,
            "risk_level": self.risk_level.value,
            "reason": self.reason,
            "context": self.context,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status.value,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
        }


class HITLManager:
    """Manager for Human-In-The-Loop approvals."""
    
    DEFAULT_EXPIRY_MINUTES = 30
    HIGH_RISK_EXPIRY_MINUTES = 60
    
    def __init__(self) -> None:
        self._requests: Dict[str, ApprovalRequest] = {}
        self._pending_queue: asyncio.Queue = asyncio.Queue()
        self._callbacks: Dict[str, List[Callable]] = {
            "on_request": [],
            "on_approve": [],
            "on_reject": [],
            "on_expire": [],
        }
        self._auto_approve_low_risk = False
        self._logger = logging.getLogger("localclaw.security.hitl")
    
    def set_auto_approve_low_risk(self, enabled: bool) -> None:
        """Enable or disable auto-approval for low risk operations."""
        self._auto_approve_low_risk = enabled
    
    def requires_approval(self, step: Step, risk_level: RiskLevel) -> bool:
        """Check if a step requires human approval."""
        if risk_level == RiskLevel.LOW and self._auto_approve_low_risk:
            return False
        
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return True
        
        if step.type.value == "tool_call":
            tool_name = step.tool_name or ""
            if "delete" in tool_name or "shell" in tool_name:
                return True
        
        return False
    
    async def request_approval(
        self,
        step: Step,
        risk_level: RiskLevel,
        reason: str,
        context: Optional[Dict[str, Any]] = None,
        expiry_minutes: Optional[int] = None,
    ) -> ApprovalRequest:
        """Request approval for a step."""
        import uuid
        request_id = str(uuid.uuid4())[:8]
        
        if expiry_minutes is None:
            expiry_minutes = self.HIGH_RISK_EXPIRY_MINUTES if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) else self.DEFAULT_EXPIRY_MINUTES
        
        request = ApprovalRequest(
            request_id=request_id,
            step=step,
            risk_level=risk_level,
            reason=reason,
            context=context or {},
            expires_at=datetime.now() + timedelta(minutes=expiry_minutes),
        )
        
        self._requests[request_id] = request
        await self._pending_queue.put(request)
        
        self._logger.info(f"Created approval request: {request_id} for step {step.id}")
        
        for callback in self._callbacks["on_request"]:
            try:
                result = callback(request)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                self._logger.error(f"Callback error: {e}")
        
        return request
    
    def approve(self, request_id: str, approver: Optional[str] = None) -> bool:
        """Approve a pending request."""
        request = self._requests.get(request_id)
        if not request or request.status != ApprovalStatus.PENDING:
            return False
        
        request.approve(approver)
        self._logger.info(f"Approved request: {request_id}")
        
        for callback in self._callbacks["on_approve"]:
            try:
                callback(request)
            except Exception as e:
                self._logger.error(f"Callback error: {e}")
        
        return True
    
    def reject(self, request_id: str, reason: Optional[str] = None, rejector: Optional[str] = None) -> bool:
        """Reject a pending request."""
        request = self._requests.get(request_id)
        if not request or request.status != ApprovalStatus.PENDING:
            return False
        
        request.reject(reason, rejector)
        self._logger.info(f"Rejected request: {request_id}")
        
        for callback in self._callbacks["on_reject"]:
            try:
                callback(request)
            except Exception as e:
                self._logger.error(f"Callback error: {e}")
        
        return True
    
    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Get a request by ID."""
        return self._requests.get(request_id)
    
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """Get all pending requests."""
        return [
            r for r in self._requests.values()
            if r.status == ApprovalStatus.PENDING and not r.is_expired()
        ]
    
    def cleanup_expired(self) -> int:
        """Mark expired requests and return count."""
        count = 0
        for request in self._requests.values():
            if request.status == ApprovalStatus.PENDING and request.is_expired():
                request.status = ApprovalStatus.EXPIRED
                count += 1
                
                for callback in self._callbacks["on_expire"]:
                    try:
                        callback(request)
                    except Exception as e:
                        self._logger.error(f"Callback error: {e}")
        
        return count
    
    def on_request(self, callback: Callable) -> None:
        """Register a callback for new approval requests."""
        self._callbacks["on_request"].append(callback)
    
    def on_approve(self, callback: Callable) -> None:
        """Register a callback for approved requests."""
        self._callbacks["on_approve"].append(callback)
    
    def on_reject(self, callback: Callable) -> None:
        """Register a callback for rejected requests."""
        self._callbacks["on_reject"].append(callback)
    
    def on_expire(self, callback: Callable) -> None:
        """Register a callback for expired requests."""
        self._callbacks["on_expire"].append(callback)
    
    async def wait_for_approval(self, request_id: str, timeout: Optional[float] = None) -> ApprovalStatus:
        """Wait for a request to be approved or rejected."""
        start_time = datetime.now()
        
        while True:
            request = self._requests.get(request_id)
            if not request:
                return ApprovalStatus.CANCELLED
            
            if request.status != ApprovalStatus.PENDING:
                return request.status
            
            if request.is_expired():
                request.status = ApprovalStatus.EXPIRED
                return ApprovalStatus.EXPIRED
            
            if timeout:
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed >= timeout:
                    return ApprovalStatus.PENDING
            
            await asyncio.sleep(0.5)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get approval statistics."""
        requests = list(self._requests.values())
        
        return {
            "total_requests": len(requests),
            "pending": len([r for r in requests if r.status == ApprovalStatus.PENDING]),
            "approved": len([r for r in requests if r.status == ApprovalStatus.APPROVED]),
            "rejected": len([r for r in requests if r.status == ApprovalStatus.REJECTED]),
            "expired": len([r for r in requests if r.status == ApprovalStatus.EXPIRED]),
        }


_hitl_manager: Optional[HITLManager] = None


def get_hitl_manager() -> HITLManager:
    """Get the global HITL manager."""
    global _hitl_manager
    if _hitl_manager is None:
        _hitl_manager = HITLManager()
    return _hitl_manager

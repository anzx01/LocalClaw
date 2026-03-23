"""Security module."""

from localclaw.security.permissions import (
    Permission,
    PermissionSet,
    PermissionManager,
    Role,
    get_permission_manager,
)
from localclaw.security.audit import (
    AuditLogger,
    AuditEntry,
    get_audit_logger,
)
from localclaw.security.sandbox import (
    SandboxConfig,
    SandboxExecutor,
    get_sandbox_executor,
)
from localclaw.security.hitl import (
    ApprovalRequest,
    ApprovalStatus,
    HITLManager,
    get_hitl_manager,
)

__all__ = [
    "Permission",
    "PermissionSet",
    "PermissionManager",
    "Role",
    "get_permission_manager",
    "AuditLogger",
    "AuditEntry",
    "get_audit_logger",
    "SandboxConfig",
    "SandboxExecutor",
    "get_sandbox_executor",
    "ApprovalRequest",
    "ApprovalStatus",
    "HITLManager",
    "get_hitl_manager",
]

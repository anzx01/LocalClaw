"""Permission control module."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from localclaw.core.models import RiskLevel, Step


class Permission(str, Enum):
    """Permission types."""
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    SHELL_EXECUTE = "shell_execute"
    HTTP_REQUEST = "http_request"
    NETWORK_ACCESS = "network_access"
    MEMORY_WRITE = "memory_write"
    SKILL_EXECUTE = "skill_execute"


@dataclass
class PermissionSet:
    """A set of permissions with constraints."""
    allowed: Set[Permission] = field(default_factory=set)
    denied: Set[Permission] = field(default_factory=set)
    constraints: Dict[str, Any] = field(default_factory=dict)
    
    def is_allowed(self, permission: Permission) -> bool:
        """Check if a permission is allowed."""
        if permission in self.denied:
            return False
        return permission in self.allowed
    
    def allow(self, *permissions: Permission) -> None:
        """Grant permissions."""
        for p in permissions:
            self.allowed.add(p)
            self.denied.discard(p)
    
    def deny(self, *permissions: Permission) -> None:
        """Deny permissions."""
        for p in permissions:
            self.denied.add(p)
            self.allowed.discard(p)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "allowed": [p.value for p in self.allowed],
            "denied": [p.value for p in self.denied],
            "constraints": self.constraints,
        }


@dataclass
class Role:
    """A role with associated permissions."""
    name: str
    permissions: PermissionSet
    max_risk_level: RiskLevel = RiskLevel.HIGH
    description: str = ""
    
    def can_execute(self, step: Step, risk_level: RiskLevel) -> bool:
        """Check if this role can execute a step."""
        if risk_level.value > self.max_risk_level.value:
            return False
        
        required_permission = self._get_required_permission(step)
        if required_permission is None:
            return True
        
        return self.permissions.is_allowed(required_permission)
    
    def _get_required_permission(self, step: Step) -> Optional[Permission]:
        """Get the permission required for a step."""
        if step.type.value == "tool_call":
            tool_name = step.tool_name or ""
            if tool_name.startswith("file_read"):
                return Permission.FILE_READ
            elif tool_name.startswith("file_write"):
                return Permission.FILE_WRITE
            elif tool_name.startswith("file_delete"):
                return Permission.FILE_DELETE
            elif tool_name in ("shell", "safe_shell"):
                return Permission.SHELL_EXECUTE
            elif tool_name == "browser_cdp":
                return Permission.NETWORK_ACCESS
            elif tool_name.startswith("http"):
                return Permission.HTTP_REQUEST
        
        if step.type.value == "skill_call":
            return Permission.SKILL_EXECUTE
        
        return None


class PermissionManager:
    """Manages permissions and roles."""
    
    DEFAULT_ROLE = "user"
    ADMIN_ROLE = "admin"
    
    def __init__(self) -> None:
        self._roles: Dict[str, Role] = {}
        self._user_roles: Dict[str, str] = {}
        self._risk_overrides: Dict[str, RiskLevel] = {}
        
        self._create_default_roles()
    
    def _create_default_roles(self) -> None:
        """Create default roles."""
        admin_perms = PermissionSet()
        admin_perms.allow(*list(Permission))
        
        self._roles[self.ADMIN_ROLE] = Role(
            name=self.ADMIN_ROLE,
            permissions=admin_perms,
            max_risk_level=RiskLevel.CRITICAL,
            description="Administrator with full permissions",
        )
        
        user_perms = PermissionSet()
        user_perms.allow(
            Permission.FILE_READ,
            Permission.HTTP_REQUEST,
            Permission.SKILL_EXECUTE,
            Permission.MEMORY_WRITE,
        )
        
        self._roles[self.DEFAULT_ROLE] = Role(
            name=self.DEFAULT_ROLE,
            permissions=user_perms,
            max_risk_level=RiskLevel.HIGH,
            description="Default user role",
        )
    
    def create_role(self, name: str, permissions: PermissionSet, max_risk_level: RiskLevel = RiskLevel.MEDIUM) -> Role:
        """Create a new role."""
        role = Role(name=name, permissions=permissions, max_risk_level=max_risk_level)
        self._roles[name] = role
        return role
    
    def get_role(self, name: str) -> Optional[Role]:
        """Get a role by name."""
        return self._roles.get(name)
    
    def assign_role(self, user_id: str, role_name: str) -> bool:
        """Assign a role to a user."""
        if role_name not in self._roles:
            return False
        self._user_roles[user_id] = role_name
        return True
    
    def get_user_role(self, user_id: str) -> Role:
        """Get the role for a user."""
        role_name = self._user_roles.get(user_id, self.DEFAULT_ROLE)
        return self._roles.get(role_name) or self._roles[self.DEFAULT_ROLE]
    
    def can_execute(self, user_id: str, step: Step, risk_level: RiskLevel) -> bool:
        """Check if a user can execute a step."""
        role = self.get_user_role(user_id)
        return role.can_execute(step, risk_level)
    
    def set_risk_override(self, user_id: str, risk_level: RiskLevel) -> None:
        """Set a risk level override for a user."""
        self._risk_overrides[user_id] = risk_level
    
    def get_effective_risk_level(self, user_id: str, default: RiskLevel) -> RiskLevel:
        """Get the effective risk level for a user."""
        return self._risk_overrides.get(user_id, default)
    
    def list_roles(self) -> List[str]:
        """List all role names."""
        return list(self._roles.keys())


_permission_manager: Optional[PermissionManager] = None


def get_permission_manager() -> PermissionManager:
    """Get the global permission manager."""
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = PermissionManager()
    return _permission_manager

"""Audit logging module."""

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    """An audit log entry."""
    id: str = field(default_factory=lambda: str(uuid4())[:8])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = ""
    user_id: str = "system"
    channel: str = "system"
    task_id: Optional[str] = None
    step_id: Optional[str] = None
    skill_name: Optional[str] = None
    tool_name: Optional[str] = None
    action: str = ""
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    error: Optional[str] = None
    risk_level: str = "low"
    approved: bool = False
    approved_by: Optional[str] = None
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "user_id": self.user_id,
            "channel": self.channel,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "skill_name": self.skill_name,
            "tool_name": self.tool_name,
            "action": self.action,
            "input_data": self._sanitize(self.input_data),
            "output_data": self._sanitize(self.output_data),
            "status": self.status,
            "error": self.error,
            "risk_level": self.risk_level,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }
    
    def _sanitize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize sensitive data."""
        sensitive_keys = {"password", "token", "secret", "key", "credential", "api_key"}
        sanitized = {}
        
        for key, value in data.items():
            if key.lower() in sensitive_keys:
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize(value)
            else:
                sanitized[key] = value
        
        return sanitized
    
    def to_jsonl(self) -> str:
        """Convert to JSON Lines format."""
        return json.dumps(self.to_dict())


class AuditLogger:
    """Audit logger for recording all operations."""
    
    def __init__(self, log_file: Optional[Path] = None, max_entries: int = 10000) -> None:
        self._log_file = log_file
        self._max_entries = max_entries
        self._entries: deque = deque(maxlen=max_entries)
        self._file_handle: Optional[Any] = None
        self._logger = logging.getLogger("localclaw.audit")
    
    def set_log_file(self, path: Path) -> None:
        """Set the log file path."""
        self._log_file = path
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
    
    def log(
        self,
        event_type: str,
        action: str,
        user_id: str = "system",
        channel: str = "system",
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        status: str = "success",
        error: Optional[str] = None,
        risk_level: str = "low",
        approved: bool = False,
        approved_by: Optional[str] = None,
        duration_ms: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Log an audit entry."""
        entry = AuditEntry(
            event_type=event_type,
            action=action,
            user_id=user_id,
            channel=channel,
            task_id=task_id,
            step_id=step_id,
            skill_name=skill_name,
            tool_name=tool_name,
            input_data=input_data or {},
            output_data=output_data or {},
            status=status,
            error=error,
            risk_level=risk_level,
            approved=approved,
            approved_by=approved_by,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )
        
        self._entries.append(entry)
        
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]
        
        self._write_entry(entry)
        
        self._logger.info("Audit: %s - %s [%s]", event_type, action, status)
        
        return entry
    
    def log_task_start(self, task_id: str, user_id: str, channel: str, message: str) -> AuditEntry:
        """Log task start."""
        return self.log(
            event_type="task",
            action="start",
            user_id=user_id,
            channel=channel,
            task_id=task_id,
            input_data={"message": message},
            status="started",
        )
    
    def log_task_complete(self, task_id: str, user_id: str, status: str, result: Dict[str, Any]) -> AuditEntry:
        """Log task completion."""
        return self.log(
            event_type="task",
            action="complete",
            user_id=user_id,
            task_id=task_id,
            output_data=result,
            status=status,
        )
    
    def log_step_execution(
        self,
        task_id: str,
        step_id: str,
        step_type: str,
        tool_name: Optional[str],
        skill_name: Optional[str],
        input_data: Dict[str, Any],
        output_data: Dict[str, Any],
        status: str,
        error: Optional[str],
        risk_level: str,
        duration_ms: float,
    ) -> AuditEntry:
        """Log step execution."""
        return self.log(
            event_type="step",
            action="execute",
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            skill_name=skill_name,
            input_data=input_data,
            output_data=output_data,
            status=status,
            error=error,
            risk_level=risk_level,
            duration_ms=duration_ms,
        )
    
    def log_approval(self, step_id: str, user_id: str, approved: bool, approver: str) -> AuditEntry:
        """Log an approval decision."""
        return self.log(
            event_type="approval",
            action="approve" if approved else "reject",
            step_id=step_id,
            user_id=user_id,
            approved=approved,
            approved_by=approver,
            status="approved" if approved else "rejected",
        )
    
    def log_skill_load(self, skill_name: str, version: str) -> AuditEntry:
        """Log skill loading."""
        return self.log(
            event_type="skill",
            action="load",
            skill_name=skill_name,
            metadata={"version": version},
        )
    
    def log_tool_execution(self, tool_name: str, input_data: Dict[str, Any], output_data: Dict[str, Any], status: str) -> AuditEntry:
        """Log tool execution."""
        return self.log(
            event_type="tool",
            action="execute",
            tool_name=tool_name,
            input_data=input_data,
            output_data=output_data,
            status=status,
        )
    
    def _write_entry(self, entry: AuditEntry) -> None:
        """Write an entry to the log file."""
        if self._log_file is None:
            return
        
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(entry.to_jsonl() + "\n")
        except Exception as e:
            self._logger.error(f"Failed to write audit entry: {e}")
    
    def get_entries(
        self,
        event_type: Optional[str] = None,
        user_id: Optional[str] = None,
        task_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """Get filtered audit entries."""
        entries = self._entries
        
        if event_type:
            entries = [e for e in entries if e.event_type == event_type]
        if user_id:
            entries = [e for e in entries if e.user_id == user_id]
        if task_id:
            entries = [e for e in entries if e.task_id == task_id]
        if status:
            entries = [e for e in entries if e.status == status]
        
        return entries[-limit:]
    
    def export(self, path: Path, format: str = "jsonl") -> None:
        """Export audit log to a file."""
        if format == "jsonl":
            with open(path, "w", encoding="utf-8") as f:
                for entry in self._entries:
                    f.write(entry.to_jsonl() + "\n")
        elif format == "json":
            with open(path, "w", encoding="utf-8") as f:
                json.dump([e.to_dict() for e in self._entries], f, indent=2)
        else:
            raise ValueError(f"Unsupported format: {format}")


_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def configure_audit_logger(log_file: Path) -> None:
    """Configure the global audit logger."""
    global _audit_logger
    _audit_logger = AuditLogger(log_file=log_file)

"""Short-term memory for session-based storage."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class MemoryEntry:
    """A single memory entry."""
    key: str
    value: Any
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    ttl_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_expired(self) -> bool:
        """Check if this entry has expired."""
        if self.ttl_seconds is None:
            return False
        
        elapsed = (datetime.now() - self.updated_at).total_seconds()
        return elapsed > self.ttl_seconds


class ShortTermMemory:
    """Short-term memory for session-based storage."""
    
    def __init__(self, session_id: str = "default") -> None:
        self._session_id = session_id
        self._entries: Dict[str, MemoryEntry] = {}
        self._created_at = datetime.now()
    
    @property
    def session_id(self) -> str:
        """Get the session ID."""
        return self._session_id
    
    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Set a value in memory."""
        existing = self._entries.get(key)
        
        if existing:
            existing.value = value
            existing.updated_at = datetime.now()
            if ttl_seconds is not None:
                existing.ttl_seconds = ttl_seconds
            if metadata:
                existing.metadata.update(metadata)
        else:
            self._entries[key] = MemoryEntry(
                key=key,
                value=value,
                ttl_seconds=ttl_seconds,
                metadata=metadata or {},
            )
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from memory."""
        entry = self._entries.get(key)
        
        if entry is None:
            return default
        
        if entry.is_expired():
            del self._entries[key]
            return default
        
        return entry.value
    
    def delete(self, key: str) -> bool:
        """Delete a value from memory."""
        if key in self._entries:
            del self._entries[key]
            return True
        return False
    
    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        entry = self._entries.get(key)
        if entry is None:
            return False
        if entry.is_expired():
            del self._entries[key]
            return False
        return True
    
    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
    
    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        expired_keys = [k for k, v in self._entries.items() if v.is_expired()]
        for key in expired_keys:
            del self._entries[key]
        return len(expired_keys)
    
    def keys(self) -> list:
        """Get all non-expired keys."""
        self.cleanup_expired()
        return list(self._entries.keys())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        self.cleanup_expired()
        return {k: v.value for k, v in self._entries.items()}
    
    def get_metadata(self, key: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a key."""
        entry = self._entries.get(key)
        if entry is None or entry.is_expired():
            return None
        return entry.metadata.copy()


class SessionManager:
    """Manages multiple sessions."""
    
    def __init__(self) -> None:
        self._sessions: Dict[str, ShortTermMemory] = {}
    
    def get_session(self, session_id: str) -> ShortTermMemory:
        """Get or create a session."""
        if session_id not in self._sessions:
            self._sessions[session_id] = ShortTermMemory(session_id)
        return self._sessions[session_id]
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False
    
    def list_sessions(self) -> list:
        """List all session IDs."""
        return list(self._sessions.keys())
    
    def cleanup_all_expired(self) -> int:
        """Cleanup expired entries in all sessions."""
        total = 0
        for session in self._sessions.values():
            total += session.cleanup_expired()
        return total

    async def start_cleanup_task(self, interval_seconds: float = 60.0) -> asyncio.Task:
        """Start a background task that periodically removes expired entries."""
        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval_seconds)
                self.cleanup_all_expired()

        return asyncio.create_task(_loop())

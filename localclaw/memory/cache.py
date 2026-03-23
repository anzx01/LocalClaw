"""Cache memory for storing computed results."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class CacheEntry:
    """A cache entry."""
    key: str
    value: Any
    created_at: datetime = field(default_factory=datetime.now)
    ttl_seconds: Optional[int] = None
    hits: int = 0
    
    def is_expired(self) -> bool:
        """Check if this entry has expired."""
        if self.ttl_seconds is None:
            return False
        
        elapsed = (datetime.now() - self.created_at).total_seconds()
        return elapsed > self.ttl_seconds


class CacheMemory:
    """Cache for storing computed results to avoid redundant operations."""
    
    def __init__(self, default_ttl: Optional[int] = 3600) -> None:
        self._entries: Dict[str, CacheEntry] = {}
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0
    
    def _generate_key(self, *args, **kwargs) -> str:
        """Generate a cache key from arguments."""
        key_data = {
            "args": args,
            "kwargs": kwargs,
        }
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.sha256(key_str.encode()).hexdigest()[:16]
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache."""
        entry = self._entries.get(key)
        
        if entry is None:
            self._misses += 1
            return None
        
        if entry.is_expired():
            del self._entries[key]
            self._misses += 1
            return None
        
        entry.hits += 1
        self._hits += 1
        return entry.value
    
    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Set a value in cache."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        
        self._entries[key] = CacheEntry(
            key=key,
            value=value,
            ttl_seconds=ttl,
        )
    
    def delete(self, key: str) -> bool:
        """Delete a value from cache."""
        if key in self._entries:
            del self._entries[key]
            return True
        return False
    
    def exists(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        entry = self._entries.get(key)
        if entry is None:
            return False
        if entry.is_expired():
            del self._entries[key]
            return False
        return True
    
    def clear(self) -> None:
        """Clear all cache entries."""
        self._entries.clear()
        self._hits = 0
        self._misses = 0
    
    def cleanup_expired(self) -> int:
        """Remove expired entries."""
        expired_keys = [k for k, v in self._entries.items() if v.is_expired()]
        for key in expired_keys:
            del self._entries[key]
        return len(expired_keys)
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self._hits + self._misses
        hit_rate = self._hits / total_requests if total_requests > 0 else 0
        
        return {
            "entries": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
        }
    
    def cache_result(self, ttl_seconds: Optional[int] = None):
        """Decorator to cache function results."""
        def decorator(func):
            def wrapper(*args, **kwargs):
                key = self._generate_key(func.__name__, *args, **kwargs)
                
                cached = self.get(key)
                if cached is not None:
                    return cached
                
                result = func(*args, **kwargs)
                self.set(key, result, ttl_seconds)
                return result
            
            return wrapper
        return decorator


_cache: Optional[CacheMemory] = None


def get_cache() -> CacheMemory:
    """Get the global cache instance."""
    global _cache
    if _cache is None:
        _cache = CacheMemory()
    return _cache

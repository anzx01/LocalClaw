"""Tests for memory modules."""

import asyncio
import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import os

from localclaw.memory.short_term import ShortTermMemory, SessionManager
from localclaw.memory.cache import CacheMemory, CacheEntry
from localclaw.memory.long_term import LongTermMemory


class TestShortTermMemory:
    """Tests for ShortTermMemory."""
    
    def test_set_and_get(self):
        """Test basic set and get operations."""
        memory = ShortTermMemory()
        
        memory.set("key1", "value1")
        assert memory.get("key1") == "value1"
        
        memory.set("key2", {"nested": "data"})
        assert memory.get("key2") == {"nested": "data"}
    
    def test_get_nonexistent(self):
        """Test getting nonexistent key."""
        memory = ShortTermMemory()
        
        assert memory.get("nonexistent") is None
        assert memory.get("nonexistent", "default") == "default"
    
    def test_delete(self):
        """Test delete operation."""
        memory = ShortTermMemory()
        
        memory.set("key1", "value1")
        assert memory.delete("key1") is True
        assert memory.get("key1") is None
        assert memory.delete("key1") is False
    
    def test_exists(self):
        """Test exists operation."""
        memory = ShortTermMemory()
        
        assert memory.exists("key1") is False
        memory.set("key1", "value1")
        assert memory.exists("key1") is True
    
    def test_clear(self):
        """Test clear operation."""
        memory = ShortTermMemory()
        
        memory.set("key1", "value1")
        memory.set("key2", "value2")
        memory.clear()
        
        assert memory.get("key1") is None
        assert memory.get("key2") is None
    
    def test_ttl_expiration(self):
        """Test TTL expiration."""
        memory = ShortTermMemory()
        
        memory.set("key1", "value1", ttl_seconds=1)
        assert memory.get("key1") == "value1"
        
        import time
        time.sleep(1.1)
        
        assert memory.get("key1") is None
    
    def test_cleanup_expired(self):
        """Test cleanup of expired entries."""
        memory = ShortTermMemory()
        
        memory.set("key1", "value1", ttl_seconds=1)
        memory.set("key2", "value2")
        
        import time
        time.sleep(1.1)
        
        count = memory.cleanup_expired()
        assert count == 1
        assert memory.exists("key2") is True
    
    def test_keys(self):
        """Test keys operation."""
        memory = ShortTermMemory()
        
        assert memory.keys() == []
        
        memory.set("key1", "value1")
        memory.set("key2", "value2")
        
        keys = memory.keys()
        assert set(keys) == {"key1", "key2"}
    
    def test_to_dict(self):
        """Test to_dict operation."""
        memory = ShortTermMemory()
        
        memory.set("key1", "value1")
        memory.set("key2", "value2")
        
        d = memory.to_dict()
        assert d == {"key1": "value1", "key2": "value2"}
    
    def test_metadata(self):
        """Test metadata storage."""
        memory = ShortTermMemory()
        
        memory.set("key1", "value1", metadata={"source": "test"})
        
        meta = memory.get_metadata("key1")
        assert meta == {"source": "test"}


class TestSessionManager:
    """Tests for SessionManager."""
    
    def test_get_session(self):
        """Test getting sessions."""
        manager = SessionManager()
        
        session1 = manager.get_session("session1")
        assert session1.session_id == "session1"
        
        session1_again = manager.get_session("session1")
        assert session1 is session1_again
    
    def test_delete_session(self):
        """Test deleting sessions."""
        manager = SessionManager()
        
        manager.get_session("session1")
        assert manager.delete_session("session1") is True
        assert manager.delete_session("nonexistent") is False
    
    def test_list_sessions(self):
        """Test listing sessions."""
        manager = SessionManager()
        
        assert manager.list_sessions() == []
        
        manager.get_session("session1")
        manager.get_session("session2")
        
        sessions = manager.list_sessions()
        assert set(sessions) == {"session1", "session2"}


class TestCacheMemory:
    """Tests for CacheMemory."""
    
    def test_set_and_get(self):
        """Test basic cache operations."""
        cache = CacheMemory()
        
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
    
    def test_cache_key_generation(self):
        """Test cache key generation."""
        cache = CacheMemory()
        
        key1 = cache._generate_key("func", "arg1", "arg2")
        key2 = cache._generate_key("func", "arg1", "arg2")
        key3 = cache._generate_key("func", "arg1", "arg3")
        
        assert key1 == key2
        assert key1 != key3
    
    def test_cache_with_kwargs(self):
        """Test cache key with kwargs."""
        cache = CacheMemory()
        
        key1 = cache._generate_key("func", a=1, b=2)
        key2 = cache._generate_key("func", b=2, a=1)
        
        assert key1 == key2
    
    def test_cache_stats(self):
        """Test cache statistics."""
        cache = CacheMemory()
        
        cache.set("key1", "value1")
        cache.get("key1")
        cache.get("key1")
        cache.get("nonexistent")
        
        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["entries"] == 1
    
    def test_cache_clear(self):
        """Test cache clear."""
        cache = CacheMemory()
        
        cache.set("key1", "value1")
        cache.clear()
        
        assert cache.get("key1") is None
    
    def test_cache_ttl(self):
        """Test cache TTL."""
        cache = CacheMemory(default_ttl=1)
        
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
        
        import time
        time.sleep(1.1)
        
        assert cache.get("key1") is None
    
    def test_cache_decorator(self):
        """Test cache_result decorator."""
        cache = CacheMemory()
        call_count = 0
        
        @cache.cache_result()
        def expensive_function(x):
            nonlocal call_count
            call_count += 1
            return x * 2
        
        result1 = expensive_function(5)
        result2 = expensive_function(5)
        
        assert result1 == 10
        assert result2 == 10
        assert call_count == 1


class TestLongTermMemory:
    """Tests for LongTermMemory."""
    
    @pytest.fixture
    async def memory(self, tmp_path):
        """Create a temporary memory instance."""
        db_path = tmp_path / "test_memory.db"
        mem = LongTermMemory(db_path)
        await mem.initialize()
        yield mem
        await mem.close()
    
    @pytest.mark.asyncio
    async def test_set_and_get(self, memory):
        """Test basic set and get operations."""
        await memory.set("key1", "value1")
        
        result = await memory.get("key1")
        assert result == "value1"
    
    @pytest.mark.asyncio
    async def test_get_nonexistent(self, memory):
        """Test getting nonexistent key."""
        result = await memory.get("nonexistent")
        assert result is None
        
        result = await memory.get("nonexistent", "default")
        assert result == "default"
    
    @pytest.mark.asyncio
    async def test_delete(self, memory):
        """Test delete operation."""
        await memory.set("key1", "value1")
        
        result = await memory.delete("key1")
        assert result is True
        
        result = await memory.get("key1")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_set_with_metadata(self, memory):
        """Test setting value with metadata."""
        await memory.set("key1", "value1", metadata={"source": "test"})
        
        metadata = await memory.get_metadata("key1")
        assert metadata == {"source": "test"}
    
    @pytest.mark.asyncio
    async def test_search(self, memory):
        """Test search functionality."""
        await memory.set("apple", {"name": "apple", "type": "fruit"})
        await memory.set("banana", {"name": "banana", "type": "fruit"})
        await memory.set("carrot", {"name": "carrot", "type": "vegetable"})
        
        results = await memory.search("fruit")
        assert len(results) == 2
    
    @pytest.mark.asyncio
    async def test_keys(self, memory):
        """Test listing keys."""
        await memory.set("key1", "value1")
        await memory.set("key2", "value2")
        
        keys = await memory.keys()
        assert set(keys) == {"key1", "key2"}
    
    @pytest.mark.asyncio
    async def test_count(self, memory):
        """Test count operation."""
        assert await memory.count() == 0
        
        await memory.set("key1", "value1")
        await memory.set("key2", "value2")
        
        assert await memory.count() == 2
    
    @pytest.mark.asyncio
    async def test_exists(self, memory):
        """Test exists operation."""
        assert await memory.exists("key1") is False
        
        await memory.set("key1", "value1")
        assert await memory.exists("key1") is True
    
    @pytest.mark.asyncio
    async def test_json_values(self, memory):
        """Test storing JSON values."""
        await memory.set("key1", {"nested": {"data": [1, 2, 3]}})
        
        result = await memory.get("key1")
        assert result == {"nested": {"data": [1, 2, 3]}}

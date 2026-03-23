"""Long-term memory with SQLite persistence."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite


logger = logging.getLogger(__name__)


class LongTermMemory:
    """Long-term memory with SQLite persistence."""
    
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._logger = logging.getLogger("localclaw.memory.long_term")
    
    async def initialize(self) -> None:
        """Initialize the database."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._db = await aiosqlite.connect(self._db_path)
        
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                value_type TEXT NOT NULL DEFAULT 'string',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT,
                embedding BLOB
            )
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON memory(created_at)
        """)
        
        await self._db.commit()
        self._logger.info(f"Initialized long-term memory at {self._db_path}")
    
    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
    
    async def set(
        self,
        key: str,
        value: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Set a value in long-term memory."""
        if self._db is None:
            await self.initialize()
        
        now = datetime.now().isoformat()
        
        value_type = type(value).__name__
        
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value)
            value_type = "json"
        else:
            value_str = str(value)
        
        metadata_str = json.dumps(metadata) if metadata else None
        
        await self._db.execute(
            """
            INSERT OR REPLACE INTO memory (key, value, value_type, created_at, updated_at, metadata)
            VALUES (?, ?, ?, COALESCE((SELECT created_at FROM memory WHERE key = ?), ?), ?, ?)
            """,
            (key, value_str, value_type, key, now, now, metadata_str),
        )
        
        await self._db.commit()
    
    async def get(self, key: str, default: Any = None) -> Any:
        """Get a value from long-term memory."""
        if self._db is None:
            await self.initialize()
        
        async with self._db.execute(
            "SELECT value, value_type FROM memory WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
            
            if row is None:
                return default
            
            value_str, value_type = row
            
            if value_type == "json":
                return json.loads(value_str)
            elif value_type == "int":
                return int(value_str)
            elif value_type == "float":
                return float(value_str)
            elif value_type == "bool":
                return value_str.lower() == "true"
            else:
                return value_str
    
    async def delete(self, key: str) -> bool:
        """Delete a value from long-term memory."""
        if self._db is None:
            await self.initialize()
        
        cursor = await self._db.execute("DELETE FROM memory WHERE key = ?", (key,))
        await self._db.commit()
        
        return cursor.rowcount > 0
    
    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        if self._db is None:
            await self.initialize()
        
        async with self._db.execute(
            "SELECT 1 FROM memory WHERE key = ?",
            (key,),
        ) as cursor:
            return await cursor.fetchone() is not None
    
    async def keys(self, pattern: Optional[str] = None) -> List[str]:
        """Get all keys, optionally matching a pattern."""
        if self._db is None:
            await self.initialize()
        
        if pattern:
            sql_pattern = pattern.replace("*", "%").replace("?", "_")
            async with self._db.execute(
                "SELECT key FROM memory WHERE key LIKE ?",
                (sql_pattern,),
            ) as cursor:
                return [row[0] for row in await cursor.fetchall()]
        else:
            async with self._db.execute("SELECT key FROM memory") as cursor:
                return [row[0] for row in await cursor.fetchall()]
    
    async def get_metadata(self, key: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a key."""
        if self._db is None:
            await self.initialize()
        
        async with self._db.execute(
            "SELECT metadata FROM memory WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
            
            if row is None or row[0] is None:
                return None
            
            return json.loads(row[0])
    
    async def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for entries containing the query."""
        if self._db is None:
            await self.initialize()
        
        async with self._db.execute(
            """
            SELECT key, value, value_type, metadata, created_at
            FROM memory
            WHERE key LIKE ? OR value LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit),
        ) as cursor:
            results = []
            for row in await cursor.fetchall():
                key, value, value_type, metadata, created_at = row
                results.append({
                    "key": key,
                    "value": json.loads(value) if value_type == "json" else value,
                    "metadata": json.loads(metadata) if metadata else None,
                    "created_at": created_at,
                })
            return results
    
    async def clear(self) -> None:
        """Clear all entries."""
        if self._db is None:
            await self.initialize()
        
        await self._db.execute("DELETE FROM memory")
        await self._db.commit()
    
    async def count(self) -> int:
        """Count total entries."""
        if self._db is None:
            await self.initialize()
        
        async with self._db.execute("SELECT COUNT(*) FROM memory") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


_long_term_memory: Optional[LongTermMemory] = None


async def get_long_term_memory() -> LongTermMemory:
    """Get the global long-term memory instance."""
    global _long_term_memory
    if _long_term_memory is None:
        from localclaw.config.settings import get_settings
        settings = get_settings()
        _long_term_memory = LongTermMemory(settings.memory_db)
        await _long_term_memory.initialize()
    return _long_term_memory

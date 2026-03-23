"""Gateway module for message handling and routing."""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from localclaw.core.models import Message


logger = logging.getLogger(__name__)


@dataclass
class Session:
    """A user session."""
    session_id: str
    user_id: str
    channel: str
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def touch(self) -> None:
        """Update last activity time."""
        self.last_activity = datetime.now()


class MessageQueue:
    """Async message queue for handling messages."""
    
    def __init__(self, max_size: int = 1000) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._handlers: List[Callable] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def put(self, message: Message) -> bool:
        """Add a message to the queue."""
        try:
            self._queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            logger.warning("Message queue is full")
            return False
    
    async def get(self) -> Message:
        """Get a message from the queue."""
        return await self._queue.get()
    
    def add_handler(self, handler: Callable) -> None:
        """Add a message handler."""
        self._handlers.append(handler)
    
    def remove_handler(self, handler: Callable) -> None:
        """Remove a message handler."""
        if handler in self._handlers:
            self._handlers.remove(handler)
    
    async def start(self) -> None:
        """Start processing messages."""
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("Message queue started")
    
    async def stop(self) -> None:
        """Stop processing messages."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Message queue stopped")
    
    async def _process_loop(self) -> None:
        """Process messages from the queue."""
        while self._running:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                
                for handler in self._handlers:
                    try:
                        await handler(message)
                    except Exception as e:
                        logger.error(f"Handler error: {e}")
                
                self._queue.task_done()
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Queue processing error: {e}")
    
    @property
    def size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()


class SessionManager:
    """Manages user sessions."""
    
    def __init__(self, session_timeout: int = 3600) -> None:
        self._sessions: Dict[str, Session] = {}
        self._user_sessions: Dict[str, List[str]] = defaultdict(list)
        self._session_timeout = session_timeout
    
    def create_session(self, user_id: str, channel: str, metadata: Optional[Dict[str, Any]] = None) -> Session:
        """Create a new session."""
        import uuid
        session_id = str(uuid.uuid4())[:8]
        
        session = Session(
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            metadata=metadata or {},
        )
        
        self._sessions[session_id] = session
        self._user_sessions[user_id].append(session_id)
        
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        return self._sessions.get(session_id)
    
    def get_user_sessions(self, user_id: str) -> List[Session]:
        """Get all sessions for a user."""
        session_ids = self._user_sessions.get(user_id, [])
        return [self._sessions[sid] for sid in session_ids if sid in self._sessions]
    
    def end_session(self, session_id: str) -> bool:
        """End a session."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        
        if session_id in self._user_sessions.get(session.user_id, []):
            self._user_sessions[session.user_id].remove(session_id)
        
        del self._sessions[session_id]
        return True
    
    def cleanup_expired(self) -> int:
        """Remove expired sessions."""
        now = datetime.now()
        expired = [
            sid for sid, s in self._sessions.items()
            if (now - s.last_activity).total_seconds() > self._session_timeout
        ]
        
        for sid in expired:
            self.end_session(sid)
        
        return len(expired)
    
    def touch_session(self, session_id: str) -> None:
        """Update session activity time."""
        session = self._sessions.get(session_id)
        if session:
            session.touch()


class Gateway:
    """Main gateway for message handling and routing."""
    
    def __init__(self) -> None:
        self._message_queue = MessageQueue()
        self._session_manager = SessionManager()
        self._message_handlers: List[Callable] = []
        self._logger = logging.getLogger("localclaw.gateway")
    
    @property
    def queue(self) -> MessageQueue:
        """Get the message queue."""
        return self._message_queue
    
    @property
    def sessions(self) -> SessionManager:
        """Get the session manager."""
        return self._session_manager
    
    async def start(self) -> None:
        """Start the gateway."""
        await self._message_queue.start()
        self._logger.info("Gateway started")
    
    async def stop(self) -> None:
        """Stop the gateway."""
        await self._message_queue.stop()
        self._logger.info("Gateway stopped")
    
    def add_message_handler(self, handler: Callable) -> None:
        """Add a message handler."""
        self._message_handlers.append(handler)
        self._message_queue.add_handler(handler)
    
    async def submit_message(self, message: Message) -> bool:
        """Submit a message to the gateway."""
        return await self._message_queue.put(message)
    
    async def process_message_direct(self, message: Message) -> Any:
        """Process a message directly without queuing."""
        for handler in self._message_handlers:
            try:
                result = await handler(message)
                return result
            except Exception as e:
                self._logger.error(f"Handler error: {e}")
        return None


_gateway: Optional[Gateway] = None


def get_gateway() -> Gateway:
    """Get the global gateway instance."""
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway

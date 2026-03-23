"""Trigger manager for condition-based task execution."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)


class TriggerType(str, Enum):
    """Types of triggers."""
    EVENT = "event"
    CONDITION = "condition"
    WEBHOOK = "webhook"
    FILE_CHANGE = "file_change"
    MEMORY_CHANGE = "memory_change"


@dataclass
class TriggerConfig:
    """Configuration for a trigger."""
    trigger_id: str
    trigger_type: TriggerType
    condition: Optional[str] = None
    event_pattern: Optional[str] = None
    watch_path: Optional[str] = None
    handler_name: Optional[str] = None
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "trigger_id": self.trigger_id,
            "trigger_type": self.trigger_type.value,
            "condition": self.condition,
            "event_pattern": self.event_pattern,
            "watch_path": self.watch_path,
            "handler_name": self.handler_name,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }


class TriggerInstance:
    """An active trigger instance."""
    
    def __init__(
        self,
        config: TriggerConfig,
        handler: Callable,
    ) -> None:
        self.config = config
        self.handler = handler
        self.triggered_count = 0
        self.last_triggered: Optional[datetime] = None
        self._running = False
    
    async def check_and_fire(self, context: Dict[str, Any]) -> bool:
        """Check condition and fire if met."""
        if not self.config.enabled:
            return False
        
        should_fire = await self._evaluate(context)
        
        if should_fire:
            await self._fire(context)
            return True
        
        return False
    
    async def _evaluate(self, context: Dict[str, Any]) -> bool:
        """Evaluate the trigger condition."""
        if self.config.trigger_type == TriggerType.CONDITION:
            if not self.config.condition:
                return False
            
            try:
                result = bool(eval(self.config.condition, {"__builtins__": {}}, context))
                return result
            except Exception as e:
                logger.error(f"Trigger condition evaluation error: {e}")
                return False
        
        elif self.config.trigger_type == TriggerType.EVENT:
            event_type = context.get("event_type", "")
            if self.config.event_pattern:
                import re
                return bool(re.match(self.config.event_pattern, event_type))
            return True
        
        elif self.config.trigger_type == TriggerType.MEMORY_CHANGE:
            key = context.get("key", "")
            old_value = context.get("old_value")
            new_value = context.get("new_value")
            
            if self.config.condition:
                try:
                    return bool(eval(
                        self.config.condition,
                        {"__builtins__": {}},
                        {"key": key, "old_value": old_value, "new_value": new_value},
                    ))
                except Exception:
                    return False
            return True
        
        return False
    
    async def _fire(self, context: Dict[str, Any]) -> None:
        """Fire the trigger handler."""
        self.last_triggered = datetime.now()
        self.triggered_count += 1
        
        try:
            result = self.handler(context)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Trigger handler error: {e}")


class TriggerManager:
    """Manager for condition-based triggers."""
    
    def __init__(self) -> None:
        self._triggers: Dict[str, TriggerInstance] = {}
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._logger = logging.getLogger("localclaw.events.triggers")
    
    def register_trigger(
        self,
        config: TriggerConfig,
        handler: Callable,
    ) -> TriggerInstance:
        """Register a new trigger."""
        instance = TriggerInstance(config, handler)
        self._triggers[config.trigger_id] = instance
        self._logger.info(f"Registered trigger: {config.trigger_id}")
        return instance
    
    def unregister_trigger(self, trigger_id: str) -> bool:
        """Unregister a trigger."""
        if trigger_id in self._triggers:
            del self._triggers[trigger_id]
            self._logger.info(f"Unregistered trigger: {trigger_id}")
            return True
        return False
    
    def enable_trigger(self, trigger_id: str) -> bool:
        """Enable a trigger."""
        trigger = self._triggers.get(trigger_id)
        if trigger:
            trigger.config.enabled = True
            return True
        return False
    
    def disable_trigger(self, trigger_id: str) -> bool:
        """Disable a trigger."""
        trigger = self._triggers.get(trigger_id)
        if trigger:
            trigger.config.enabled = False
            return True
        return False
    
    async def emit_event(self, event_type: str, data: Dict[str, Any]) -> int:
        """Emit an event and trigger matching triggers."""
        context = {"event_type": event_type, **data}
        triggered_count = 0
        
        for trigger in self._triggers.values():
            if trigger.config.trigger_type == TriggerType.EVENT:
                if await trigger.check_and_fire(context):
                    triggered_count += 1
        
        handlers = self._event_handlers.get(event_type, [])
        for handler in handlers:
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                self._logger.error(f"Event handler error: {e}")
        
        return triggered_count
    
    async def check_condition_triggers(self, context: Dict[str, Any]) -> int:
        """Check all condition triggers."""
        triggered_count = 0
        
        for trigger in self._triggers.values():
            if trigger.config.trigger_type == TriggerType.CONDITION:
                if await trigger.check_and_fire(context):
                    triggered_count += 1
        
        return triggered_count
    
    async def notify_memory_change(
        self,
        key: str,
        old_value: Any,
        new_value: Any,
    ) -> int:
        """Notify of memory change for memory triggers."""
        context = {
            "key": key,
            "old_value": old_value,
            "new_value": new_value,
        }
        
        triggered_count = 0
        
        for trigger in self._triggers.values():
            if trigger.config.trigger_type == TriggerType.MEMORY_CHANGE:
                if await trigger.check_and_fire(context):
                    triggered_count += 1
        
        return triggered_count
    
    def add_event_handler(self, event_type: str, handler: Callable) -> None:
        """Add a handler for a specific event type."""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)
    
    def remove_event_handler(self, event_type: str, handler: Callable) -> bool:
        """Remove an event handler."""
        if event_type in self._event_handlers:
            if handler in self._event_handlers[event_type]:
                self._event_handlers[event_type].remove(handler)
                return True
        return False
    
    def get_trigger(self, trigger_id: str) -> Optional[TriggerInstance]:
        """Get a trigger by ID."""
        return self._triggers.get(trigger_id)
    
    def list_triggers(self) -> List[str]:
        """List all trigger IDs."""
        return list(self._triggers.keys())
    
    def get_all_triggers(self) -> List[TriggerInstance]:
        """Get all triggers."""
        return list(self._triggers.values())
    
    def get_trigger_info(self, trigger_id: str) -> Optional[Dict[str, Any]]:
        """Get trigger information."""
        trigger = self._triggers.get(trigger_id)
        if trigger:
            return {
                **trigger.config.to_dict(),
                "triggered_count": trigger.triggered_count,
                "last_triggered": trigger.last_triggered.isoformat() if trigger.last_triggered else None,
            }
        return None

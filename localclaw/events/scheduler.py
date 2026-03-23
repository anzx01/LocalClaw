"""Event scheduler for scheduled and triggered tasks."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger


logger = logging.getLogger(__name__)


class ScheduledTask:
    """A scheduled task."""
    
    def __init__(
        self,
        task_id: str,
        handler: Callable,
        trigger_type: str,
        trigger_config: Dict[str, Any],
        name: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        self.task_id = task_id
        self.handler = handler
        self.trigger_type = trigger_type
        self.trigger_config = trigger_config
        self.name = name or task_id
        self.enabled = enabled
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = None
        self.run_count = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "trigger_type": self.trigger_type,
            "trigger_config": self.trigger_config,
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "run_count": self.run_count,
        }


class EventScheduler:
    """Scheduler for scheduled and triggered tasks."""
    
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._tasks: Dict[str, ScheduledTask] = {}
        self._running = False
        self._logger = logging.getLogger("localclaw.events.scheduler")
    
    async def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            return
        
        self._scheduler.start()
        self._running = True
        self._logger.info("Event scheduler started")
    
    async def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return
        
        self._scheduler.shutdown(wait=False)
        self._running = False
        self._logger.info("Event scheduler stopped")
    
    def add_interval_task(
        self,
        task_id: str,
        handler: Callable,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        start_date: Optional[datetime] = None,
        name: Optional[str] = None,
    ) -> ScheduledTask:
        """Add a task that runs at intervals."""
        trigger = IntervalTrigger(
            seconds=seconds,
            minutes=minutes,
            hours=hours,
            start_date=start_date,
        )
        
        task = ScheduledTask(
            task_id=task_id,
            handler=handler,
            trigger_type="interval",
            trigger_config={
                "seconds": seconds,
                "minutes": minutes,
                "hours": hours,
            },
            name=name,
        )
        
        self._tasks[task_id] = task
        self._scheduler.add_job(
            self._wrap_handler(task),
            trigger=trigger,
            id=task_id,
            name=name,
        )
        
        self._logger.info(f"Added interval task: {task_id}")
        return task
    
    def add_cron_task(
        self,
        task_id: str,
        handler: Callable,
        cron_expression: str,
        name: Optional[str] = None,
    ) -> ScheduledTask:
        """Add a task that runs on a cron schedule."""
        trigger = CronTrigger.from_crontab(cron_expression)
        
        task = ScheduledTask(
            task_id=task_id,
            handler=handler,
            trigger_type="cron",
            trigger_config={"expression": cron_expression},
            name=name,
        )
        
        self._tasks[task_id] = task
        self._scheduler.add_job(
            self._wrap_handler(task),
            trigger=trigger,
            id=task_id,
            name=name,
        )
        
        self._logger.info(f"Added cron task: {task_id}")
        return task
    
    def add_date_task(
        self,
        task_id: str,
        handler: Callable,
        run_date: datetime,
        name: Optional[str] = None,
    ) -> ScheduledTask:
        """Add a task that runs once at a specific time."""
        trigger = DateTrigger(run_date)
        
        task = ScheduledTask(
            task_id=task_id,
            handler=handler,
            trigger_type="date",
            trigger_config={"run_date": run_date.isoformat()},
            name=name,
        )
        
        self._tasks[task_id] = task
        self._scheduler.add_job(
            self._wrap_handler(task),
            trigger=trigger,
            id=task_id,
            name=name,
        )
        
        self._logger.info(f"Added date task: {task_id}")
        return task
    
    def _wrap_handler(self, task: ScheduledTask) -> Callable:
        """Wrap a handler to track execution."""
        async def wrapped():
            if not task.enabled:
                return
            
            task.last_run = datetime.now()
            try:
                result = task.handler()
                if asyncio.iscoroutine(result):
                    await result
                task.run_count += 1
            except Exception as e:
                self._logger.error(f"Task {task.task_id} failed: {e}")
        
        return wrapped
    
    def remove_task(self, task_id: str) -> bool:
        """Remove a scheduled task."""
        if task_id in self._tasks:
            self._scheduler.remove_job(task_id)
            del self._tasks[task_id]
            self._logger.info(f"Removed task: {task_id}")
            return True
        return False
    
    def pause_task(self, task_id: str) -> bool:
        """Pause a scheduled task."""
        if task_id in self._tasks:
            self._scheduler.pause_job(task_id)
            self._tasks[task_id].enabled = False
            return True
        return False
    
    def resume_task(self, task_id: str) -> bool:
        """Resume a paused task."""
        if task_id in self._tasks:
            self._scheduler.resume_job(task_id)
            self._tasks[task_id].enabled = True
            return True
        return False
    
    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Get a scheduled task."""
        return self._tasks.get(task_id)
    
    def list_tasks(self) -> List[str]:
        """List all scheduled task IDs."""
        return list(self._tasks.keys())
    
    def get_all_tasks(self) -> List[ScheduledTask]:
        """Get all scheduled tasks."""
        return list(self._tasks.values())
    
    def get_task_info(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task information."""
        task = self._tasks.get(task_id)
        if task:
            return task.to_dict()
        return None


_scheduler: Optional[EventScheduler] = None


def get_scheduler() -> EventScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = EventScheduler()
    return _scheduler

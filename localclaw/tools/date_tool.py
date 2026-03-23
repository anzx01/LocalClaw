"""Date and time tools."""

from datetime import datetime

from localclaw.core.models import ExecutionResult
from localclaw.tools.base import Tool


class DateWeekdayTool(Tool):
    """Tool for getting today's weekday."""
    
    name = "date_weekday"
    description = "Get today's weekday in Chinese"
    inputs = {}
    outputs = {"weekday": "string"}
    
    async def execute(self, **kwargs) -> dict:
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        today = datetime.now()
        weekday = weekdays[today.weekday()]
        
        return {
            "status": "success",
            "message": f"今天是{weekday}",
            "data": {"weekday": weekday},
        }


class DateTodayTool(Tool):
    """Tool for getting today's date."""
    
    name = "date_today"
    description = "Get today's date"
    inputs = {}
    outputs = {"date": "string"}
    
    async def execute(self, **kwargs) -> dict:
        today = datetime.now()
        date_str = today.strftime("%Y年%m月%d日")
        
        return {
            "status": "success",
            "message": f"今天是{date_str}",
            "data": {"date": date_str},
        }


class TimeNowTool(Tool):
    """Tool for getting current time."""
    
    name = "time_now"
    description = "Get current time"
    inputs = {}
    outputs = {"time": "string"}
    
    async def execute(self, **kwargs) -> dict:
        now = datetime.now()
        time_str = now.strftime("%H:%M:%S")
        
        return {
            "status": "success",
            "message": f"现在是{time_str}",
            "data": {"time": time_str},
        }


def register_date_tools() -> None:
    """Register date tools."""
    from localclaw.tools.base import get_tool_registry
    
    registry = get_tool_registry()
    registry.register(DateWeekdayTool())
    registry.register(DateTodayTool())
    registry.register(TimeNowTool())

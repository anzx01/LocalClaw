"""HTTP request tool."""

import logging
from typing import Any, ClassVar, Dict, Optional

import httpx

from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel
from localclaw.tools.base import Tool, register_tool


logger = logging.getLogger(__name__)


class HttpTool(Tool):
    """Tool for making HTTP requests."""
    
    name = "http"
    description = "Make HTTP requests"
    risk_level = RiskLevel.MEDIUM
    inputs = {"url": "string", "method": "string"}
    outputs = {"status_code": "integer", "body": "string"}
    
    def __init__(self, timeout: float = 30.0) -> None:
        super().__init__()
        self._timeout = timeout
    
    async def execute(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> ExecutionResult:
        """Execute HTTP request."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    content=body,
                    json=json_data,
                )
            
            try:
                response_body = response.json()
            except Exception:
                response_body = response.text
            
            return ExecutionResult.success(
                message=f"HTTP {method} {url}: {response.status_code}",
                data={
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": response_body,
                },
            )
            
        except httpx.TimeoutException:
            return ExecutionResult.from_error(
                f"Request timed out: {url}",
                ErrorType.TIMEOUT_ERROR,
            )
        except httpx.RequestError as e:
            return ExecutionResult.from_error(
                f"Request failed: {e}",
                ErrorType.TOOL_ERROR,
            )
        except Exception as e:
            return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)


class HttpGetTool(Tool):
    """Tool for HTTP GET requests."""
    
    name = "http_get"
    description = "Make HTTP GET request"
    risk_level = RiskLevel.LOW
    inputs = {"url": "string"}
    outputs = {"status_code": "integer", "body": "string"}
    
    def __init__(self, timeout: float = 30.0) -> None:
        super().__init__()
        self._timeout = timeout
    
    async def execute(self, url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> ExecutionResult:
        """Execute HTTP GET request."""
        http_tool = HttpTool(self._timeout)
        return await http_tool.execute(url=url, method="GET", headers=headers)


class HttpPostTool(Tool):
    """Tool for HTTP POST requests."""
    
    name = "http_post"
    description = "Make HTTP POST request"
    risk_level = RiskLevel.MEDIUM
    inputs = {"url": "string", "body": "string"}
    outputs = {"status_code": "integer", "body": "string"}
    
    def __init__(self, timeout: float = 30.0) -> None:
        super().__init__()
        self._timeout = timeout
    
    async def execute(
        self,
        url: str,
        body: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> ExecutionResult:
        """Execute HTTP POST request."""
        http_tool = HttpTool(self._timeout)
        return await http_tool.execute(
            url=url,
            method="POST",
            headers=headers,
            body=body,
            json_data=json_data,
        )


def register_http_tools(timeout: float = 30.0) -> None:
    """Register HTTP tools."""
    register_tool(HttpTool(timeout))
    register_tool(HttpGetTool(timeout))
    register_tool(HttpPostTool(timeout))

"""Web channel for LocalClaw using FastAPI."""

import json
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from localclaw.config.settings import get_settings
from localclaw.core.engine import ExecutionEngine, get_engine
from localclaw.core.models import ExecutionResult, Message, Step, Task, TaskState
from localclaw.llm.provider import initialize_llm_provider
from localclaw.security.audit import configure_audit_logger
from localclaw.skills.loader import load_skills_from_settings
from localclaw.system.windows_service import (
    get_background_service_status,
    install_background_service,
    start_background_service,
    stop_background_service,
    uninstall_background_service,
)
from localclaw.tools.base import Tool, get_tool_registry
from localclaw.tools.file_tool import register_file_tools
from localclaw.tools.http_tool import register_http_tools
from localclaw.tools.local_model_tool import register_local_model_tools
from localclaw.tools.shell_tool import register_shell_tools
from localclaw.tools.browser_cdp_tool import register_browser_cdp_tools
from localclaw.tools.clawhub_tool import register_clawhub_tools
from localclaw.channels.wechat_personal import (
    PersonalWeChatEnvelope,
    build_personal_wechat_message,
    format_task_for_personal_wechat,
    is_valid_personal_wechat_token,
    normalize_personal_wechat_payload,
    send_personal_wechat_reply,
)
from localclaw.channels.whatsapp import (
    WhatsAppEnvelope,
    build_whatsapp_message,
    format_task_for_whatsapp,
    is_valid_whatsapp_signature,
    is_valid_whatsapp_verify_token,
    normalize_whatsapp_webhook_payload,
    send_whatsapp_text_reply,
)
from localclaw.channels.weixin import (
    StoredWeixinAccount,
    WeixinEnvelope,
    WeixinQrLoginSession,
    build_weixin_message,
    fetch_weixin_updates,
    format_task_for_weixin,
    get_cached_weixin_context_token,
    is_allowed_weixin_sender,
    is_valid_weixin_webhook_token,
    list_stored_weixin_account_ids,
    load_stored_weixin_account,
    load_weixin_updates_cursor,
    normalize_weixin_polled_message,
    normalize_weixin_webhook_payload,
    persist_weixin_context_token,
    poll_weixin_qr_login,
    provided_weixin_webhook_token,
    resolve_weixin_context_token,
    resolve_weixin_reply_configuration,
    save_weixin_updates_cursor,
    send_weixin_text_reply,
    start_weixin_qr_login,
    WEIXIN_GET_UPDATES_DEFAULT_TIMEOUT_MS,
    WEIXIN_SESSION_EXPIRED_ERRCODE,
)
from localclaw.channels.result_formatter import format_task_for_chat


logger = logging.getLogger(__name__)


class MessageRequest(BaseModel):
    """Request model for sending a message."""
    content: str
    user_id: str = "default"
    channel: str = "web"


class MessageResponse(BaseModel):
    """Response model for message processing."""
    task_id: str
    status: str
    message: str
    data: Dict[str, Any] = {}
    error: Optional[str] = None


class TaskResponse(BaseModel):
    """Response model for task status."""
    id: str
    state: str
    created_at: datetime
    completed_at: Optional[datetime]
    result: Dict[str, Any] = {}
    error: Optional[str] = None
    channel: Optional[str] = None
    message: Optional[str] = None
    current_step: Optional[Dict[str, Any]] = None


class ApprovalItemResponse(BaseModel):
    """Response model for a pending approval item."""

    task_id: str
    task_state: str
    created_at: datetime
    channel: str
    user_id: str
    message: Optional[str] = None
    step: Dict[str, Any]


class ApprovalsResponse(BaseModel):
    """Collection of pending approvals."""

    approvals: List[ApprovalItemResponse]


class SkillResponse(BaseModel):
    """Response model for skill info."""
    name: str
    version: str
    description: str
    type: str
    state: str
    skill_key: Optional[str] = None
    aliases: List[str] = []
    availability: str = "available"
    availability_reason: Optional[str] = None
    source_path: Optional[str] = None
    source_format: Optional[str] = None
    source_scope: str = "runtime"
    removable: bool = False


class SkillSecurityReviewResponse(BaseModel):
    """Response model for pre-install skill safety checks."""

    skill_id: str
    skill_name: str
    version: str
    risk_level: str
    risk_label: str
    risk_score: int
    status: str
    recommended_action: str
    summary: str
    findings: List[Dict[str, Any]]
    recommendations: List[str]
    install_options: List[Dict[str, Any]]
    metadata_snapshot: Dict[str, Any]
    checks: Dict[str, Any]
    scan_version: int


class PersonalWeChatWebhookResponse(BaseModel):
    """Response model for the experimental personal WeChat bridge."""

    accepted: bool
    task_id: str
    status: str
    reply: Dict[str, Any]
    bridge_delivery: Optional[Dict[str, Any]] = None


class WhatsAppWebhookResponse(BaseModel):
    """Response model for the WhatsApp Cloud API bridge."""

    accepted: bool
    event_type: str
    task_id: Optional[str] = None
    status: Optional[str] = None
    reply: Optional[Dict[str, Any]] = None
    outbound_delivery: Optional[Dict[str, Any]] = None


class WeixinWebhookResponse(BaseModel):
    """Response model for the Weixin webhook bridge."""

    accepted: bool
    event_type: str
    task_id: Optional[str] = None
    status: Optional[str] = None
    reply: Optional[Dict[str, Any]] = None
    outbound_delivery: Optional[Dict[str, Any]] = None


class ChannelsOverviewResponse(BaseModel):
    """Aggregated channel metadata used by the Web UI."""

    channels: List[Dict[str, Any]]


class PersonalWeChatTestRequest(BaseModel):
    """Manual connectivity test request for the personal WeChat bridge."""

    reply_target: Optional[str] = None
    conversation_id: Optional[str] = None
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    message_id: Optional[str] = None
    text: str = "LocalClaw connectivity test"


class WhatsAppTestRequest(BaseModel):
    """Manual connectivity test request for the WhatsApp channel."""

    recipient: Optional[str] = None
    phone_number_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    sender_name: Optional[str] = None
    text: str = "LocalClaw connectivity test"


class WeixinTestRequest(BaseModel):
    """Manual connectivity test request for the Weixin channel."""

    recipient: Optional[str] = None
    account_id: Optional[str] = None
    context_token: Optional[str] = None
    text: str = "LocalClaw connectivity test"


class WeixinLoginStartRequest(BaseModel):
    """Start a Weixin QR login session."""

    account_id: Optional[str] = None


class WeixinConnectedAccountResponse(BaseModel):
    """Public Weixin account details shown in the Web UI."""

    account_id: str
    remote_account_id: str = ""
    user_id: str = ""
    base_url: str
    saved_at: Optional[str] = None
    context_token_count: int = 0


class WeixinLoginSessionResponse(BaseModel):
    """Serialized Weixin QR login session."""

    session_id: str
    account_id: str
    status: str
    qrcode: str = ""
    qrcode_img_content: str = ""
    poll_path: str
    created_at: str
    updated_at: str
    message: str
    error: Optional[str] = None
    connected_account: Optional[WeixinConnectedAccountResponse] = None


class ChannelTestResponse(BaseModel):
    """Structured result for manual channel tests."""

    ok: bool
    mode: str
    channel: str
    summary: str
    request: Dict[str, Any] = {}
    missing: List[str] = []
    delivery: Optional[Dict[str, Any]] = None


class SkillInstallResponse(BaseModel):
    """Structured result for skill installation with required review."""

    installed: bool
    skill_path: str = ""
    requires_review: bool = False
    error: Optional[str] = None
    scan: Optional[SkillSecurityReviewResponse] = None
    guard: Optional[Dict[str, Any]] = None


class BackgroundServiceStatusResponse(BaseModel):
    """Status payload for LocalClaw background service management."""

    supported: bool
    platform: str
    service_name: str
    display_name: str
    installed: bool
    state: str
    running: bool
    startup_type: str
    binary_path: str
    can_manage: bool
    python_executable: str
    script_path: str
    command: str
    message: str = ""


class BackgroundServiceActionResponse(BaseModel):
    """Action response for service install/start/stop/uninstall operations."""

    ok: bool
    action: str
    changed: bool
    message: str
    status: BackgroundServiceStatusResponse


class ConnectionManager:
    """Manages WebSocket connections."""
    
    def __init__(self) -> None:
        self._connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new connection."""
        await websocket.accept()
        self._connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a connection."""
        if websocket in self._connections:
            self._connections.remove(websocket)
    
    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcast a message to all connections."""
        for connection in self._connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


_manager = ConnectionManager()
_weixin_poll_tasks: Dict[str, asyncio.Task] = {}


def _coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort int coercion used for external API error codes."""

    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("+", "-")):
            sign, digits = text[0], text[1:]
            if digits.isdigit():
                return int(f"{sign}{digits}")
        if text.isdigit():
            return int(text)
    return default


def _weixin_polling_enabled(settings) -> bool:
    """Return whether native Weixin polling should be active."""

    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return bool(
        settings.weixin_enabled
        and getattr(settings, "weixin_polling_enabled", True)
    )


def _normalize_path(path: str, fallback: str) -> str:
    """Normalize API path-style strings and ensure a leading slash."""

    cleaned = (path or "").strip()
    if not cleaned:
        cleaned = fallback
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def _serialize_weixin_connected_account(
    account: Optional[StoredWeixinAccount],
    account_id: str = "default",
) -> Optional[WeixinConnectedAccountResponse]:
    """Build a public Weixin account payload for the Web UI."""

    if account is None:
        return None

    return WeixinConnectedAccountResponse(
        account_id=account_id or "default",
        remote_account_id=account.remote_account_id,
        user_id=account.user_id,
        base_url=account.base_url,
        saved_at=account.saved_at or None,
        context_token_count=len(account.context_tokens or {}),
    )


def _weixin_login_message(session: WeixinQrLoginSession) -> str:
    """Convert a Weixin QR login status into a UI-friendly message."""

    status = (session.status or "").strip().lower()
    if status in {"wait", "waiting", "pending"}:
        return "Scan the QR code with Weixin to start login."
    if status in {"scaned", "scanned", "scan"}:
        return "QR code scanned. Confirm the login in Weixin."
    if status == "confirmed":
        return "Weixin login confirmed. LocalClaw stored the bot token."
    if status == "expired":
        return session.error or "QR code expired. Start a new scan to continue."
    if status == "error":
        return session.error or "Weixin QR login failed."
    return "Waiting for Weixin login status."


def _serialize_weixin_login_session(
    session: WeixinQrLoginSession,
) -> WeixinLoginSessionResponse:
    """Serialize a Weixin QR login session for the API."""

    return WeixinLoginSessionResponse(
        session_id=session.session_id,
        account_id=session.account_id,
        status=session.status,
        qrcode=session.qrcode,
        qrcode_img_content=session.qrcode_img_content,
        poll_path=f"/api/channels/weixin/login/{session.session_id}",
        created_at=session.created_at,
        updated_at=session.updated_at,
        message=_weixin_login_message(session),
        error=session.error or None,
        connected_account=_serialize_weixin_connected_account(
            session.connected_account,
            account_id=session.account_id,
        ),
    )


def _weixin_channel_status_payload(settings) -> Dict[str, Any]:
    """Build the enriched Weixin channel status payload."""

    reply_config = resolve_weixin_reply_configuration(settings=settings)
    connected_account = _serialize_weixin_connected_account(
        reply_config["stored_account"],
        account_id="default",
    )

    if settings.weixin_reply_via_api:
        reply_via_api_source = "env"
    elif reply_config["has_stored_login"]:
        reply_via_api_source = "stored_login"
    else:
        reply_via_api_source = "disabled"

    return {
        "enabled": settings.weixin_enabled,
        "reply_via_api": reply_config["reply_enabled"],
        "reply_via_api_source": reply_via_api_source,
        "webhook_path": _normalize_path(settings.weixin_webhook_path, "/weixin/messages"),
        "has_webhook_token": bool(settings.weixin_webhook_token),
        "has_bot_token": bool(reply_config["bot_token"]),
        "bot_token_source": reply_config["bot_token_source"],
        "has_allowed_user_ids": bool((settings.weixin_allowed_user_ids or "").strip()),
        "base_url": reply_config["base_url"],
        "has_stored_login": reply_config["has_stored_login"],
        "connected_account": connected_account.model_dump() if connected_account else None,
        "login_path": "/api/channels/weixin/login/start",
    }


def _weixin_delivery_error(delivery: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a human-readable Weixin outbound error, if any."""

    if not isinstance(delivery, dict):
        return "No Weixin delivery payload was returned."

    try:
        status_code = int(delivery.get("status_code", 0) or 0)
    except (TypeError, ValueError):
        status_code = 0
    if status_code < 200 or status_code >= 300:
        return f"Weixin API returned HTTP {status_code or 'unknown'}."

    body = delivery.get("body")
    if not isinstance(body, dict):
        return None

    errcode = body.get("errcode")
    ret = body.get("ret")
    code = None
    if isinstance(errcode, int) and errcode != 0:
        code = errcode
    elif isinstance(ret, int) and ret != 0:
        code = ret

    if code is None:
        return None

    errmsg = str(body.get("errmsg") or body.get("error") or "").strip()
    if code == -14:
        return (
            "Weixin session timed out (errcode -14). Run Scan to Login again to refresh "
            "the stored bot token."
        )
    return f"Weixin API error {code}: {errmsg or 'unknown error'}"


async def _process_weixin_inbound_envelope(
    envelope: WeixinEnvelope,
    settings,
) -> Dict[str, Any]:
    """Process a normalized Weixin envelope and return webhook-style response data."""

    if not is_allowed_weixin_sender(settings.weixin_allowed_user_ids, envelope.sender_id):
        return {
            "accepted": True,
            "event_type": "filtered",
            "status": "ignored",
            "reply": {
                "type": "text",
                "text": "sender filtered by allowed_user_ids",
                "to_user_id": envelope.sender_id,
                "account_id": envelope.account_id,
            },
            "outbound_delivery": None,
        }

    if envelope.context_token:
        persist_weixin_context_token(
            envelope.account_id,
            envelope.sender_id,
            envelope.context_token,
            settings=settings,
        )

    engine = get_engine()
    task = await engine.process_message(build_weixin_message(envelope))
    reply_text = format_task_for_weixin(task)
    outbound_delivery = await send_weixin_text_reply(
        envelope=envelope,
        reply_text=reply_text,
        settings=settings,
    )
    resolved_context_token = resolve_weixin_context_token(envelope)

    return {
        "accepted": True,
        "event_type": "message",
        "task_id": task.id,
        "status": task.state.value,
        "reply": {
            "type": "text",
            "text": reply_text,
            "to_user_id": envelope.sender_id,
            "account_id": envelope.account_id,
            "context_token": resolved_context_token or "",
        },
        "outbound_delivery": outbound_delivery,
    }


async def _run_weixin_native_poll_loop(account_id: str) -> None:
    """Continuously poll Weixin getupdates for one stored account."""

    resolved_account_id = (account_id or "default").strip() or "default"
    timeout_ms = WEIXIN_GET_UPDATES_DEFAULT_TIMEOUT_MS
    cursor = load_weixin_updates_cursor(account_id=resolved_account_id)
    consecutive_failures = 0

    logger.info("Weixin native polling started for account '%s'", resolved_account_id)

    while True:
        settings = get_settings()
        if not _weixin_polling_enabled(settings):
            await asyncio.sleep(2.0)
            continue

        stored_account = load_stored_weixin_account(
            settings=settings,
            account_id=resolved_account_id,
        )
        if stored_account is None or not stored_account.token.strip():
            await asyncio.sleep(2.0)
            continue

        try:
            response = await fetch_weixin_updates(
                stored_account,
                get_updates_buf=cursor,
                settings=settings,
                timeout_ms=timeout_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            consecutive_failures += 1
            wait_seconds = 2.0 if consecutive_failures < 3 else 15.0
            if consecutive_failures >= 3:
                consecutive_failures = 0
            logger.warning(
                "Weixin getupdates request failed for account '%s': %s",
                resolved_account_id,
                exc,
            )
            await asyncio.sleep(wait_seconds)
            continue

        ret = _coerce_int(response.get("ret"), 0)
        errcode = _coerce_int(response.get("errcode"), 0)
        error_code = errcode if errcode != 0 else ret
        if error_code != 0:
            errmsg = str(response.get("errmsg") or response.get("error") or "").strip()
            if error_code == WEIXIN_SESSION_EXPIRED_ERRCODE:
                logger.warning(
                    "Weixin session expired for account '%s' (errcode -14). "
                    "Run Scan to Login again.",
                    resolved_account_id,
                )
                await asyncio.sleep(8.0)
                continue

            consecutive_failures += 1
            wait_seconds = 2.0 if consecutive_failures < 3 else 15.0
            if consecutive_failures >= 3:
                consecutive_failures = 0
            logger.warning(
                "Weixin getupdates API error for account '%s': code=%s, errmsg=%s",
                resolved_account_id,
                error_code,
                errmsg or "unknown",
            )
            await asyncio.sleep(wait_seconds)
            continue

        consecutive_failures = 0

        next_cursor = str(response.get("get_updates_buf") or "").strip()
        if next_cursor and next_cursor != cursor:
            cursor = next_cursor
            save_weixin_updates_cursor(
                account_id=resolved_account_id,
                get_updates_buf=cursor,
                settings=settings,
            )

        requested_timeout_ms = _coerce_int(response.get("longpolling_timeout_ms"), 0)
        if requested_timeout_ms > 0:
            timeout_ms = requested_timeout_ms

        messages = response.get("msgs")
        if not isinstance(messages, list):
            continue

        for raw_message in messages:
            envelope = normalize_weixin_polled_message(
                raw_message if isinstance(raw_message, dict) else {},
                account_id=resolved_account_id,
            )
            if envelope is None:
                continue

            try:
                result = await _process_weixin_inbound_envelope(envelope, settings)
                delivery_error = _weixin_delivery_error(result.get("outbound_delivery"))
                if delivery_error:
                    logger.warning(
                        "Weixin native polling reply error for account '%s' user '%s': %s",
                        resolved_account_id,
                        envelope.sender_id,
                        delivery_error,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Failed processing polled Weixin message for account '%s': %s",
                    resolved_account_id,
                    exc,
                )


async def _ensure_weixin_poll_tasks_running() -> None:
    """Start Weixin polling workers for accounts with stored logins."""

    settings = get_settings()
    if not _weixin_polling_enabled(settings):
        return

    for account_id, task in list(_weixin_poll_tasks.items()):
        if task.done():
            _weixin_poll_tasks.pop(account_id, None)

    for account_id in list_stored_weixin_account_ids(settings=settings):
        stored_account = load_stored_weixin_account(settings=settings, account_id=account_id)
        if stored_account is None or not stored_account.token.strip():
            continue
        existing = _weixin_poll_tasks.get(account_id)
        if existing is not None and not existing.done():
            continue
        _weixin_poll_tasks[account_id] = asyncio.create_task(
            _run_weixin_native_poll_loop(account_id),
            name=f"weixin-poll:{account_id}",
        )


async def _stop_weixin_poll_tasks() -> None:
    """Cancel all active Weixin polling workers."""

    tasks = list(_weixin_poll_tasks.values())
    _weixin_poll_tasks.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _serialize_step(step: Optional[Step]) -> Optional[Dict[str, Any]]:
    """Serialize step metadata for task and approval APIs."""

    if step is None:
        return None

    command_preview = ""
    if isinstance(step.input, dict):
        command_preview = str(
            step.input.get("command")
            or step.input.get("url")
            or step.input.get("path")
            or step.input.get("query")
            or ""
        ).strip()

    risk_level = "low"
    if step.tool_name == "shell":
        risk_level = "critical"
    elif step.tool_name in {"http_post", "browser_cdp"}:
        risk_level = "high"

    return {
        "id": step.id,
        "name": step.name,
        "tool_name": step.tool_name,
        "status": step.status.value,
        "input": step.input or {},
        "error": step.error,
        "started_at": step.started_at,
        "completed_at": step.completed_at,
        "risk_level": risk_level,
        "command_preview": command_preview,
    }


def _task_result_data(task: Task) -> Dict[str, Any]:
    """Extract a JSON-serializable result payload from a task."""

    result = task.result
    if isinstance(result, ExecutionResult):
        return result.data or {}
    if isinstance(result, dict):
        return result
    return {}


def _serialize_task(task: Task) -> TaskResponse:
    """Build the task response used by the API."""

    return TaskResponse(
        id=task.id,
        state=task.state.value,
        created_at=task.created_at,
        completed_at=task.completed_at,
        result=_task_result_data(task),
        error=task.error,
        channel=task.channel,
        message=task.message.content if task.message else None,
        current_step=_serialize_step(task.get_current_step()),
    )


def _serialize_approval(task: Task) -> Optional[ApprovalItemResponse]:
    """Build a pending approval item from an active task."""

    step = task.get_current_step()
    serialized_step = _serialize_step(step)
    if task.state != TaskState.VERIFYING or serialized_step is None:
        return None

    return ApprovalItemResponse(
        task_id=task.id,
        task_state=task.state.value,
        created_at=task.created_at,
        channel=task.channel,
        user_id=task.user_id,
        message=task.message.content if task.message else None,
        step=serialized_step,
    )


class SystemStatusTool(Tool):
    """Tool for getting system status."""
    
    name = "system_status"
    description = "Get system status information"
    inputs = {}
    outputs = {"status": "string", "version": "string"}
    
    async def execute(self, **kwargs) -> ExecutionResult:
        from localclaw import __version__
        return ExecutionResult.success(
            message="System status",
            data={
                "status": "running",
                "version": __version__,
                "mode": get_settings().mode.value,
            },
        )


class ListSkillsTool(Tool):
    """Tool for listing skills."""
    
    name = "list_skills"
    description = "List all available skills"
    inputs = {}
    outputs = {"skills": "list"}
    
    async def execute(self, **kwargs) -> ExecutionResult:
        from localclaw.skills.registry import get_skill_registry
        skills = get_skill_registry().list_skills()
        
        # Generate a more natural response
        response = "我是一个智能助手，可以帮助你做以下事情：\n\n"
        response += "1. 提供信息：\n"
        response += "   - 查询日期和时间（今天几号？明天星期几？）\n"
        response += "   - 查询天气信息（今天天气如何？）\n"
        response += "2. 系统功能：\n"
        response += "   - 查看系统状态\n"
        response += "   - 列出所有可用技能\n"
        response += "3. 其他功能：\n"
        response += "   - 执行各种工具操作\n"
        response += "   - 处理用户的各种查询\n"
        
        return ExecutionResult.success(
            message="系统功能介绍",
            data={"skills": skills, "result": response},
        )


def initialize_system() -> ExecutionEngine:
    """Initialize the LocalClaw system."""
    settings = get_settings()
    settings.ensure_directories()
    
    configure_audit_logger(settings.audit_log)
    
    # Initialize LLM provider if enabled
    if settings.llm_enabled:
        try:
            provider = initialize_llm_provider(settings)
            logger.info(
                "LLM provider initialized: %s (%s)",
                provider.get_config().provider_type.value,
                provider.get_config().model,
            )
        except Exception as e:
            logger.error(f"Failed to initialize LLM provider: {e}")
    else:
        logger.info("LLM is disabled")
    
    tool_registry = get_tool_registry()
    tool_registry.register(SystemStatusTool())
    tool_registry.register(ListSkillsTool())
    register_file_tools()
    register_http_tools()
    register_local_model_tools()
    register_shell_tools()
    register_browser_cdp_tools()
    register_clawhub_tools()
    
    load_skills_from_settings(settings)
    
    from localclaw.core.verifier import create_default_verifier
    from localclaw.skills.registry import get_skill_registry
    verifier = create_default_verifier(settings=settings, skill_registry=get_skill_registry())
    verifier.set_auto_approve_low(True)
    verifier.set_require_confirmation_high(True)

    engine = ExecutionEngine(
        settings=settings,
        verifier=verifier,
    )
    
    def on_step_start(step, task):
        asyncio.create_task(_manager.broadcast({
            "type": "step_start",
            "task_id": task.id,
            "step_id": step.id,
            "step_name": step.name,
        }))
    
    def on_step_complete(step, task):
        asyncio.create_task(_manager.broadcast({
            "type": "step_complete",
            "task_id": task.id,
            "step_id": step.id,
            "status": step.status.value,
        }))
    
    def on_task_complete(task):
        asyncio.create_task(_manager.broadcast({
            "type": "task_complete",
            "task_id": task.id,
            "state": task.state.value,
        }))

    def on_approval_required(step, task):
        asyncio.create_task(_manager.broadcast({
            "type": "approval_required",
            "task_id": task.id,
            "step_id": step.id,
            "step_name": step.name,
            "tool_name": step.tool_name,
        }))
    
    engine.set_callbacks(
        on_step_start=on_step_start,
        on_step_complete=on_step_complete,
        on_task_complete=on_task_complete,
        on_approval_required=on_approval_required,
    )
    
    # Set the global engine instance
    from localclaw.core.engine import _engine
    import localclaw.core.engine as engine_module
    engine_module._engine = engine
    
    return engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Check if system is already initialized
    from localclaw.core.engine import _engine
    if _engine is None:
        initialize_system()
    await _ensure_weixin_poll_tasks_running()
    logger.info("LocalClaw web server started")
    yield
    await _stop_weixin_poll_tasks()
    logger.info("LocalClaw web server stopped")


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="LocalClaw",
        description="Local-first Agent Runtime System",
        version="0.1.0",
        lifespan=lifespan,
    )
    
    # Mount static files directory
    import os
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    
    @app.get("/", response_class=HTMLResponse)
    async def root():
        """Serve the web UI."""
        import os
        static_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "static", "index.html")
        if os.path.exists(static_file):
            with open(static_file, 'r', encoding='utf-8') as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content=get_web_ui_html())
    
    @app.post("/api/message", response_model=MessageResponse)
    async def send_message(request: MessageRequest):
        """Process a message and return the result."""
        engine = get_engine()
        
        message = Message(
            content=request.content,
            user_id=request.user_id,
            channel=request.channel,
        )
        
        task = await engine.process_message(message)
        
        return MessageResponse(
            task_id=task.id,
            status=task.state.value,
            message=format_task_for_chat(task),
            data=task.result.data if task.result else {},
            error=task.error,
        )
    
    @app.get("/api/tasks", response_model=List[TaskResponse])
    async def list_tasks(limit: int = 10):
        """List recent tasks."""
        engine = get_engine()
        task_map = {
            task.id: task
            for task in [*engine.get_active_tasks(), *engine.get_task_history(limit)]
        }
        tasks = sorted(task_map.values(), key=lambda task: task.created_at, reverse=True)[:limit]

        return [_serialize_task(task) for task in tasks]
    
    @app.get("/api/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str):
        """Get a specific task."""
        engine = get_engine()
        task = engine.get_task(task_id)
        
        if not task:
            tasks = engine.get_task_history(100)
            task = next((t for t in tasks if t.id == task_id), None)
        
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return _serialize_task(task)

    @app.get("/api/approvals", response_model=ApprovalsResponse)
    async def list_approvals():
        """List tasks that are currently waiting for human approval."""

        engine = get_engine()
        approvals = [
            approval
            for approval in (
                _serialize_approval(task) for task in engine.get_active_tasks()
            )
            if approval is not None
        ]
        approvals.sort(key=lambda approval: approval.created_at, reverse=True)
        return ApprovalsResponse(approvals=approvals)

    @app.post("/api/tasks/{task_id}/approve/{step_id}", response_model=TaskResponse)
    async def approve_task_step(task_id: str, step_id: str):
        """Approve a waiting step and resume task execution."""
        engine = get_engine()
        task = await engine.approve_and_resume_step(task_id, step_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task or step not found")

        return _serialize_task(task)
    
    @app.get("/api/skills", response_model=List[SkillResponse])
    async def list_skills():
        """List all registered skills."""
        from localclaw.skills.registry import get_skill_registry

        registry = get_skill_registry()
        skills = registry.get_all_info()
        settings = get_settings()
        managed_dir = settings.managed_skills_dir.resolve()
        configured_dirs = [path.resolve() for path in settings.extra_skill_dirs]

        def _classify_skill_source(path_value: Optional[str]) -> tuple[str, bool]:
            normalized_path = str(path_value or "").strip()
            if not normalized_path:
                return "runtime", False

            try:
                resolved = Path(normalized_path).resolve()
            except Exception:
                return "unknown", False

            parents = {resolved, *resolved.parents}
            if managed_dir in parents:
                return "managed", True
            if any(configured_dir in parents for configured_dir in configured_dirs):
                return "configured", False
            return "unknown", False

        return [
            SkillResponse(
                name=s["name"],
                version=s["version"],
                description=s["description"],
                type=s["type"],
                state=s["state"],
                skill_key=s.get("skill_key"),
                aliases=s.get("aliases", []),
                availability=s.get("availability", "available"),
                availability_reason=s.get("availability_details", {}).get("reason"),
                source_path=s.get("source_path"),
                source_format=s.get("source_format"),
                source_scope=_classify_skill_source(s.get("source_path"))[0],
                removable=_classify_skill_source(s.get("source_path"))[1],
            )
            for s in skills
        ]
    
    @app.get("/api/tools")
    async def list_tools():
        """List all registered tools."""
        from localclaw.tools.base import get_tool_registry
        
        registry = get_tool_registry()
        return registry.get_all_info()
    
    @app.get("/api/clawhub/search")
    async def clawhub_search(query: str = ""):
        registry = get_tool_registry()
        result = await registry.execute("clawhub_search", query=query)
        if result.status == "success":
            return {
                "skills": result.data.get("skills", []),
                "remote_error": result.data.get("remote_error"),
            }
        return {"skills": [], "error": result.message}

    @app.get("/api/clawhub/scan", response_model=SkillSecurityReviewResponse)
    async def clawhub_scan(skill_id: str):
        registry = get_tool_registry()
        result = await registry.execute("clawhub_scan", skill_id=skill_id)
        if result.status != "success":
            raise HTTPException(status_code=502, detail=result.message)
        return result.data.get("scan", {})

    @app.post("/api/clawhub/install", response_model=SkillInstallResponse)
    async def clawhub_install(skill_id: str, decision: Optional[str] = None):
        registry = get_tool_registry()
        result = await registry.execute("clawhub_install", skill_id=skill_id, decision=decision)
        if result.status == "success":
            return {
                "installed": True,
                "skill_path": result.data.get("skill_path", ""),
                "scan": result.data.get("scan"),
                "guard": result.data.get("guard"),
            }
        return {
            "installed": False,
            "requires_review": bool(result.data.get("requires_review")),
            "error": result.message,
            "scan": result.data.get("scan"),
            "guard": result.data.get("guard"),
        }

    @app.post("/api/clawhub/remove")
    async def clawhub_remove(skill_id: str):
        registry = get_tool_registry()
        result = await registry.execute("clawhub_remove", skill_id=skill_id)
        if result.status == "success":
            return {"removed": True}
        return {"removed": False, "error": result.message}
    
    @app.get("/api/config")
    async def get_config():
        """Get current configuration."""
        settings = get_settings()
        weixin_reply_config = resolve_weixin_reply_configuration(settings=settings)
        return {
            "mode": settings.mode.value,
            "llm_enabled": settings.llm_enabled,
            "llm_parse_only": settings.llm_parse_only,
            "model_provider": settings.model_provider.value,
            "model_name": settings.model_name,
            "model_base_url": settings.get_model_base_url(),
            "clawhub_base_url": settings.get_clawhub_base_url(),
            "clawhub_has_token": bool(settings.get_clawhub_token()),
            "bundled_skill_catalog_dir": str(settings.bundled_skill_catalog_dir),
            "managed_skills_dir": str(settings.managed_skills_dir),
            "configured_skill_dirs": [str(path) for path in settings.extra_skill_dirs],
            "data_dir": str(settings.data_dir),
            "wechat_personal_enabled": settings.wechat_personal_enabled,
            "wechat_personal_reply_via_proxy": settings.wechat_personal_reply_via_proxy,
            "wechat_personal_has_proxy_url": bool(settings.wechat_personal_proxy_url),
            "weixin_enabled": settings.weixin_enabled,
            "weixin_webhook_path": _normalize_path(settings.weixin_webhook_path, "/weixin/messages"),
            "weixin_reply_via_api": weixin_reply_config["reply_enabled"],
            "weixin_has_webhook_token": bool(settings.weixin_webhook_token),
            "weixin_has_bot_token": bool(weixin_reply_config["bot_token"]),
            "whatsapp_enabled": settings.whatsapp_enabled,
            "whatsapp_reply_via_cloud_api": settings.whatsapp_reply_via_cloud_api,
            "whatsapp_has_phone_number_id": bool(settings.whatsapp_phone_number_id),
            "skill_install_protection_mode": settings.skill_install_protection_mode.value,
            "skill_isolation_require_approval": settings.skill_isolation_require_approval,
            "skill_isolation_block_critical": settings.skill_isolation_block_critical,
        }

    @app.get("/api/system/service", response_model=BackgroundServiceStatusResponse)
    async def get_system_service_status():
        """Return LocalClaw background-service status for the Settings UI."""

        return get_background_service_status()

    @app.post("/api/system/service/install", response_model=BackgroundServiceActionResponse)
    async def install_system_service():
        """Install LocalClaw as a Windows background service."""

        return install_background_service()

    @app.post("/api/system/service/start", response_model=BackgroundServiceActionResponse)
    async def start_system_service():
        """Start the LocalClaw Windows service."""

        return start_background_service()

    @app.post("/api/system/service/stop", response_model=BackgroundServiceActionResponse)
    async def stop_system_service():
        """Stop the LocalClaw Windows service."""

        return stop_background_service()

    @app.post("/api/system/service/uninstall", response_model=BackgroundServiceActionResponse)
    async def uninstall_system_service():
        """Uninstall the LocalClaw Windows service."""

        return uninstall_background_service()

    @app.get("/api/channels/wechat-personal/status")
    async def get_personal_wechat_status():
        """Return status information for the experimental personal WeChat bridge."""
        settings = get_settings()
        return {
            "enabled": settings.wechat_personal_enabled,
            "reply_via_proxy": settings.wechat_personal_reply_via_proxy,
            "has_inbound_token": bool(settings.wechat_personal_inbound_token),
            "has_proxy_url": bool(settings.wechat_personal_proxy_url),
            "has_api_key": bool(settings.wechat_personal_api_key),
        }

    @app.get("/api/channels/weixin/status")
    async def get_weixin_status():
        """Return status information for the Weixin webhook channel."""
        settings = get_settings()
        return _weixin_channel_status_payload(settings)

    @app.post("/api/channels/weixin/login/start", response_model=WeixinLoginSessionResponse)
    async def start_weixin_login(request: WeixinLoginStartRequest):
        """Start a visual Weixin QR login session."""

        settings = get_settings()
        try:
            session = await start_weixin_qr_login(
                settings=settings,
                account_id=(request.account_id or "").strip(),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to create Weixin QR login: {exc}",
            ) from exc

        return _serialize_weixin_login_session(session)

    @app.get("/api/channels/weixin/login/{session_id}", response_model=WeixinLoginSessionResponse)
    async def get_weixin_login_session(session_id: str):
        """Poll a Weixin QR login session."""

        settings = get_settings()
        try:
            session = await poll_weixin_qr_login(session_id, settings=settings)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown Weixin login session") from exc

        if (session.status or "").strip().lower() == "confirmed":
            await _ensure_weixin_poll_tasks_running()

        return _serialize_weixin_login_session(session)

    @app.get("/api/channels/whatsapp/status")
    async def get_whatsapp_status():
        """Return status information for the WhatsApp Cloud API channel."""
        settings = get_settings()
        return {
            "enabled": settings.whatsapp_enabled,
            "reply_via_cloud_api": settings.whatsapp_reply_via_cloud_api,
            "has_verify_token": bool(settings.whatsapp_verify_token),
            "has_app_secret": bool(settings.whatsapp_app_secret),
            "has_access_token": bool(settings.whatsapp_access_token),
            "has_phone_number_id": bool(settings.whatsapp_phone_number_id),
            "graph_base_url": settings.whatsapp_graph_base_url,
            "graph_api_version": settings.whatsapp_graph_api_version,
        }

    @app.post("/api/channels/wechat-personal/test", response_model=ChannelTestResponse)
    async def test_personal_wechat_channel(request: PersonalWeChatTestRequest):
        """Run a manual connectivity test against the personal WeChat bridge."""

        settings = get_settings()
        reply_target = (request.reply_target or "").strip()
        sender_id = (request.sender_id or reply_target or "wechat-test-user").strip()
        conversation_id = (request.conversation_id or reply_target or sender_id).strip()
        request_preview = {
            "reply_target": reply_target,
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "sender_name": request.sender_name or "",
            "message_id": request.message_id or "",
            "text": request.text,
        }

        missing: List[str] = []
        if not settings.wechat_personal_enabled:
            missing.append("LOCALCLAW_WECHAT_PERSONAL_ENABLED")
        if not settings.wechat_personal_reply_via_proxy:
            missing.append("LOCALCLAW_WECHAT_PERSONAL_REPLY_VIA_PROXY")
        if not settings.wechat_personal_proxy_url:
            missing.append("LOCALCLAW_WECHAT_PERSONAL_PROXY_URL")
        if not reply_target:
            missing.append("reply_target")

        if missing:
            return ChannelTestResponse(
                ok=True,
                mode="dry_run",
                channel="wechat_personal",
                summary=(
                    "Dry run only. Enable the personal WeChat bridge proxy path and "
                    "provide a reply target to send a live test."
                ),
                request=request_preview,
                missing=missing,
            )

        envelope = PersonalWeChatEnvelope(
            content=request.text,
            sender_id=sender_id,
            conversation_id=conversation_id,
            reply_target=reply_target,
            sender_name=request.sender_name,
            message_id=request.message_id,
            bridge_name="manual-test",
            raw_payload={"manual_test": True},
        )
        delivery = await send_personal_wechat_reply(
            envelope=envelope,
            reply_text=request.text,
            settings=settings,
        )
        status_code = int((delivery or {}).get("status_code", 0))
        ok = 200 <= status_code < 300
        summary = (
            f"Live bridge test delivered with HTTP {status_code}."
            if ok
            else f"Bridge proxy returned HTTP {status_code or 'unknown'}."
        )
        return ChannelTestResponse(
            ok=ok,
            mode="live",
            channel="wechat_personal",
            summary=summary,
            request=request_preview,
            delivery=delivery,
        )

    @app.post("/api/channels/weixin/test", response_model=ChannelTestResponse)
    async def test_weixin_channel(request: WeixinTestRequest):
        """Run a manual connectivity test against the Weixin outbound API."""

        settings = get_settings()
        recipient = (request.recipient or "").strip()
        account_id = (request.account_id or "").strip()
        provided_context_token = (request.context_token or "").strip()
        reply_config = resolve_weixin_reply_configuration(settings=settings, account_id=account_id)
        cached_context_token = (
            get_cached_weixin_context_token(account_id, recipient, settings=settings)
            if recipient
            else None
        )
        resolved_context_token = provided_context_token or (cached_context_token or "")
        request_preview = {
            "recipient": recipient,
            "account_id": account_id,
            "context_token_source": (
                "provided"
                if provided_context_token
                else ("cache" if cached_context_token else "missing")
            ),
            "text": request.text,
        }

        missing: List[str] = []
        if not settings.weixin_enabled:
            missing.append("LOCALCLAW_WEIXIN_ENABLED")
        if not reply_config["reply_enabled"]:
            missing.append("LOCALCLAW_WEIXIN_REPLY_VIA_API")
        if not reply_config["bot_token"]:
            missing.append("LOCALCLAW_WEIXIN_BOT_TOKEN")
        if not recipient:
            missing.append("recipient")
        if not resolved_context_token:
            missing.append("context_token")

        if missing:
            return ChannelTestResponse(
                ok=True,
                mode="dry_run",
                channel="weixin",
                summary=(
                    "Dry run only. Provide outbound API credentials or use Scan to Login, then "
                    "set a recipient and context_token or trigger an inbound message first so "
                    "LocalClaw can cache it."
                ),
                request=request_preview,
                missing=missing,
            )

        envelope = WeixinEnvelope(
            content=request.text,
            sender_id=recipient,
            account_id=account_id,
            message_id="",
            timestamp_ms=None,
            timestamp=None,
            context_token=provided_context_token,
            raw_payload={"manual_test": True},
        )
        delivery = await send_weixin_text_reply(
            envelope=envelope,
            reply_text=request.text,
            settings=settings,
            context_token_override=resolved_context_token,
        )
        delivery_error = _weixin_delivery_error(delivery)
        status_code = int((delivery or {}).get("status_code", 0))
        ok = delivery_error is None
        summary = (
            f"Live Weixin test delivered with HTTP {status_code}."
            if ok
            else delivery_error
        )
        return ChannelTestResponse(
            ok=ok,
            mode="live",
            channel="weixin",
            summary=summary,
            request=request_preview,
            delivery=delivery,
        )

    @app.post("/api/channels/whatsapp/test", response_model=ChannelTestResponse)
    async def test_whatsapp_channel(request: WhatsAppTestRequest):
        """Run a manual connectivity test against the WhatsApp Cloud API."""

        settings = get_settings()
        recipient = (request.recipient or "").strip()
        phone_number_id = (request.phone_number_id or settings.whatsapp_phone_number_id or "").strip()
        request_preview = {
            "recipient": recipient,
            "phone_number_id": phone_number_id,
            "reply_to_message_id": request.reply_to_message_id or "",
            "sender_name": request.sender_name or "",
            "text": request.text,
        }

        missing: List[str] = []
        if not settings.whatsapp_enabled:
            missing.append("LOCALCLAW_WHATSAPP_ENABLED")
        if not settings.whatsapp_reply_via_cloud_api:
            missing.append("LOCALCLAW_WHATSAPP_REPLY_VIA_CLOUD_API")
        if not settings.whatsapp_access_token:
            missing.append("LOCALCLAW_WHATSAPP_ACCESS_TOKEN")
        if not phone_number_id:
            missing.append("LOCALCLAW_WHATSAPP_PHONE_NUMBER_ID")
        if not recipient:
            missing.append("recipient")

        if missing:
            return ChannelTestResponse(
                ok=True,
                mode="dry_run",
                channel="whatsapp",
                summary=(
                    "Dry run only. Add Cloud API credentials and a target recipient "
                    "to send a live WhatsApp test."
                ),
                request=request_preview,
                missing=missing,
            )

        envelope = WhatsAppEnvelope(
            content=request.text,
            sender_id=recipient,
            sender_name=request.sender_name,
            message_id=(request.reply_to_message_id or "").strip(),
            phone_number_id=phone_number_id,
            display_phone_number=None,
            raw_payload={"manual_test": True},
        )
        delivery = await send_whatsapp_text_reply(
            envelope=envelope,
            reply_text=request.text,
            settings=settings,
        )
        status_code = int((delivery or {}).get("status_code", 0))
        ok = 200 <= status_code < 300
        summary = (
            f"Live WhatsApp test delivered with HTTP {status_code}."
            if ok
            else f"WhatsApp Cloud API returned HTTP {status_code or 'unknown'}."
        )
        return ChannelTestResponse(
            ok=ok,
            mode="live",
            channel="whatsapp",
            summary=summary,
            request=request_preview,
            delivery=delivery,
        )

    @app.get("/api/channels", response_model=ChannelsOverviewResponse)
    async def get_channels_overview():
        """Return a UI-friendly overview of supported chat channels."""
        settings = get_settings()
        weixin_status = _weixin_channel_status_payload(settings)
        return ChannelsOverviewResponse(
            channels=[
                {
                    "key": "wechat_personal",
                    "name": "Personal WeChat",
                    "kind": "bridge",
                    "enabled": settings.wechat_personal_enabled,
                    "reply_mode": (
                        "bridge_proxy"
                        if settings.wechat_personal_reply_via_proxy
                        else "inline_response"
                    ),
                    "webhook_path": "/api/channels/wechat-personal/webhook",
                    "status_path": "/api/channels/wechat-personal/status",
                    "test_path": "/api/channels/wechat-personal/test",
                    "required_env": [
                        "LOCALCLAW_WECHAT_PERSONAL_ENABLED",
                        "LOCALCLAW_WECHAT_PERSONAL_INBOUND_TOKEN",
                    ],
                    "optional_env": [
                        "LOCALCLAW_WECHAT_PERSONAL_PROXY_URL",
                        "LOCALCLAW_WECHAT_PERSONAL_API_KEY",
                        "LOCALCLAW_WECHAT_PERSONAL_REPLY_VIA_PROXY",
                    ],
                    "checks": {
                        "has_inbound_token": bool(settings.wechat_personal_inbound_token),
                        "has_proxy_url": bool(settings.wechat_personal_proxy_url),
                        "has_api_key": bool(settings.wechat_personal_api_key),
                    },
                    "summary": "Experimental bridge for personal WeChat webhook adapters.",
                    "notes": [
                        "Bridge must POST JSON to the webhook URL.",
                        "Use X-LocalClaw-Token or Authorization: Bearer <token>.",
                        "Routine commands can use /cmd and safe_shell automatically.",
                    ],
                },
                {
                    "key": "weixin",
                    "name": "Weixin",
                    "kind": "official",
                    "enabled": settings.weixin_enabled,
                    "reply_mode": (
                        "weixin_api"
                        if weixin_status["reply_via_api"]
                        else "inline_response"
                    ),
                    "webhook_path": _normalize_path(settings.weixin_webhook_path, "/weixin/messages"),
                    "status_path": "/api/channels/weixin/status",
                    "test_path": "/api/channels/weixin/test",
                    "login_path": "/api/channels/weixin/login/start",
                    "supports_qr_login": True,
                    "required_env": [
                        "LOCALCLAW_WEIXIN_ENABLED",
                    ],
                    "optional_env": [
                        "LOCALCLAW_WEIXIN_WEBHOOK_PATH",
                        "LOCALCLAW_WEIXIN_WEBHOOK_TOKEN",
                        "LOCALCLAW_WEIXIN_ALLOWED_USER_IDS",
                        "LOCALCLAW_WEIXIN_BASE_URL",
                        "LOCALCLAW_WEIXIN_BOT_TOKEN",
                        "LOCALCLAW_WEIXIN_REPLY_VIA_API",
                    ],
                    "checks": {
                        "has_webhook_token": weixin_status["has_webhook_token"],
                        "has_bot_token": weixin_status["has_bot_token"],
                        "has_allowed_user_ids": weixin_status["has_allowed_user_ids"],
                        "has_stored_login": weixin_status["has_stored_login"],
                    },
                    "connected_account": weixin_status["connected_account"],
                    "summary": "Weixin inbound webhook channel with optional outbound ilink API replies and QR login.",
                    "notes": [
                        "Webhook token can be sent via x-weixin-webhook-token or Authorization: Bearer.",
                        "Inbound payload supports both top-level fields and nested message objects.",
                        "Use Scan to Login in this page to save a bot token without editing env first.",
                        "Outbound API replies require a context_token from the latest inbound message.",
                    ],
                },
                {
                    "key": "whatsapp",
                    "name": "WhatsApp Cloud API",
                    "kind": "official",
                    "enabled": settings.whatsapp_enabled,
                    "reply_mode": (
                        "cloud_api"
                        if settings.whatsapp_reply_via_cloud_api
                        else "inline_response"
                    ),
                    "webhook_path": "/api/channels/whatsapp/webhook",
                    "verify_path": "/api/channels/whatsapp/webhook",
                    "status_path": "/api/channels/whatsapp/status",
                    "test_path": "/api/channels/whatsapp/test",
                    "required_env": [
                        "LOCALCLAW_WHATSAPP_ENABLED",
                        "LOCALCLAW_WHATSAPP_VERIFY_TOKEN",
                    ],
                    "optional_env": [
                        "LOCALCLAW_WHATSAPP_APP_SECRET",
                        "LOCALCLAW_WHATSAPP_ACCESS_TOKEN",
                        "LOCALCLAW_WHATSAPP_PHONE_NUMBER_ID",
                        "LOCALCLAW_WHATSAPP_REPLY_VIA_CLOUD_API",
                    ],
                    "checks": {
                        "has_verify_token": bool(settings.whatsapp_verify_token),
                        "has_app_secret": bool(settings.whatsapp_app_secret),
                        "has_access_token": bool(settings.whatsapp_access_token),
                        "has_phone_number_id": bool(settings.whatsapp_phone_number_id),
                    },
                    "summary": "Official WhatsApp Cloud API inbound webhook and optional outbound reply channel.",
                    "notes": [
                        "Meta verification uses the same webhook URL.",
                        "GET verify must receive hub.mode=subscribe, hub.verify_token and hub.challenge.",
                        "POST webhook validates X-Hub-Signature-256 when app secret is configured.",
                    ],
                },
            ]
        )

    @app.get("/api/channels/whatsapp/webhook")
    async def verify_whatsapp_webhook(request: Request):
        """Verify the WhatsApp Cloud API webhook subscription."""
        settings = get_settings()
        if not settings.whatsapp_enabled:
            raise HTTPException(status_code=503, detail="WhatsApp channel is disabled")

        hub_mode = request.query_params.get("hub.mode")
        hub_verify_token = request.query_params.get("hub.verify_token")
        hub_challenge = request.query_params.get("hub.challenge", "")

        if hub_mode != "subscribe":
            raise HTTPException(status_code=400, detail="Invalid hub.mode")
        if not is_valid_whatsapp_verify_token(settings.whatsapp_verify_token, hub_verify_token):
            raise HTTPException(status_code=401, detail="Invalid WhatsApp verify token")

        return PlainTextResponse(content=hub_challenge)

    @app.post("/api/channels/whatsapp/webhook", response_model=WhatsAppWebhookResponse)
    async def whatsapp_webhook(request: Request):
        """Receive WhatsApp Cloud API webhook events."""
        settings = get_settings()
        if not settings.whatsapp_enabled:
            raise HTTPException(status_code=503, detail="WhatsApp channel is disabled")

        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not is_valid_whatsapp_signature(settings.whatsapp_app_secret, body, signature):
            raise HTTPException(status_code=401, detail="Invalid WhatsApp signature")

        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid WhatsApp webhook payload") from exc

        envelope = normalize_whatsapp_webhook_payload(payload)
        if envelope is None:
            return WhatsAppWebhookResponse(
                accepted=True,
                event_type="status",
                status="ignored",
            )

        engine = get_engine()
        task = await engine.process_message(build_whatsapp_message(envelope))
        reply_text = format_task_for_whatsapp(task)
        outbound_delivery = await send_whatsapp_text_reply(
            envelope=envelope,
            reply_text=reply_text,
            settings=settings,
        )

        return WhatsAppWebhookResponse(
            accepted=True,
            event_type="message",
            task_id=task.id,
            status=task.state.value,
            reply={
                "type": "text",
                "text": reply_text,
                "to": envelope.sender_id,
                "reply_to_message_id": envelope.message_id,
            },
            outbound_delivery=outbound_delivery,
        )

    async def weixin_webhook(payload: Dict[str, Any], request: Request) -> WeixinWebhookResponse:
        """Receive inbound Weixin webhook messages."""
        settings = get_settings()
        if not settings.weixin_enabled:
            raise HTTPException(status_code=503, detail="Weixin channel is disabled")

        provided_token = provided_weixin_webhook_token(request.headers)
        if not is_valid_weixin_webhook_token(settings.weixin_webhook_token, provided_token):
            raise HTTPException(status_code=401, detail="Invalid Weixin webhook token")

        try:
            envelope = normalize_weixin_webhook_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return WeixinWebhookResponse(
            **(await _process_weixin_inbound_envelope(envelope, settings))
        )

    configured_weixin_webhook_path = _normalize_path(
        get_settings().weixin_webhook_path,
        "/weixin/messages",
    )
    app.add_api_route(
        configured_weixin_webhook_path,
        weixin_webhook,
        methods=["POST"],
        response_model=WeixinWebhookResponse,
        name="weixin_webhook_configured",
    )
    if configured_weixin_webhook_path != "/api/channels/weixin/webhook":
        app.add_api_route(
            "/api/channels/weixin/webhook",
            weixin_webhook,
            methods=["POST"],
            response_model=WeixinWebhookResponse,
            name="weixin_webhook_debug",
        )

    @app.post(
        "/api/channels/wechat-personal/webhook",
        response_model=PersonalWeChatWebhookResponse,
    )
    async def personal_wechat_webhook(payload: Dict[str, Any], request: Request):
        """Receive messages from an experimental personal WeChat bridge."""
        settings = get_settings()
        if not settings.wechat_personal_enabled:
            raise HTTPException(status_code=503, detail="Personal WeChat bridge is disabled")

        inbound_token = request.headers.get("X-LocalClaw-Token")
        authorization = request.headers.get("Authorization")
        if not is_valid_personal_wechat_token(
            settings.wechat_personal_inbound_token,
            inbound_token,
            authorization,
        ):
            raise HTTPException(status_code=401, detail="Invalid personal WeChat bridge token")

        try:
            envelope = normalize_personal_wechat_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        engine = get_engine()
        task = await engine.process_message(build_personal_wechat_message(envelope))
        reply_text = format_task_for_personal_wechat(task)
        bridge_delivery = await send_personal_wechat_reply(
            envelope=envelope,
            reply_text=reply_text,
            settings=settings,
        )

        return PersonalWeChatWebhookResponse(
            accepted=True,
            task_id=task.id,
            status=task.state.value,
            reply={
                "type": "text",
                "text": reply_text,
                "conversation_id": envelope.conversation_id,
                "reply_target": envelope.reply_target,
            },
            bridge_delivery=bridge_delivery,
        )
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}
    
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time updates."""
        await _manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_json()
                
                if data.get("type") == "message":
                    engine = get_engine()
                    message = Message(
                        content=data.get("content", ""),
                        user_id=data.get("user_id", "ws"),
                        channel="websocket",
                    )
                    
                    task = await engine.process_message(message)
                    
                    await websocket.send_json({
                        "type": "result",
                        "task_id": task.id,
                        "status": task.state.value,
                        "message": format_task_for_chat(task),
                        "data": task.result.data if task.result else {},
                    })
        except WebSocketDisconnect:
            _manager.disconnect(websocket)
    
    return app


def get_web_ui_html() -> str:
    """Get the web UI HTML."""
    html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LocalClaw</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23007bff' d='M8 0C3.58 0 0 3.58 0 8s3.58 8 8 8 8-3.58 8-8-3.58-8-8-8zm0 14.5c-3.59 0-6.5-2.91-6.5-6.5S4.41 1.5 8 1.5 14.5 4.41 14.5 8 11.59 14.5 8 14.5zm-1.5-8c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5-.67-1.5-1.5-1.5-1.5.67-1.5 1.5zm0 5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5-.67-1.5-1.5-1.5-1.5.67-1.5 1.5z'/%3E%3C/svg%3E" type="image/svg+xml">
    <script src="https://unpkg.com/axios/dist/axios.min.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .header { text-align: center; margin-bottom: 20px; }
        .header h1 { color: #333; }
        .tabs { display: flex; margin-bottom: 20px; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .tab { flex: 1; padding: 15px; text-align: center; cursor: pointer; border-bottom: 3px solid transparent; }
        .tab.active { border-bottom-color: #007bff; color: #007bff; font-weight: bold; }
        .tab-content { display: none; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 20px; }
        .tab-content.active { display: block; }
        .chat-container { background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .messages { height: 400px; overflow-y: auto; padding: 20px; border-bottom: 1px solid #eee; }
        .message { margin-bottom: 15px; padding: 10px 15px; border-radius: 8px; }
        .message.user { background: #007bff; color: white; margin-left: 20%; }
        .message.system { background: #f0f0f0; margin-right: 20%; }
        .message.error { background: #f8d7da; color: #721c24; }
        .input-area { padding: 15px; display: flex; gap: 10px; }
        .input-area input { flex: 1; padding: 10px 15px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        .input-area button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
        .input-area button:hover { background: #0056b3; }
        .sidebar { margin-top: 20px; background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .sidebar h3 { margin-bottom: 10px; color: #333; }
        .skill-list { list-style: none; }
        .skill-list li { padding: 5px 0; border-bottom: 1px solid #eee; }
        .status { font-size: 12px; color: #666; margin-top: 10px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .task { border: 1px solid #eee; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
        .task h4 { margin-bottom: 10px; color: #333; }
        .task p { margin-bottom: 5px; font-size: 14px; }
        .task-status { display: inline-block; padding: 3px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; }
        .task-status.completed { background-color: #d4edda; color: #155724; }
        .task-status.failed { background-color: #f8d7da; color: #721c24; }
        .task-status.running { background-color: #d1ecf1; color: #0c5460; }
        .skill-card { border: 1px solid #eee; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
        .skill-card h4 { margin-bottom: 10px; color: #333; }
        .skill-card p { margin-bottom: 5px; font-size: 14px; }
        .skill-actions { margin-top: 10px; display: flex; gap: 10px; }
        .skill-actions button { padding: 5px 10px; font-size: 12px; border: none; border-radius: 4px; cursor: pointer; }
        .skill-actions button.install { background: #28a745; color: white; }
        .skill-actions button.uninstall { background: #dc3545; color: white; }
        .skill-actions button:hover { opacity: 0.8; }
    </style>
</head>
<body>
    <div class="container" x-data="app()" x-init="init()">
        <div class="header">
            <h1>🦀 LocalClaw</h1>
            <p class="status">Mode: <span x-text="config.mode || 'loading...'"></span></p>
        </div>
        
        <div class="tabs">
            <div class="tab" :class="{active: activeTab === 'chat'}" @click="activeTab = 'chat'">Chat</div>
            <div class="tab" :class="{active: activeTab === 'tasks'}" @click="activeTab = 'tasks'">Tasks</div>
            <div class="tab" :class="{active: activeTab === 'skills'}" @click="activeTab = 'skills'">Skills</div>
        </div>
        
        <div class="tab-content" x-show="activeTab === 'chat'">
            <div class="chat-container">
                <div class="messages" x-ref="messages">
                    <template x-for="msg in messages" :key="msg.id">
                        <div class="message" :class="msg.type" x-text="msg.content"></div>
                    </template>
                </div>
                
                <div class="input-area">
                    <input type="text" x-model="input" @keyup.enter="sendMessage()" placeholder="Type a message or /skill_name params...">
                    <button @click="sendMessage()" :disabled="loading">Send</button>
                </div>
            </div>
        </div>
        
        <div class="tab-content" x-show="activeTab === 'tasks'">
            <h2>Task History</h2>
            <div class="task-list">
                <template x-for="task in tasks" :key="task.id">
                    <div class="task">
                        <h4>Task {{ task.id }}</h4>
                        <p><strong>Status:</strong> <span class="task-status" :class="task.state.toLowerCase()">{{ task.state }}</span></p>
                        <p><strong>Created:</strong> <span x-text="new Date(task.created_at).toLocaleString()"></span></p>
                        <p><strong>Completed:</strong> <span x-text="task.completed_at ? new Date(task.completed_at).toLocaleString() : 'N/A'"></span></p>
                        <p><strong>Result:</strong> <span x-text="JSON.stringify(task.result)"></span></p>
                        <p><strong>Error:</strong> <span x-text="task.error || 'N/A'"></span></p>
                    </div>
                </template>
            </div>
        </div>
        
        <div class="tab-content" x-show="activeTab === 'skills'">
            <h2>Skill Management</h2>
            <div class="grid">
                <div>
                    <h3>Installed Skills</h3>
                    <div class="skill-list">
                        <template x-for="skill in skills" :key="skill.name">
                            <div class="skill-card">
                                <h4>{{ skill.name }} v{{ skill.version }}</h4>
                                <p><strong>Description:</strong> {{ skill.description }}</p>
                                <p><strong>Type:</strong> {{ skill.type }}</p>
                                <p><strong>State:</strong> {{ skill.state }}</p>
                                <div class="skill-actions">
                                    <button class="uninstall" @click="uninstallSkill(skill.name)">Uninstall</button>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
                <div>
                    <h3>ClawHub Skills</h3>
                    <div class="search-box" style="margin-bottom: 15px;">
                        <input type="text" x-model="searchQuery" placeholder="Search skills..." style="width: 70%; padding: 8px;">
                        <button @click="searchClawHub(searchQuery)" style="padding: 8px 15px; margin-left: 5px;">Search</button>
                    </div>
                    <div class="skill-list">
                        <template x-for="skill in availableSkills" :key="skill.id">
                            <div class="skill-card">
                                <h4>{{ skill.name }} v{{ skill.version }}</h4>
                                <p><strong>Description:</strong> {{ skill.description }}</p>
                                <p><strong>Author:</strong> {{ skill.author || 'Unknown' }}</p>
                                <div class="skill-actions">
                                    <template x-if="isSkillInstalled(skill.id)">
                                        <button class="uninstall" @click="uninstallSkill(skill.id)">Uninstall</button>
                                    </template>
                                    <template x-if="!isSkillInstalled(skill.id)">
                                        <button class="install" @click="installSkill(skill.id)" :disabled="installing === skill.id">
                                            <span x-text="installing === skill.id ? 'Installing...' : 'Install'"></span>
                                        </button>
                                    </template>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
        const _FALLBACK_SKILLS = [
            { id: 'skill-vetter', name: 'Skill-Vetter', version: '1.0.0', description: 'Review a skill before installation', author: 'LocalClaw Team' },
            { id: 'agent-browser', name: 'Agent Browser', version: '1.0.0', description: 'Fetch a page or endpoint over HTTP', author: 'LocalClaw Team' },
            { id: 'tavily-web-search', name: 'Tavily Web Search', version: '1.0.0', description: 'Search with Tavily when configured', author: 'LocalClaw Team' },
            { id: 'find-skills', name: 'Find-Skills', version: '1.0.0', description: 'Search bundled skills and ClawHub skills', author: 'LocalClaw Team' },
            { id: 'weather', name: 'Weather', version: '1.0.0', description: 'Get current weather and forecasts', author: 'LocalClaw Team' },
            { id: 'self-improving-agent', name: 'Self-Improving-Agent', version: '1.0.0', description: 'Generate a concrete improvement plan', author: 'LocalClaw Team' },
            { id: 'summarize', name: 'Summarize', version: '1.0.0', description: 'Summarize text with the local model', author: 'LocalClaw Team' },
            { id: 'humanizer', name: 'Humanizer', version: '1.0.0', description: 'Rewrite text to sound more natural', author: 'LocalClaw Team' }
        ];

        window.app = function() {
            return {
                activeTab: 'chat',
                messages: [],
                input: '',
                loading: false,
                skills: [],
                availableSkills: [],
                tasks: [],
                config: {},
                searchQuery: '',
                installing: null,
                
                async init() {
                    await this.loadConfig();
                    await Promise.all([this.loadSkills(), this.loadTasks(), this.searchClawHub('')]);

                    setInterval(() => {
                        Promise.all([this.loadSkills(), this.loadTasks()]);
                    }, 5000);
                },
                
                async loadConfig() {
                    try {
                        const response = await axios.get('/api/config');
                        this.config = response.data;
                    } catch (e) {
                        console.error('Failed to load config:', e);
                    }
                },
                
                async loadSkills() {
                    try {
                        const response = await axios.get('/api/skills');
                        this.skills = response.data;
                    } catch (e) {
                        console.error('Failed to load skills:', e);
                    }
                },
                
                async loadTasks() {
                    try {
                        const response = await axios.get('/api/tasks');
                        this.tasks = response.data;
                    } catch (e) {
                        console.error('Failed to load tasks:', e);
                    }
                },
                
                async searchClawHub(query) {
                    try {
                        const response = await axios.get('/api/clawhub/search', { params: { query: query || '' } });
                        this.availableSkills = response.data.skills || [];
                        if (this.availableSkills.length === 0) {
                            this.availableSkills = _FALLBACK_SKILLS;
                        }
                    } catch (e) {
                        console.error('Failed to search ClawHub:', e);
                        if (e.response && e.response.status === 429) {
                            console.warn('ClawHub rate limit exceeded, using fallback skills');
                        }
                        this.availableSkills = _FALLBACK_SKILLS;
                    }
                },
                
                isSkillInstalled(skillId) {
                    return this.skills.some(s => s.name.toLowerCase() === skillId.toLowerCase());
                },
                
                async sendMessage() {
                    if (!this.input.trim() || this.loading) return;
                    
                    const content = this.input.trim();
                    this.input = '';
                    
                    this.messages.push({
                        id: Date.now(),
                        type: 'user',
                        content: content
                    });
                    
                    this.loading = true;
                    
                    try {
                        const response = await axios.post('/api/message', {
                            content: content,
                            user_id: 'web',
                            channel: 'web'
                        });
                        
                        const data = response.data;
                        
                        let responseText = data.message || 'Done';
                        if (data.data) {
                            const stepIds = Object.keys(data.data);
                            if (stepIds.length > 0) {
                                const firstStepId = stepIds[0];
                                const stepResult = data.data[firstStepId];
                                let weatherData = stepResult.body || stepResult.result;
                                if (weatherData && typeof weatherData === 'object' && weatherData.current_condition) {
                                    const weather = weatherData;
                                    const current = weather.current_condition[0];
                                    const location = weather.nearest_area[0];
                                    responseText = '🌍 位置: ' + (location.areaName[0].value || '未知') + ', ' + (location.country[0].value || '') + '\\n';
                                    responseText += '🌡️ 温度: ' + current.temp_C + '°C (体感 ' + current.FeelsLikeC + '°C)\\n';
                                    responseText += '☁️ 天气: ' + current.weatherDesc[0].value + '\\n';
                                    responseText += '💨 风速: ' + current.windspeedKmph + ' km/h\\n';
                                    responseText += '💧 湿度: ' + current.humidity + '%';
                                    if (weather.weather && weather.weather.length > 1) {
                                        const tomorrow = weather.weather[1];
                                        responseText += '\\n\\n📅 明天预报:\\n';
                                        responseText += '🌡️ 温度: ' + tomorrow.mintempC + '°C ~ ' + tomorrow.maxtempC + '°C\\n';
                                        responseText += '☁️ 天气: ' + tomorrow.hourly[4].weatherDesc[0].value;
                                    }
                                } else if (stepResult && stepResult.result) {
                                    responseText = stepResult.result;
                                } else if (stepResult && stepResult.message) {
                                    responseText = stepResult.message;
                                } else if (stepResult && stepResult.files && stepResult.directories) {
                                    let fileList = '📁 目录: ' + stepResult.path + '\\n\\n';
                                    if (stepResult.directories && stepResult.directories.length > 0) {
                                        fileList += '📂 目录:\\n';
                                        stepResult.directories.forEach(dir => {
                                            fileList += '  📁 ' + dir + '\\n';
                                        });
                                        fileList += '\\n';
                                    }
                                    if (stepResult.files && stepResult.files.length > 0) {
                                        fileList += '📄 文件:\\n';
                                        stepResult.files.forEach(file => {
                                            fileList += '  📄 ' + file + '\\n';
                                        });
                                    }
                                    responseText = fileList;
                                }
                            } else if (data.data.result) {
                                responseText = data.data.result;
                            } else if (data.data.message) {
                                responseText = data.data.message;
                            }
                        }
                        
                        this.messages.push({
                            id: Date.now() + 1,
                            type: data.status === 'failed' ? 'error' : 'system',
                            content: responseText
                        });
                    } catch (e) {
                        this.messages.push({
                            id: Date.now() + 1,
                            type: 'error',
                            content: 'Error: ' + (e.response?.data?.detail || e.message)
                        });
                    } finally {
                        this.loading = false;
                        setTimeout(() => {
                            const messagesElement = document.querySelector('.messages');
                            if (messagesElement) {
                                messagesElement.scrollTop = messagesElement.scrollHeight;
                            }
                        }, 100);
                    }
                },
                
                async installSkill(skillId) {
                    if (this.installing === skillId) return;
                    this.installing = skillId;
                    try {
                        const response = await axios.post('/api/clawhub/install?skill_id=' + encodeURIComponent(skillId));
                        if (response.data.installed) {
                            alert('Skill ' + skillId + ' installed successfully!');
                            await this.loadSkills();
                        } else {
                            alert('Failed to install skill: ' + (response.data.error || 'Unknown error'));
                        }
                    } catch (e) {
                        console.error('Failed to install skill:', e);
                        alert('Failed to install skill: ' + (e.response?.data?.error || e.message || 'Unknown error'));
                    } finally {
                        this.installing = null;
                    }
                },
                
                async uninstallSkill(skillName) {
                    try {
                        const response = await axios.post('/api/clawhub/remove?skill_id=' + encodeURIComponent(skillName));
                        if (response.data.removed) {
                            alert('Skill ' + skillName + ' uninstalled successfully!');
                            await this.loadSkills();
                        } else {
                            alert('Failed to uninstall skill: ' + (response.data.error || 'Unknown error'));
                        }
                    } catch (e) {
                        console.error('Failed to uninstall skill:', e);
                        alert('Failed to uninstall skill: ' + (e.response?.data?.error || e.message || 'Unknown error'));
                    }
                }
            };
        };
    </script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
</body>
</html>
'''
    return html

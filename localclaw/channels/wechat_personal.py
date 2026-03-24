"""Experimental personal WeChat bridge helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from localclaw.config.settings import Settings, get_settings
from localclaw.core.models import Message, Task, TaskState


logger = logging.getLogger(__name__)


@dataclass
class PersonalWeChatEnvelope:
    """Normalized incoming personal WeChat payload."""

    content: str
    sender_id: str
    conversation_id: str
    reply_target: str
    sender_name: Optional[str] = None
    message_id: Optional[str] = None
    bridge_name: str = "generic"
    raw_payload: Optional[Dict[str, Any]] = None


def _pick_value(data: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Return the first non-empty value for a set of keys."""

    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        elif value not in (None, "", [], {}):
            return value
    return None


def _coerce_string(value: Any) -> str:
    """Convert a bridge field to a string."""

    if isinstance(value, dict):
        nested = _pick_value(
            value,
            "id",
            "wxid",
            "user_id",
            "userId",
            "conversation_id",
            "conversationId",
            "name",
            "nickname",
        )
        return str(nested or "").strip()
    return str(value or "").strip()


def normalize_personal_wechat_payload(payload: Dict[str, Any]) -> PersonalWeChatEnvelope:
    """Normalize a third-party personal WeChat webhook payload."""

    source = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    content = _coerce_string(
        _pick_value(source, "content", "text", "message", "msg", "body", "question")
    )
    sender_raw = _pick_value(
        source,
        "sender_id",
        "senderId",
        "from_user",
        "fromUser",
        "user_id",
        "userId",
        "talker",
        "wxid",
        "sender",
        "from",
    )
    conversation_raw = _pick_value(
        source,
        "conversation_id",
        "conversationId",
        "room_id",
        "roomId",
        "chat_id",
        "chatId",
        "reply_target",
        "replyTarget",
        "talker",
        "room",
        "chat",
    )
    reply_raw = _pick_value(
        source,
        "reply_target",
        "replyTarget",
        "conversation_id",
        "conversationId",
        "room_id",
        "roomId",
        "chat_id",
        "chatId",
        "talker",
    )

    sender_id = _coerce_string(sender_raw)
    conversation_id = _coerce_string(conversation_raw) or sender_id
    reply_target = _coerce_string(reply_raw) or conversation_id
    sender_name = _coerce_string(
        _pick_value(source, "sender_name", "senderName", "nickname", "name")
    ) or None
    message_id = _coerce_string(
        _pick_value(source, "message_id", "messageId", "msg_id", "msgId")
    ) or None
    bridge_name = _coerce_string(_pick_value(payload, "bridge", "provider", "source")) or "generic"

    if not content:
        raise ValueError("Missing message content in personal WeChat payload")
    if not sender_id:
        raise ValueError("Missing sender identifier in personal WeChat payload")

    return PersonalWeChatEnvelope(
        content=content,
        sender_id=sender_id,
        conversation_id=conversation_id,
        reply_target=reply_target,
        sender_name=sender_name,
        message_id=message_id,
        bridge_name=bridge_name,
        raw_payload=payload,
    )


def build_personal_wechat_message(envelope: PersonalWeChatEnvelope) -> Message:
    """Create the runtime message object from a normalized bridge payload."""

    return Message(
        content=envelope.content,
        user_id=envelope.sender_id,
        channel="wechat_personal",
        metadata={
            "conversation_id": envelope.conversation_id,
            "reply_target": envelope.reply_target,
            "sender_name": envelope.sender_name,
            "message_id": envelope.message_id,
            "bridge_name": envelope.bridge_name,
        },
    )


def format_task_for_personal_wechat(task: Task) -> str:
    """Format a task result for a compact chat reply."""

    if task.state == TaskState.VERIFYING:
        current_step = task.get_current_step()
        step_name = current_step.tool_name if current_step else "operation"
        return f"需要人工确认后才能继续执行: {step_name}"

    if task.state == TaskState.FAILED:
        return f"执行失败: {task.error or 'unknown error'}"

    result = task.result
    result_data = getattr(result, "data", {}) or {}

    if "result" in result_data:
        return str(result_data["result"])
    if "message" in result_data:
        return str(result_data["message"])
    if "content" in result_data:
        return str(result_data["content"])

    if len(result_data) == 1:
        first_value = next(iter(result_data.values()))
        if isinstance(first_value, dict):
            if "stdout" in first_value or "stderr" in first_value:
                parts = []
                if first_value.get("command"):
                    parts.append(f"命令: {first_value['command']}")
                if "exit_code" in first_value:
                    parts.append(f"退出码: {first_value['exit_code']}")
                if first_value.get("stdout"):
                    parts.append(f"输出:\n{first_value['stdout']}")
                if first_value.get("stderr"):
                    parts.append(f"错误:\n{first_value['stderr']}")
                return "\n\n".join(parts)

            if "files" in first_value or "directories" in first_value:
                lines = [f"路径: {first_value.get('path', '')}".strip()]
                directories = first_value.get("directories") or []
                files = first_value.get("files") or []
                if directories:
                    lines.append("目录:")
                    lines.extend(f"- {item}" for item in directories)
                if files:
                    lines.append("文件:")
                    lines.extend(f"- {item}" for item in files)
                if not directories and not files:
                    lines.append("(empty)")
                return "\n".join(line for line in lines if line)

            if "result" in first_value:
                return str(first_value["result"])
            if "message" in first_value:
                return str(first_value["message"])

    if getattr(result, "message", ""):
        return str(result.message)

    return str(result_data)


def is_valid_personal_wechat_token(
    expected_token: Optional[str],
    provided_token: Optional[str],
    authorization: Optional[str],
) -> bool:
    """Check the shared secret used by a bridge webhook."""

    if not expected_token:
        return True
    if provided_token and provided_token == expected_token:
        return True
    if authorization:
        prefix = "Bearer "
        if authorization.startswith(prefix) and authorization[len(prefix) :] == expected_token:
            return True
    return False


async def send_personal_wechat_reply(
    envelope: PersonalWeChatEnvelope,
    reply_text: str,
    settings: Optional[Settings] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Dict[str, Any]]:
    """Optionally forward the reply to a third-party bridge proxy."""

    resolved_settings = settings or get_settings()
    if not (
        resolved_settings.wechat_personal_enabled
        and resolved_settings.wechat_personal_reply_via_proxy
        and resolved_settings.wechat_personal_proxy_url
    ):
        return None

    payload = {
        "channel": "wechat_personal",
        "conversation_id": envelope.conversation_id,
        "reply_target": envelope.reply_target,
        "sender_id": envelope.sender_id,
        "sender_name": envelope.sender_name,
        "message_id": envelope.message_id,
        "text": reply_text,
    }
    headers = {"Content-Type": "application/json"}
    if resolved_settings.wechat_personal_api_key:
        headers["Authorization"] = f"Bearer {resolved_settings.wechat_personal_api_key}"
        headers["X-API-Key"] = resolved_settings.wechat_personal_api_key

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=resolved_settings.default_timeout)
        close_client = True

    try:
        response = await client.post(
            resolved_settings.wechat_personal_proxy_url,
            json=payload,
            headers=headers,
        )
        try:
            body = response.json()
        except Exception:
            body = response.text
        return {"status_code": response.status_code, "body": body}
    finally:
        if close_client:
            await client.aclose()

"""WhatsApp Cloud API channel helpers."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from localclaw.channels.wechat_personal import format_task_for_personal_wechat
from localclaw.config.settings import Settings, get_settings
from localclaw.core.models import Message, Task


logger = logging.getLogger(__name__)


@dataclass
class WhatsAppEnvelope:
    """Normalized incoming WhatsApp message."""

    content: str
    sender_id: str
    sender_name: Optional[str]
    message_id: str
    phone_number_id: Optional[str]
    display_phone_number: Optional[str]
    raw_payload: Dict[str, Any]


def is_valid_whatsapp_verify_token(
    expected_token: Optional[str],
    provided_token: Optional[str],
) -> bool:
    """Validate the Cloud API verification token."""

    if not expected_token:
        return True
    return bool(provided_token) and provided_token == expected_token


def is_valid_whatsapp_signature(
    app_secret: Optional[str],
    body: bytes,
    signature_header: Optional[str],
) -> bool:
    """Validate the x-hub-signature-256 header against the shared app secret."""

    if not app_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


def normalize_whatsapp_webhook_payload(payload: Dict[str, Any]) -> Optional[WhatsAppEnvelope]:
    """Extract the first inbound message from a Cloud API webhook payload."""

    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            if change.get("field") != "messages":
                continue

            value = change.get("value", {}) or {}
            messages = value.get("messages") or []
            if not messages:
                return None

            message = messages[0]
            contacts = value.get("contacts") or []
            metadata = value.get("metadata", {}) or {}
            sender_name = None
            if contacts:
                sender_name = ((contacts[0].get("profile") or {}).get("name")) or None

            content = _extract_whatsapp_content(message)
            sender_id = str(message.get("from") or "").strip()
            message_id = str(message.get("id") or "").strip()
            phone_number_id = str(metadata.get("phone_number_id") or "").strip() or None
            display_phone_number = (
                str(metadata.get("display_phone_number") or "").strip() or None
            )

            if not content:
                raise ValueError("Unsupported or empty WhatsApp message payload")
            if not sender_id or not message_id:
                raise ValueError("Missing WhatsApp sender or message identifier")

            return WhatsAppEnvelope(
                content=content,
                sender_id=sender_id,
                sender_name=sender_name,
                message_id=message_id,
                phone_number_id=phone_number_id,
                display_phone_number=display_phone_number,
                raw_payload=payload,
            )

    return None


def _extract_whatsapp_content(message: Dict[str, Any]) -> str:
    """Extract user-facing text from a WhatsApp message object."""

    message_type = message.get("type")
    if message_type == "text":
        return str(((message.get("text") or {}).get("body")) or "").strip()

    if message_type == "interactive":
        interactive = message.get("interactive") or {}
        button_reply = interactive.get("button_reply") or {}
        list_reply = interactive.get("list_reply") or {}
        return str(
            button_reply.get("title")
            or list_reply.get("title")
            or list_reply.get("description")
            or ""
        ).strip()

    if message_type == "button":
        return str(((message.get("button") or {}).get("text")) or "").strip()

    if message_type in {"image", "video", "document"}:
        media = message.get(message_type) or {}
        caption = str(media.get("caption") or "").strip()
        return caption or f"[{message_type}]"

    if message_type == "audio":
        return "[audio]"

    if message_type == "location":
        location = message.get("location") or {}
        name = str(location.get("name") or "").strip()
        return name or "[location]"

    return ""


def build_whatsapp_message(envelope: WhatsAppEnvelope) -> Message:
    """Convert a normalized WhatsApp message into the runtime message model."""

    return Message(
        content=envelope.content,
        user_id=envelope.sender_id,
        channel="whatsapp",
        metadata={
            "message_id": envelope.message_id,
            "phone_number_id": envelope.phone_number_id,
            "display_phone_number": envelope.display_phone_number,
            "sender_name": envelope.sender_name,
        },
    )


def format_task_for_whatsapp(task: Task) -> str:
    """Format a task result into a compact WhatsApp-friendly reply."""

    return format_task_for_personal_wechat(task)


async def send_whatsapp_text_reply(
    envelope: WhatsAppEnvelope,
    reply_text: str,
    settings: Optional[Settings] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Dict[str, Any]]:
    """Send a text reply through the WhatsApp Cloud API when configured."""

    resolved_settings = settings or get_settings()
    if not (
        resolved_settings.whatsapp_enabled
        and resolved_settings.whatsapp_reply_via_cloud_api
        and resolved_settings.whatsapp_access_token
    ):
        return None

    phone_number_id = envelope.phone_number_id or resolved_settings.whatsapp_phone_number_id
    if not phone_number_id:
        return None

    graph_version = (resolved_settings.whatsapp_graph_api_version or "v23.0").strip()
    base_url = resolved_settings.whatsapp_graph_base_url.rstrip("/")
    url = f"{base_url}/{graph_version}/{phone_number_id}/messages"

    payload: Dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": envelope.sender_id,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": reply_text,
        },
    }
    if envelope.message_id:
        payload["context"] = {"message_id": envelope.message_id}

    headers = {
        "Authorization": f"Bearer {resolved_settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=resolved_settings.default_timeout)
        close_client = True

    try:
        response = await client.post(url, json=payload, headers=headers)
        try:
            body = response.json()
        except Exception:
            body = response.text
        return {"status_code": response.status_code, "body": body}
    finally:
        if close_client:
            await client.aclose()

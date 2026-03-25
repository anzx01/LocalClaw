"""Weixin webhook channel helpers."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional

import httpx

from localclaw.channels.wechat_personal import format_task_for_personal_wechat
from localclaw.config.settings import Settings, get_settings
from localclaw.core.models import Message, Task


logger = logging.getLogger(__name__)

DEFAULT_WEIXIN_BASE_URL = "https://ilinkai.weixin.qq.com"
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
MSG_ITEM_TEXT = 1


@dataclass
class WeixinEnvelope:
    """Normalized incoming Weixin webhook payload."""

    content: str
    sender_id: str
    account_id: str = ""
    message_id: str = ""
    timestamp_ms: Optional[int] = None
    timestamp: Optional[str] = None
    context_token: str = ""
    raw_payload: Optional[Dict[str, Any]] = None


_context_token_cache: Dict[str, str] = {}
_context_token_lock = threading.Lock()


def _context_cache_key(account_id: str, sender_id: str) -> str:
    """Build a deterministic key for context-token storage."""

    account = account_id.strip() or "default"
    sender = sender_id.strip()
    return f"{account}:{sender}"


def cache_weixin_context_token(account_id: str, sender_id: str, context_token: str) -> None:
    """Cache the latest context token for a sender."""

    token = context_token.strip()
    sender = sender_id.strip()
    if not token or not sender:
        return
    with _context_token_lock:
        _context_token_cache[_context_cache_key(account_id, sender)] = token


def get_cached_weixin_context_token(account_id: str, sender_id: str) -> Optional[str]:
    """Get a cached context token for a sender."""

    sender = sender_id.strip()
    if not sender:
        return None
    with _context_token_lock:
        return _context_token_cache.get(_context_cache_key(account_id, sender))


def parse_weixin_allowed_user_ids(raw_csv: Optional[str]) -> set[str]:
    """Parse an optional CSV allowlist into a set."""

    return {
        value.strip()
        for value in (raw_csv or "").split(",")
        if isinstance(value, str) and value.strip()
    }


def is_allowed_weixin_sender(raw_csv: Optional[str], sender_id: str) -> bool:
    """Return whether the sender passes the configured allowlist."""

    allowed = parse_weixin_allowed_user_ids(raw_csv)
    if not allowed:
        return True
    return sender_id in allowed


def _pick_string(data: Dict[str, Any], *keys: str) -> str:
    """Pick the first non-empty string value for provided keys."""

    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return ""


def _as_dict(value: Any) -> Dict[str, Any]:
    """Return a dict value or an empty dict."""

    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Dict[str, Any]]:
    """Return a list of dict entries."""

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _coerce_flexible_id(value: Any) -> str:
    """Coerce a potentially flexible message id into a string."""

    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text
    if isinstance(value, dict):
        for key in ("id", "value", "message_id", "messageId"):
            nested = value.get(key)
            if nested is None:
                continue
            text = str(nested).strip()
            if text:
                return text
    return ""


def summarize_weixin_item(item: Dict[str, Any]) -> Optional[str]:
    """Summarize a single Weixin item entry into user-visible text."""

    try:
        item_type = int(item.get("type") or 0)
    except (TypeError, ValueError):
        item_type = 0

    if item_type == 1:
        text = _pick_string(_as_dict(item.get("text_item")), "text")
        if not text:
            return None
        reference = _pick_string(_as_dict(item.get("ref_msg")), "title")
        if reference:
            return f"[quoted: {reference}]\n{text}"
        return text

    if item_type == 2:
        return "[image]"

    if item_type == 3:
        voice_text = _pick_string(_as_dict(item.get("voice_item")), "text")
        return voice_text or "[voice]"

    if item_type == 4:
        file_name = _pick_string(_as_dict(item.get("file_item")), "file_name", "fileName")
        if file_name:
            return f"[file: {file_name}]"
        return "[file]"

    if item_type == 5:
        return "[video]"

    return None


def summarize_weixin_items(items: Iterable[Dict[str, Any]]) -> str:
    """Summarize a list of Weixin item entries."""

    parts = [part for part in (summarize_weixin_item(item) for item in items) if part]
    return "\n".join(parts)


def normalize_weixin_webhook_payload(payload: Dict[str, Any]) -> WeixinEnvelope:
    """Extract the inbound Weixin message from webhook payload."""

    nested = _as_dict(payload.get("message"))

    sender_id = _pick_string(payload, "from_user_id") or _pick_string(nested, "from_user_id")
    account_id = _pick_string(payload, "account_id")
    direct_text = _pick_string(payload, "text")

    top_items = _as_list(payload.get("item_list"))
    nested_items = _as_list(nested.get("item_list"))
    content = direct_text or summarize_weixin_items(top_items) or summarize_weixin_items(nested_items)

    message_id = _coerce_flexible_id(payload.get("message_id")) or _coerce_flexible_id(
        nested.get("message_id")
    )
    timestamp_ms_raw = payload.get("timestamp_ms", nested.get("create_time_ms"))
    timestamp_ms: Optional[int]
    if isinstance(timestamp_ms_raw, int):
        timestamp_ms = timestamp_ms_raw
    elif isinstance(timestamp_ms_raw, str) and timestamp_ms_raw.strip().isdigit():
        timestamp_ms = int(timestamp_ms_raw.strip())
    else:
        timestamp_ms = None

    timestamp = _pick_string(payload, "timestamp") or None
    context_token = _pick_string(payload, "context_token") or _pick_string(nested, "context_token")

    if not sender_id:
        raise ValueError("Missing Weixin sender identifier")
    if not content:
        raise ValueError("Unsupported or empty Weixin message payload")

    if context_token:
        cache_weixin_context_token(account_id, sender_id, context_token)

    return WeixinEnvelope(
        content=content,
        sender_id=sender_id,
        account_id=account_id,
        message_id=message_id,
        timestamp_ms=timestamp_ms,
        timestamp=timestamp,
        context_token=context_token,
        raw_payload=payload,
    )


def build_weixin_message(envelope: WeixinEnvelope) -> Message:
    """Convert a normalized Weixin payload to a runtime message."""

    return Message(
        content=envelope.content,
        user_id=envelope.sender_id,
        channel="weixin",
        metadata={
            "account_id": envelope.account_id,
            "message_id": envelope.message_id,
            "timestamp_ms": envelope.timestamp_ms,
            "timestamp": envelope.timestamp,
            "context_token": envelope.context_token,
        },
    )


def format_task_for_weixin(task: Task) -> str:
    """Format a task result for Weixin replies."""

    return format_task_for_personal_wechat(task)


def provided_weixin_webhook_token(headers: Mapping[str, str]) -> str:
    """Extract a provided webhook token from headers."""

    header_token = str(headers.get("x-weixin-webhook-token") or "").strip()
    if header_token:
        return header_token

    authorization = str(headers.get("authorization") or headers.get("Authorization") or "").strip()
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return ""


def is_valid_weixin_webhook_token(expected_token: Optional[str], provided_token: Optional[str]) -> bool:
    """Validate webhook token for Weixin inbound requests."""

    if not expected_token:
        return True
    return bool(provided_token) and provided_token == expected_token


def resolve_weixin_context_token(
    envelope: WeixinEnvelope,
    context_token_override: Optional[str] = None,
) -> Optional[str]:
    """Resolve an outbound context token from override, envelope, or cache."""

    explicit = (context_token_override or "").strip()
    if explicit:
        cache_weixin_context_token(envelope.account_id, envelope.sender_id, explicit)
        return explicit

    embedded = (envelope.context_token or "").strip()
    if embedded:
        cache_weixin_context_token(envelope.account_id, envelope.sender_id, embedded)
        return embedded

    return get_cached_weixin_context_token(envelope.account_id, envelope.sender_id)


async def send_weixin_text_reply(
    envelope: WeixinEnvelope,
    reply_text: str,
    settings: Optional[Settings] = None,
    client: Optional[httpx.AsyncClient] = None,
    context_token_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Send a text reply through the Weixin ilink API when configured."""

    resolved_settings = settings or get_settings()
    if not (
        resolved_settings.weixin_enabled
        and resolved_settings.weixin_reply_via_api
        and resolved_settings.weixin_bot_token
    ):
        return None

    context_token = resolve_weixin_context_token(
        envelope=envelope,
        context_token_override=context_token_override,
    )
    if not context_token:
        return {"status_code": 0, "body": {"error": "missing_context_token"}}

    base_url = (resolved_settings.weixin_base_url or DEFAULT_WEIXIN_BASE_URL).rstrip("/")
    url = f"{base_url}/ilink/bot/sendmessage"
    payload: Dict[str, Any] = {
        "msg": {
            "from_user_id": "",
            "to_user_id": envelope.sender_id,
            "client_id": f"localclaw-weixin:{uuid.uuid4()}",
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": [
                {
                    "type": MSG_ITEM_TEXT,
                    "text_item": {"text": reply_text},
                }
            ],
            "context_token": context_token,
        },
        "base_info": {"channel_version": "localclaw"},
    }
    headers = {
        "Authorization": f"Bearer {resolved_settings.weixin_bot_token}",
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

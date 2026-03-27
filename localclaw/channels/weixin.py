"""Weixin webhook channel helpers."""

from __future__ import annotations

import json
import logging
import threading
import uuid
import base64
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import quote

import httpx

from localclaw.channels.wechat_personal import format_task_for_personal_wechat
from localclaw.channels.result_formatter import format_task_for_chat
from localclaw.config.settings import Settings, get_settings
from localclaw.core.models import Message, Task


logger = logging.getLogger(__name__)

DEFAULT_WEIXIN_BASE_URL = "https://ilinkai.weixin.qq.com"
WEIXIN_QR_LOGIN_BOT_TYPE = "3"
WEIXIN_QR_CLIENT_VERSION = "1"
WEIXIN_QR_SESSION_TTL = timedelta(minutes=15)
WEIXIN_GET_UPDATES_DEFAULT_TIMEOUT_MS = 35_000
WEIXIN_SESSION_EXPIRED_ERRCODE = -14
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


@dataclass
class StoredWeixinAccount:
    """Persisted Weixin account credentials and context tokens."""

    token: str = ""
    base_url: str = DEFAULT_WEIXIN_BASE_URL
    remote_account_id: str = ""
    user_id: str = ""
    saved_at: str = ""
    context_tokens: Dict[str, str] = field(default_factory=dict)


@dataclass
class WeixinQrLoginSession:
    """In-memory Weixin QR login session."""

    session_id: str
    account_id: str
    base_url: str
    status: str
    qrcode: str
    qrcode_img_content: str
    created_at: str
    updated_at: str
    error: str = ""
    connected_account: Optional[StoredWeixinAccount] = None


_context_token_cache: Dict[str, str] = {}
_context_token_lock = threading.Lock()
_weixin_login_sessions: Dict[str, WeixinQrLoginSession] = {}
_weixin_login_sessions_lock = threading.Lock()


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return _utcnow().isoformat()


def _build_weixin_post_headers(bot_token: str) -> Dict[str, str]:
    """Build Weixin API POST headers compatible with native clients."""

    token = str(bot_token or "").strip()
    if not token:
        raise ValueError("Missing Weixin bot token")

    random_bytes = uuid.uuid4().bytes
    wechat_uin = int.from_bytes(random_bytes[:4], byteorder="big", signed=False)
    encoded_uin = base64.b64encode(str(wechat_uin).encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Bearer {token}",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": encoded_uin,
        "Content-Type": "application/json",
    }


def _normalize_weixin_qr_status(raw_status: Any) -> str:
    """Normalize Weixin QR status values across known upstream variants."""

    status = str(raw_status or "").strip().lower()
    aliases = {
        "0": "wait",
        "wait": "wait",
        "waiting": "wait",
        "pending": "wait",
        "1": "scaned",
        "scan": "scaned",
        "scaned": "scaned",
        "scanned": "scaned",
        "2": "confirmed",
        "confirmed": "confirmed",
        "success": "confirmed",
        "ok": "confirmed",
        "3": "expired",
        "expired": "expired",
        "timeout": "expired",
        "error": "error",
        "failed": "error",
    }
    return aliases.get(status, status)


def normalize_weixin_account_id(account_id: str) -> str:
    """Normalize an optional account id into a local storage key."""

    normalized = str(account_id or "").strip()
    return normalized or "default"


def _sanitize_weixin_account_key(value: str) -> str:
    """Create a filesystem-safe account key."""

    safe = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "_"
        for character in normalize_weixin_account_id(value)
    )
    return safe or "default"


def _weixin_state_root(settings: Settings) -> Path:
    """Resolve the LocalClaw data directory used for Weixin state."""

    return settings.data_dir / "weixin"


def _weixin_account_file_path(settings: Settings, account_id: str = "") -> Path:
    """Resolve the persisted account-state path for a local Weixin account."""

    return (
        _weixin_state_root(settings)
        / "accounts"
        / f"{_sanitize_weixin_account_key(account_id)}.json"
    )


def _weixin_sync_file_path(settings: Settings, account_id: str = "") -> Path:
    """Resolve the persisted sync-cursor path for Weixin polling."""

    return (
        _weixin_state_root(settings)
        / "sync"
        / f"{_sanitize_weixin_account_key(account_id)}.json"
    )


def _context_cache_key(account_id: str, sender_id: str) -> str:
    """Build a deterministic key for context-token storage."""

    account = normalize_weixin_account_id(account_id)
    sender = sender_id.strip()
    return f"{account}:{sender}"


def load_stored_weixin_account(
    settings: Optional[Settings] = None,
    account_id: str = "",
) -> Optional[StoredWeixinAccount]:
    """Load a persisted Weixin account from LocalClaw data storage."""

    resolved_settings = settings or get_settings()
    normalized_account_id = normalize_weixin_account_id(account_id)
    path = _weixin_account_file_path(resolved_settings, normalized_account_id)
    if not path.exists() and normalized_account_id != "default":
        path = _weixin_account_file_path(resolved_settings, "default")
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read stored Weixin account state from %s: %s", path, exc)
        return None

    if not isinstance(payload, dict):
        return None

    context_tokens = payload.get("context_tokens")
    if not isinstance(context_tokens, dict):
        context_tokens = {}

    return StoredWeixinAccount(
        token=str(payload.get("token") or "").strip(),
        base_url=str(payload.get("base_url") or DEFAULT_WEIXIN_BASE_URL).strip()
        or DEFAULT_WEIXIN_BASE_URL,
        remote_account_id=str(payload.get("remote_account_id") or "").strip(),
        user_id=str(payload.get("user_id") or "").strip(),
        saved_at=str(payload.get("saved_at") or "").strip(),
        context_tokens={
            str(key).strip(): str(value).strip()
            for key, value in context_tokens.items()
            if str(key).strip() and str(value).strip()
        },
    )


def save_stored_weixin_account(
    account: StoredWeixinAccount,
    settings: Optional[Settings] = None,
    account_id: str = "",
) -> Path:
    """Persist a Weixin account into LocalClaw data storage."""

    resolved_settings = settings or get_settings()
    path = _weixin_account_file_path(resolved_settings, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "token": account.token.strip(),
        "base_url": (account.base_url or DEFAULT_WEIXIN_BASE_URL).strip() or DEFAULT_WEIXIN_BASE_URL,
        "remote_account_id": account.remote_account_id.strip(),
        "user_id": account.user_id.strip(),
        "saved_at": account.saved_at.strip() or _utcnow_iso(),
        "context_tokens": {
            str(key).strip(): str(value).strip()
            for key, value in (account.context_tokens or {}).items()
            if str(key).strip() and str(value).strip()
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return path


def get_weixin_connected_account(
    settings: Optional[Settings] = None,
    account_id: str = "",
) -> Optional[StoredWeixinAccount]:
    """Return the persisted Weixin account, if LocalClaw has one."""

    return load_stored_weixin_account(settings=settings, account_id=account_id)


def list_stored_weixin_account_ids(settings: Optional[Settings] = None) -> list[str]:
    """List account ids that have persisted Weixin login state."""

    resolved_settings = settings or get_settings()
    root = _weixin_state_root(resolved_settings) / "accounts"
    if not root.exists():
        return []

    account_ids: list[str] = []
    for entry in root.glob("*.json"):
        account_id = normalize_weixin_account_id(entry.stem)
        if account_id:
            account_ids.append(account_id)
    return sorted(set(account_ids))


def load_weixin_updates_cursor(
    account_id: str = "",
    settings: Optional[Settings] = None,
) -> str:
    """Load persisted getupdates cursor for an account."""

    resolved_settings = settings or get_settings()
    path = _weixin_sync_file_path(resolved_settings, account_id)
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("get_updates_buf") or "").strip()
    if isinstance(payload, str):
        return payload.strip()
    return ""


def save_weixin_updates_cursor(
    account_id: str,
    get_updates_buf: str,
    settings: Optional[Settings] = None,
) -> Path:
    """Persist getupdates cursor for an account."""

    resolved_settings = settings or get_settings()
    path = _weixin_sync_file_path(resolved_settings, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"get_updates_buf": str(get_updates_buf or "").strip()}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return path


def resolve_weixin_reply_configuration(
    settings: Optional[Settings] = None,
    account_id: str = "",
) -> Dict[str, Any]:
    """Resolve outbound Weixin reply configuration from env and stored login state."""

    resolved_settings = settings or get_settings()
    stored_account = load_stored_weixin_account(settings=resolved_settings, account_id=account_id)
    stored_token = (stored_account.token if stored_account else "").strip()
    env_token = (resolved_settings.weixin_bot_token or "").strip()
    bot_token = env_token or stored_token

    if env_token:
        base_url = (resolved_settings.weixin_base_url or DEFAULT_WEIXIN_BASE_URL).strip()
        bot_token_source = "env"
    elif stored_account and stored_account.base_url.strip():
        base_url = stored_account.base_url.strip()
        bot_token_source = "stored_login"
    else:
        base_url = (resolved_settings.weixin_base_url or DEFAULT_WEIXIN_BASE_URL).strip()
        bot_token_source = "missing"

    has_stored_login = bool(stored_token)
    reply_enabled = bool(resolved_settings.weixin_reply_via_api or has_stored_login)

    return {
        "reply_enabled": reply_enabled,
        "bot_token": bot_token,
        "bot_token_source": bot_token_source,
        "has_stored_login": has_stored_login,
        "stored_account": stored_account,
        "base_url": base_url.rstrip("/") or DEFAULT_WEIXIN_BASE_URL,
    }


def cache_weixin_context_token(account_id: str, sender_id: str, context_token: str) -> None:
    """Cache the latest context token for a sender."""

    token = context_token.strip()
    sender = sender_id.strip()
    if not token or not sender:
        return
    with _context_token_lock:
        _context_token_cache[_context_cache_key(account_id, sender)] = token


def get_cached_weixin_context_token(
    account_id: str,
    sender_id: str,
    settings: Optional[Settings] = None,
) -> Optional[str]:
    """Get a cached or persisted context token for a sender."""

    sender = sender_id.strip()
    if not sender:
        return None

    cache_key = _context_cache_key(account_id, sender)
    with _context_token_lock:
        cached = _context_token_cache.get(cache_key)
    if cached:
        return cached

    stored_account = load_stored_weixin_account(settings=settings, account_id=account_id)
    if not stored_account:
        return None

    token = str(stored_account.context_tokens.get(sender) or "").strip()
    if not token:
        return None

    cache_weixin_context_token(account_id, sender, token)
    return token


def persist_weixin_context_token(
    account_id: str,
    sender_id: str,
    context_token: str,
    settings: Optional[Settings] = None,
) -> None:
    """Persist the latest Weixin context token for a sender."""

    token = str(context_token or "").strip()
    sender = str(sender_id or "").strip()
    if not token or not sender:
        return

    resolved_settings = settings or get_settings()
    cache_weixin_context_token(account_id, sender, token)

    stored_account = load_stored_weixin_account(settings=resolved_settings, account_id=account_id)
    if stored_account is None:
        reply_config = resolve_weixin_reply_configuration(
            settings=resolved_settings,
            account_id=account_id,
        )
        stored_account = StoredWeixinAccount(
            base_url=reply_config["base_url"],
            token=str(reply_config["bot_token"] or "").strip(),
        )

    stored_account.context_tokens[sender] = token
    if not stored_account.base_url.strip():
        stored_account.base_url = resolve_weixin_reply_configuration(
            settings=resolved_settings,
            account_id=account_id,
        )["base_url"]
    if not stored_account.saved_at.strip():
        stored_account.saved_at = _utcnow_iso()

    save_stored_weixin_account(
        stored_account,
        settings=resolved_settings,
        account_id=account_id,
    )


def _prune_weixin_login_sessions() -> None:
    """Drop expired in-memory QR login sessions."""

    now = _utcnow()
    with _weixin_login_sessions_lock:
        expired_session_ids = []
        for session_id, session in _weixin_login_sessions.items():
            try:
                updated_at = datetime.fromisoformat(session.updated_at)
            except ValueError:
                expired_session_ids.append(session_id)
                continue
            if now - updated_at > WEIXIN_QR_SESSION_TTL:
                expired_session_ids.append(session_id)

        for session_id in expired_session_ids:
            _weixin_login_sessions.pop(session_id, None)


def _store_weixin_login_session(session: WeixinQrLoginSession) -> WeixinQrLoginSession:
    """Store a QR login session and return a detached snapshot."""

    _prune_weixin_login_sessions()
    with _weixin_login_sessions_lock:
        _weixin_login_sessions[session.session_id] = session
        return deepcopy(session)


def get_weixin_qr_login_session(session_id: str) -> Optional[WeixinQrLoginSession]:
    """Read a detached QR login session snapshot."""

    _prune_weixin_login_sessions()
    with _weixin_login_sessions_lock:
        session = _weixin_login_sessions.get(session_id)
        return deepcopy(session) if session else None


def _save_weixin_qr_login_session_status(
    session: WeixinQrLoginSession,
    *,
    status: str,
    error: str = "",
    connected_account: Optional[StoredWeixinAccount] = None,
) -> WeixinQrLoginSession:
    """Update and store a QR login session status."""

    session.status = status
    session.error = error.strip()
    session.updated_at = _utcnow_iso()
    if connected_account is not None:
        session.connected_account = connected_account
    return _store_weixin_login_session(session)


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
        return str(value).strip()
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


def normalize_weixin_polled_message(
    message: Dict[str, Any],
    account_id: str = "",
) -> Optional[WeixinEnvelope]:
    """Normalize a message returned by ilink/bot/getupdates into an envelope."""

    if not isinstance(message, dict):
        return None

    sender_id = _pick_string(message, "from_user_id")
    direct_text = _pick_string(message, "text")
    items = _as_list(message.get("item_list"))
    content = direct_text or summarize_weixin_items(items)
    if not sender_id or not content:
        return None

    message_id = _coerce_flexible_id(message.get("message_id"))
    timestamp_ms_raw = message.get("create_time_ms", message.get("timestamp_ms"))
    timestamp_ms: Optional[int]
    if isinstance(timestamp_ms_raw, int):
        timestamp_ms = timestamp_ms_raw
    elif isinstance(timestamp_ms_raw, str) and timestamp_ms_raw.strip().isdigit():
        timestamp_ms = int(timestamp_ms_raw.strip())
    else:
        timestamp_ms = None

    context_token = _pick_string(message, "context_token")
    if context_token:
        cache_weixin_context_token(account_id, sender_id, context_token)

    return WeixinEnvelope(
        content=content,
        sender_id=sender_id,
        account_id=account_id,
        message_id=message_id,
        timestamp_ms=timestamp_ms,
        context_token=context_token,
        raw_payload={"source": "poll", "message": message},
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

    # Prefer the richer chat formatter so HTTP/weather/tool outputs
    # are rendered into user-facing text instead of generic success strings.
    text = format_task_for_chat(task)
    if isinstance(text, str) and text.strip():
        return text
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


async def fetch_weixin_login_qrcode(
    settings: Optional[Settings] = None,
    client: Optional[httpx.AsyncClient] = None,
    base_url: Optional[str] = None,
) -> Dict[str, str]:
    """Request a fresh Weixin QR login code."""

    resolved_settings = settings or get_settings()
    resolved_base_url = (base_url or resolved_settings.weixin_base_url or DEFAULT_WEIXIN_BASE_URL).rstrip("/")
    url = f"{resolved_base_url}/ilink/bot/get_bot_qrcode?bot_type={WEIXIN_QR_LOGIN_BOT_TYPE}"

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=resolved_settings.default_timeout)
        close_client = True

    try:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    finally:
        if close_client:
            await client.aclose()

    qrcode = str(payload.get("qrcode") or "").strip()
    qrcode_img_content = str(payload.get("qrcode_img_content") or "").strip()
    if not qrcode or not qrcode_img_content:
        raise ValueError("Weixin QR login response was missing qrcode or qrcode_img_content")

    return {
        "qrcode": qrcode,
        "qrcode_img_content": qrcode_img_content,
        "base_url": resolved_base_url,
    }


async def fetch_weixin_login_status(
    qrcode: str,
    settings: Optional[Settings] = None,
    client: Optional[httpx.AsyncClient] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Poll Weixin QR login status."""

    resolved_settings = settings or get_settings()
    resolved_base_url = (base_url or resolved_settings.weixin_base_url or DEFAULT_WEIXIN_BASE_URL).rstrip("/")
    url = f"{resolved_base_url}/ilink/bot/get_qrcode_status?qrcode={quote(qrcode, safe='')}"

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=resolved_settings.default_timeout)
        close_client = True

    try:
        response = await client.get(
            url,
            headers={"iLink-App-ClientVersion": WEIXIN_QR_CLIENT_VERSION},
        )
        response.raise_for_status()
        return response.json()
    finally:
        if close_client:
            await client.aclose()


async def fetch_weixin_updates(
    account: StoredWeixinAccount,
    *,
    get_updates_buf: str = "",
    settings: Optional[Settings] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Call ilink/bot/getupdates for a stored Weixin account."""

    token = str(account.token or "").strip()
    if not token:
        raise ValueError("Missing Weixin bot token for getupdates")

    resolved_settings = settings or get_settings()
    base_url = (account.base_url or resolved_settings.weixin_base_url or DEFAULT_WEIXIN_BASE_URL).rstrip("/")
    url = f"{base_url}/ilink/bot/getupdates"
    timeout_seconds = (
        max(float(timeout_ms or 0) / 1000.0, 1.0)
        if timeout_ms is not None
        else resolved_settings.default_timeout
    )
    payload = {
        "get_updates_buf": str(get_updates_buf or "").strip(),
        "base_info": {"channel_version": "localclaw"},
    }
    headers = _build_weixin_post_headers(token)

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_seconds)
        close_client = True

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Unexpected Weixin getupdates response shape")
        return body
    finally:
        if close_client:
            await client.aclose()


async def start_weixin_qr_login(
    settings: Optional[Settings] = None,
    account_id: str = "",
    client: Optional[httpx.AsyncClient] = None,
) -> WeixinQrLoginSession:
    """Create a new Weixin QR login session."""

    resolved_settings = settings or get_settings()
    normalized_account_id = normalize_weixin_account_id(account_id)
    qr = await fetch_weixin_login_qrcode(
        settings=resolved_settings,
        client=client,
    )

    session = WeixinQrLoginSession(
        session_id=uuid.uuid4().hex,
        account_id=normalized_account_id,
        base_url=qr["base_url"],
        status="wait",
        qrcode=qr["qrcode"],
        qrcode_img_content=qr["qrcode_img_content"],
        created_at=_utcnow_iso(),
        updated_at=_utcnow_iso(),
        connected_account=load_stored_weixin_account(
            settings=resolved_settings,
            account_id=normalized_account_id,
        ),
    )
    return _store_weixin_login_session(session)


async def poll_weixin_qr_login(
    session_id: str,
    settings: Optional[Settings] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> WeixinQrLoginSession:
    """Poll an existing Weixin QR login session."""

    session = get_weixin_qr_login_session(session_id)
    if session is None:
        raise KeyError(session_id)

    if session.status in {"confirmed", "expired", "error"}:
        return session

    resolved_settings = settings or get_settings()

    try:
        payload = await fetch_weixin_login_status(
            qrcode=session.qrcode,
            settings=resolved_settings,
            client=client,
            base_url=session.base_url,
        )
    except Exception as exc:
        logger.warning("Failed to poll Weixin QR login status for %s: %s", session_id, exc)
        return _save_weixin_qr_login_session_status(
            session,
            status="error",
            error=str(exc),
        )

    status = _normalize_weixin_qr_status(payload.get("status"))
    if status in {"wait", "scaned"}:
        return _save_weixin_qr_login_session_status(session, status=status or "wait")

    if status == "expired":
        return _save_weixin_qr_login_session_status(
            session,
            status="expired",
            error="QR code expired. Start a new scan to continue.",
        )

    if status == "confirmed":
        bot_token = str(payload.get("bot_token") or payload.get("token") or "").strip()
        remote_account_id = str(
            payload.get("ilink_bot_id")
            or payload.get("bot_id")
            or payload.get("botid")
            or ""
        ).strip()
        if not bot_token or not remote_account_id:
            return _save_weixin_qr_login_session_status(
                session,
                status="error",
                error="Login was confirmed, but the server did not return a complete bot credential set.",
            )

        existing_account = load_stored_weixin_account(
            settings=resolved_settings,
            account_id=session.account_id,
        )
        connected_account = StoredWeixinAccount(
            token=bot_token,
            base_url=(
                str(payload.get("baseurl") or payload.get("base_url") or session.base_url).strip()
                or session.base_url
            ),
            remote_account_id=remote_account_id,
            user_id=str(payload.get("ilink_user_id") or payload.get("user_id") or "").strip(),
            saved_at=_utcnow_iso(),
            context_tokens=(
                deepcopy(existing_account.context_tokens)
                if existing_account is not None
                else {}
            ),
        )
        save_stored_weixin_account(
            connected_account,
            settings=resolved_settings,
            account_id=session.account_id,
        )
        return _save_weixin_qr_login_session_status(
            session,
            status="confirmed",
            connected_account=connected_account,
        )

    return _save_weixin_qr_login_session_status(
        session,
        status="error",
        error=(
            "Unexpected Weixin QR login status: "
            f"{status or 'missing'}; payload={json.dumps(payload, ensure_ascii=True)}"
        ),
    )


async def send_weixin_text_reply(
    envelope: WeixinEnvelope,
    reply_text: str,
    settings: Optional[Settings] = None,
    client: Optional[httpx.AsyncClient] = None,
    context_token_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Send a text reply through the Weixin ilink API when configured."""

    resolved_settings = settings or get_settings()
    reply_config = resolve_weixin_reply_configuration(
        settings=resolved_settings,
        account_id=envelope.account_id,
    )
    if not (
        resolved_settings.weixin_enabled
        and reply_config["reply_enabled"]
        and reply_config["bot_token"]
    ):
        return None

    context_token = resolve_weixin_context_token(
        envelope=envelope,
        context_token_override=context_token_override,
    )
    if not context_token:
        return {"status_code": 0, "body": {"error": "missing_context_token"}}

    url = f"{reply_config['base_url']}/ilink/bot/sendmessage"
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
        **_build_weixin_post_headers(reply_config["bot_token"]),
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

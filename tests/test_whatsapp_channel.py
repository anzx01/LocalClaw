"""Tests for the WhatsApp Cloud API channel."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from localclaw.channels.whatsapp import (
    build_whatsapp_message,
    is_valid_whatsapp_signature,
    is_valid_whatsapp_verify_token,
    normalize_whatsapp_webhook_payload,
    send_whatsapp_text_reply,
)
from localclaw.config.settings import Settings
from localclaw.core.models import ExecutionResult, Message, Task, TaskState


def _sample_whatsapp_payload() -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {
                                "display_phone_number": "15550001111",
                                "phone_number_id": "1234567890",
                            },
                            "contacts": [{"profile": {"name": "Alice"}, "wa_id": "15551234567"}],
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "id": "wamid.abc123",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": "鎵ц鍛戒护 git status"},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


def test_normalize_whatsapp_webhook_payload():
    """Inbound webhook payloads should normalize to a runtime envelope."""

    envelope = normalize_whatsapp_webhook_payload(_sample_whatsapp_payload())

    assert envelope is not None
    assert envelope.content == "鎵ц鍛戒护 git status"
    assert envelope.sender_id == "15551234567"
    assert envelope.message_id == "wamid.abc123"
    assert envelope.phone_number_id == "1234567890"
    assert envelope.sender_name == "Alice"


def test_build_whatsapp_message():
    """The normalized envelope should become a runtime message."""

    envelope = normalize_whatsapp_webhook_payload(_sample_whatsapp_payload())
    assert envelope is not None

    message = build_whatsapp_message(envelope)

    assert isinstance(message, Message)
    assert message.channel == "whatsapp"
    assert message.user_id == "15551234567"
    assert message.metadata["message_id"] == "wamid.abc123"


def test_is_valid_whatsapp_verify_token():
    """Webhook verification token matching should be exact."""

    assert is_valid_whatsapp_verify_token("secret", "secret") is True
    assert is_valid_whatsapp_verify_token("secret", "wrong") is False


def test_is_valid_whatsapp_signature():
    """Webhook signature should be verified with the shared app secret."""

    body = json.dumps(_sample_whatsapp_payload()).encode("utf-8")
    secret = "app-secret"
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert is_valid_whatsapp_signature(secret, body, f"sha256={expected}") is True
    assert is_valid_whatsapp_signature(secret, body, "sha256=bad") is False


@pytest.mark.asyncio
async def test_send_whatsapp_text_reply():
    """Replies should be posted to the Graph API when configured."""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"messages": [{"id": "wamid.reply1"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        whatsapp_enabled=True,
        whatsapp_reply_via_cloud_api=True,
        whatsapp_access_token="wa-token",
        whatsapp_phone_number_id="1234567890",
        whatsapp_graph_api_version="v23.0",
    )
    envelope = normalize_whatsapp_webhook_payload(_sample_whatsapp_payload())
    assert envelope is not None

    result = await send_whatsapp_text_reply(
        envelope=envelope,
        reply_text="done",
        settings=settings,
        client=client,
    )
    await client.aclose()

    assert result is not None
    assert captured["url"] == "https://graph.facebook.com/v23.0/1234567890/messages"
    assert "wa-token" in captured["headers"]["authorization"]
    assert '"messaging_product":"whatsapp"' in captured["body"]
    assert '"to":"15551234567"' in captured["body"]


def test_whatsapp_webhook_routes(monkeypatch):
    """Webhook verification and inbound processing should work end-to-end."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        whatsapp_enabled=True,
        whatsapp_verify_token="verify-me",
        whatsapp_app_secret="app-secret",
        whatsapp_reply_via_cloud_api=False,
    )

    async def fake_process_message(message: Message) -> Task:
        task = Task(
            message=message,
            user_id=message.user_id,
            channel=message.channel,
            state=TaskState.COMPLETED,
        )
        task.result = ExecutionResult.success(
            data={"step1": {"command": "git status", "stdout": "On branch main", "exit_code": 0}}
        )
        return task

    fake_engine = AsyncMock()
    fake_engine.process_message.side_effect = fake_process_message

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: fake_engine)

    app = web_channel.create_app()
    payload = _sample_whatsapp_payload()
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(
        settings.whatsapp_app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    with TestClient(app) as client:
        verify_response = client.get(
            "/api/channels/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-123",
            },
        )
        webhook_response = client.post(
            "/api/channels/whatsapp/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        )

    assert verify_response.status_code == 200
    assert verify_response.text == "challenge-123"
    assert webhook_response.status_code == 200
    data = webhook_response.json()
    assert data["accepted"] is True
    assert data["reply"]["text"].startswith("命令: git status")


def test_whatsapp_test_route_live(monkeypatch):
    """The manual test route should send through Cloud API when all credentials exist."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        whatsapp_enabled=True,
        whatsapp_reply_via_cloud_api=True,
        whatsapp_access_token="wa-token",
        whatsapp_phone_number_id="1234567890",
    )

    async def fake_send(envelope, reply_text, settings):
        assert envelope.sender_id == "15551234567"
        assert envelope.phone_number_id == "1234567890"
        assert reply_text == "ping"
        return {"status_code": 200, "body": {"messages": [{"id": "wamid.reply1"}]}}

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "send_whatsapp_text_reply", fake_send)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/channels/whatsapp/test",
            json={"recipient": "15551234567", "text": "ping"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["mode"] == "live"
    assert data["delivery"]["status_code"] == 200


def test_whatsapp_test_route_dry_run(monkeypatch):
    """The manual test route should return a dry run preview when config is incomplete."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        whatsapp_enabled=True,
        whatsapp_reply_via_cloud_api=False,
    )

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/channels/whatsapp/test",
            json={"recipient": "15551234567", "text": "ping"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "dry_run"
    assert "LOCALCLAW_WHATSAPP_REPLY_VIA_CLOUD_API" in data["missing"]

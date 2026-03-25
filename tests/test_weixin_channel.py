"""Tests for the Weixin webhook channel."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from localclaw.channels.weixin import (
    build_weixin_message,
    is_valid_weixin_webhook_token,
    normalize_weixin_webhook_payload,
    provided_weixin_webhook_token,
    send_weixin_text_reply,
)
from localclaw.config.settings import Settings
from localclaw.core.models import ExecutionResult, Message, Task, TaskState


def _sample_weixin_payload() -> dict:
    return {
        "account_id": "main",
        "from_user_id": "alice@im.wechat",
        "message_id": 42,
        "context_token": "ctx-42",
        "timestamp_ms": 1710000000,
        "item_list": [
            {
                "type": 1,
                "text_item": {"text": "执行命令 git status"},
            }
        ],
    }


def _sample_nested_payload() -> dict:
    return {
        "account_id": "ops",
        "message": {
            "from_user_id": "bob@im.wechat",
            "message_id": "m-2",
            "context_token": "ctx-bob",
            "item_list": [
                {"type": 2},
                {"type": 3, "voice_item": {"text": "voice transcript"}},
                {"type": 1, "text_item": {"text": "hello"}},
            ],
        },
    }


def test_normalize_weixin_webhook_payload():
    """Inbound webhook payloads should normalize to a runtime envelope."""

    envelope = normalize_weixin_webhook_payload(_sample_weixin_payload())

    assert envelope.account_id == "main"
    assert envelope.sender_id == "alice@im.wechat"
    assert envelope.message_id == "42"
    assert envelope.context_token == "ctx-42"
    assert envelope.content == "执行命令 git status"


def test_normalize_weixin_webhook_payload_nested_items():
    """Nested payloads should also normalize and summarize item_list content."""

    envelope = normalize_weixin_webhook_payload(_sample_nested_payload())

    assert envelope.sender_id == "bob@im.wechat"
    assert envelope.message_id == "m-2"
    assert envelope.context_token == "ctx-bob"
    assert envelope.content == "[image]\nvoice transcript\nhello"


def test_build_weixin_message():
    """Normalized envelope should become a runtime message."""

    envelope = normalize_weixin_webhook_payload(_sample_weixin_payload())
    message = build_weixin_message(envelope)

    assert isinstance(message, Message)
    assert message.channel == "weixin"
    assert message.user_id == "alice@im.wechat"
    assert message.metadata["account_id"] == "main"
    assert message.metadata["message_id"] == "42"


def test_weixin_token_helpers():
    """Token parsing should support both header token and bearer fallback."""

    header_token = provided_weixin_webhook_token({"x-weixin-webhook-token": "abc"})
    bearer_token = provided_weixin_webhook_token({"authorization": "Bearer xyz"})

    assert header_token == "abc"
    assert bearer_token == "xyz"
    assert is_valid_weixin_webhook_token("secret", "secret") is True
    assert is_valid_weixin_webhook_token("secret", "wrong") is False


@pytest.mark.asyncio
async def test_send_weixin_text_reply():
    """Replies should be posted to ilink API when configured."""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"ret": 0})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        weixin_enabled=True,
        weixin_reply_via_api=True,
        weixin_bot_token="wx-token",
        weixin_base_url="https://ilinkai.weixin.qq.com",
    )
    envelope = normalize_weixin_webhook_payload(_sample_weixin_payload())

    result = await send_weixin_text_reply(
        envelope=envelope,
        reply_text="done",
        settings=settings,
        client=client,
    )
    await client.aclose()

    assert result is not None
    assert captured["url"] == "https://ilinkai.weixin.qq.com/ilink/bot/sendmessage"
    assert "wx-token" in captured["headers"]["authorization"]
    assert '"to_user_id":"alice@im.wechat"' in captured["body"]
    assert '"context_token":"ctx-42"' in captured["body"]


def test_weixin_webhook_route(monkeypatch):
    """Inbound webhook processing should work through the API route."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        weixin_enabled=True,
        weixin_webhook_token="weixin-secret",
        weixin_allowed_user_ids="alice@im.wechat",
        weixin_reply_via_api=False,
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
    with TestClient(app) as client:
        response = client.post(
            "/api/channels/weixin/webhook",
            headers={"x-weixin-webhook-token": "weixin-secret"},
            json=_sample_weixin_payload(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["event_type"] == "message"
    assert "git status" in body["reply"]["text"]
    assert "On branch main" in body["reply"]["text"]


def test_weixin_webhook_default_path_compatible(monkeypatch):
    """Default /weixin/messages path should be usable for upstream compatibility."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        weixin_enabled=True,
        weixin_webhook_token="weixin-secret",
        weixin_reply_via_api=False,
    )

    async def fake_process_message(message: Message) -> Task:
        task = Task(
            message=message,
            user_id=message.user_id,
            channel=message.channel,
            state=TaskState.COMPLETED,
        )
        task.result = ExecutionResult.success(data={"result": "ok"})
        return task

    fake_engine = AsyncMock()
    fake_engine.process_message.side_effect = fake_process_message

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: fake_engine)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/weixin/messages",
            headers={"x-weixin-webhook-token": "weixin-secret"},
            json=_sample_weixin_payload(),
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is True


def test_weixin_test_route_live(monkeypatch):
    """Manual test route should call outbound API when fully configured."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        weixin_enabled=True,
        weixin_reply_via_api=True,
        weixin_bot_token="wx-token",
    )

    async def fake_send(envelope, reply_text, settings, context_token_override=None):
        assert envelope.sender_id == "alice@im.wechat"
        assert reply_text == "ping"
        assert context_token_override == "ctx-manual"
        return {"status_code": 200, "body": {"ret": 0}}

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "send_weixin_text_reply", fake_send)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/channels/weixin/test",
            json={
                "recipient": "alice@im.wechat",
                "context_token": "ctx-manual",
                "text": "ping",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["mode"] == "live"
    assert data["delivery"]["status_code"] == 200


def test_weixin_test_route_dry_run(monkeypatch):
    """Manual test route should return dry-run details if config is incomplete."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        weixin_enabled=True,
        weixin_reply_via_api=False,
    )

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/channels/weixin/test",
            json={"recipient": "alice@im.wechat", "text": "ping"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "dry_run"
    assert "LOCALCLAW_WEIXIN_REPLY_VIA_API" in data["missing"]
    assert "context_token" in data["missing"]

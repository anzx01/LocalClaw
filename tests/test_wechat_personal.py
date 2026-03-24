"""Tests for the experimental personal WeChat bridge."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from localclaw.channels.wechat_personal import (
    build_personal_wechat_message,
    format_task_for_personal_wechat,
    is_valid_personal_wechat_token,
    normalize_personal_wechat_payload,
    send_personal_wechat_reply,
)
from localclaw.config.settings import Settings
from localclaw.core.models import ExecutionResult, Message, Task, TaskState


def test_normalize_personal_wechat_payload():
    """Bridge payloads should be normalized consistently."""

    envelope = normalize_personal_wechat_payload(
        {
            "bridge": "openclaw-wechat",
            "data": {
                "text": "鎵ц鍛戒护 git status",
                "fromUser": "wxid_alice",
                "roomId": "room-1",
                "msgId": "m-1",
                "senderName": "Alice",
            },
        }
    )

    assert envelope.content == "鎵ц鍛戒护 git status"
    assert envelope.sender_id == "wxid_alice"
    assert envelope.conversation_id == "room-1"
    assert envelope.reply_target == "room-1"
    assert envelope.message_id == "m-1"
    assert envelope.bridge_name == "openclaw-wechat"


def test_build_personal_wechat_message():
    """The normalized payload should become a runtime message."""

    envelope = normalize_personal_wechat_payload(
        {"content": "hello", "sender_id": "wxid_bob", "conversation_id": "chat-1"}
    )
    message = build_personal_wechat_message(envelope)

    assert isinstance(message, Message)
    assert message.channel == "wechat_personal"
    assert message.user_id == "wxid_bob"
    assert message.metadata["conversation_id"] == "chat-1"


def test_format_task_for_personal_wechat_shell_result():
    """Shell execution should be rendered as compact chat text."""

    task = Task(state=TaskState.COMPLETED)
    task.result = ExecutionResult.success(
        data={
            "step1": {
                "command": "git status",
                "stdout": "On branch main",
                "stderr": "",
                "exit_code": 0,
            }
        }
    )

    reply = format_task_for_personal_wechat(task)

    assert "git status" in reply
    assert "On branch main" in reply
    assert "退出码: 0" in reply


def test_is_valid_personal_wechat_token():
    """Bridge shared-secret validation should support header or bearer token."""

    assert is_valid_personal_wechat_token("secret", "secret", None) is True
    assert is_valid_personal_wechat_token("secret", None, "Bearer secret") is True
    assert is_valid_personal_wechat_token("secret", "wrong", None) is False


@pytest.mark.asyncio
async def test_send_personal_wechat_reply_uses_proxy():
    """Replies should be forwarded to the configured proxy when enabled."""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        wechat_personal_enabled=True,
        wechat_personal_reply_via_proxy=True,
        wechat_personal_proxy_url="https://bridge.example.test/reply",
        wechat_personal_api_key="token-123",
    )

    envelope = normalize_personal_wechat_payload(
        {"content": "hello", "sender_id": "wxid_carol", "conversation_id": "chat-2"}
    )
    result = await send_personal_wechat_reply(
        envelope,
        "done",
        settings=settings,
        client=client,
    )
    await client.aclose()

    assert result is not None
    assert captured["url"] == "https://bridge.example.test/reply"
    assert "token-123" in captured["headers"]["authorization"]
    assert '"text":"done"' in captured["body"]


def test_personal_wechat_webhook_route(monkeypatch):
    """The webhook route should accept bridge messages and return a text reply."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        wechat_personal_enabled=True,
        wechat_personal_inbound_token="bridge-secret",
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
            "/api/channels/wechat-personal/webhook",
            headers={"X-LocalClaw-Token": "bridge-secret"},
            json={
                "bridge": "openclaw-wechat",
                "data": {"text": "鎵ц鍛戒护 git status", "fromUser": "wxid_dave", "roomId": "room-2"},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["reply"]["text"].startswith("命令: git status")


def test_personal_wechat_test_route_live(monkeypatch):
    """The manual test route should call the configured bridge proxy when ready."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        wechat_personal_enabled=True,
        wechat_personal_reply_via_proxy=True,
        wechat_personal_proxy_url="https://bridge.example.test/reply",
    )

    async def fake_send(envelope, reply_text, settings):
        assert envelope.reply_target == "room-2"
        assert reply_text == "ping"
        return {"status_code": 200, "body": {"ok": True}}

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "send_personal_wechat_reply", fake_send)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/channels/wechat-personal/test",
            json={"reply_target": "room-2", "text": "ping"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["mode"] == "live"
    assert data["delivery"]["status_code"] == 200


def test_personal_wechat_test_route_dry_run(monkeypatch):
    """The manual test route should return a dry run preview when proxy config is incomplete."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        wechat_personal_enabled=True,
        wechat_personal_reply_via_proxy=False,
    )

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/channels/wechat-personal/test",
            json={"reply_target": "room-2", "text": "ping"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "dry_run"
    assert "LOCALCLAW_WECHAT_PERSONAL_REPLY_VIA_PROXY" in data["missing"]

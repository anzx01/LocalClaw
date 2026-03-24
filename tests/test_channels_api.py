"""Tests for aggregated channel metadata used by the Web UI."""

from datetime import datetime

from fastapi.testclient import TestClient

from localclaw.config.settings import Settings
from localclaw.core.models import Message, Plan, Step, StepStatus, StepType, Task, TaskState


def test_channels_overview_endpoint(monkeypatch):
    """The Web UI should be able to fetch a single aggregated channels payload."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        wechat_personal_enabled=True,
        wechat_personal_inbound_token="wechat-secret",
        whatsapp_enabled=True,
        whatsapp_verify_token="verify-token",
        whatsapp_phone_number_id="1234567890",
        whatsapp_reply_via_cloud_api=True,
    )

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.get("/api/channels")

    assert response.status_code == 200
    data = response.json()
    assert len(data["channels"]) == 2

    wechat_channel = next(channel for channel in data["channels"] if channel["key"] == "wechat_personal")
    whatsapp_channel = next(channel for channel in data["channels"] if channel["key"] == "whatsapp")

    assert wechat_channel["enabled"] is True
    assert wechat_channel["webhook_path"] == "/api/channels/wechat-personal/webhook"
    assert wechat_channel["checks"]["has_inbound_token"] is True

    assert whatsapp_channel["enabled"] is True
    assert whatsapp_channel["reply_mode"] == "cloud_api"
    assert whatsapp_channel["verify_path"] == "/api/channels/whatsapp/webhook"
    assert whatsapp_channel["checks"]["has_phone_number_id"] is True


def test_static_ui_contains_channels_tab(monkeypatch):
    """The static UI should expose the Channels tab and aggregated workflow hints."""

    from localclaw.channels import web as web_channel

    settings = Settings()
    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Channels" in response.text
    assert "Approvals" in response.text
    assert "Channel Configuration" in response.text
    assert "/api/channels" in response.text
    assert "/api/approvals" in response.text
    assert "/api/clawhub/search" in response.text
    assert "translator" not in response.text.lower()


def test_approvals_endpoint_lists_pending_shell_steps(monkeypatch):
    """The approval center should expose pending high-risk steps with enough context."""

    from localclaw.channels import web as web_channel

    pending_step = Step(
        id="step-shell",
        type=StepType.TOOL_CALL,
        status=StepStatus.PENDING,
        name="Run raw shell",
        tool_name="shell",
        input={"command": "dir"},
    )
    task = Task(
        id="task-approval",
        state=TaskState.VERIFYING,
        message=Message(content="/shell dir", user_id="web-user", channel="web"),
        plan=Plan(steps=[pending_step]),
        current_step_index=0,
        user_id="web-user",
        channel="web",
        created_at=datetime(2026, 3, 24, 10, 0, 0),
    )

    class FakeEngine:
        def get_active_tasks(self):
            return [task]

        def get_task_history(self, limit):
            return []

    settings = Settings()
    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.get("/api/approvals")

    assert response.status_code == 200
    data = response.json()
    assert len(data["approvals"]) == 1
    approval = data["approvals"][0]
    assert approval["task_id"] == "task-approval"
    assert approval["message"] == "/shell dir"
    assert approval["step"]["tool_name"] == "shell"
    assert approval["step"]["command_preview"] == "dir"
    assert approval["step"]["risk_level"] == "critical"


def test_approve_endpoint_returns_enriched_task_payload(monkeypatch):
    """Approving from the UI should return the enriched task shape used by the dashboard."""

    from localclaw.channels import web as web_channel

    completed_step = Step(
        id="step-shell",
        type=StepType.TOOL_CALL,
        status=StepStatus.COMPLETED,
        name="Run raw shell",
        tool_name="shell",
        input={"command": "dir"},
    )
    task = Task(
        id="task-approval",
        state=TaskState.COMPLETED,
        message=Message(content="/shell dir", user_id="web-user", channel="web"),
        plan=Plan(steps=[completed_step]),
        current_step_index=0,
        user_id="web-user",
        channel="web",
    )
    task.result = {"step-shell": {"stdout": "ok"}}

    class FakeEngine:
        async def approve_and_resume_step(self, task_id, step_id):
            assert task_id == "task-approval"
            assert step_id == "step-shell"
            return task

    settings = Settings()
    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(web_channel, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post("/api/tasks/task-approval/approve/step-shell")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "task-approval"
    assert data["state"] == "completed"
    assert data["message"] == "/shell dir"
    assert data["channel"] == "web"
    assert data["result"]["step-shell"]["stdout"] == "ok"

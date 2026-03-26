"""Tests for aggregated channel metadata used by the Web UI."""

from datetime import datetime

from fastapi.testclient import TestClient

from localclaw.config.settings import Settings
from localclaw.core.models import Message, Plan, Step, StepStatus, StepType, Task, TaskState
from localclaw.skills.base import create_skill_from_dict
from localclaw.skills.registry import SkillRegistry


def test_channels_overview_endpoint(monkeypatch):
    """The Web UI should be able to fetch a single aggregated channels payload."""

    from localclaw.channels import web as web_channel

    settings = Settings(
        wechat_personal_enabled=True,
        wechat_personal_inbound_token="wechat-secret",
        weixin_enabled=True,
        weixin_webhook_token="weixin-secret",
        weixin_reply_via_api=True,
        weixin_bot_token="bot-token",
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
    assert len(data["channels"]) == 3

    wechat_channel = next(channel for channel in data["channels"] if channel["key"] == "wechat_personal")
    weixin_channel = next(channel for channel in data["channels"] if channel["key"] == "weixin")
    whatsapp_channel = next(channel for channel in data["channels"] if channel["key"] == "whatsapp")

    assert wechat_channel["enabled"] is True
    assert wechat_channel["webhook_path"] == "/api/channels/wechat-personal/webhook"
    assert wechat_channel["checks"]["has_inbound_token"] is True

    assert weixin_channel["enabled"] is True
    assert weixin_channel["reply_mode"] == "weixin_api"
    assert weixin_channel["webhook_path"] == "/weixin/messages"
    assert weixin_channel["login_path"] == "/api/channels/weixin/login/start"
    assert weixin_channel["checks"]["has_webhook_token"] is True

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
    assert "Settings" in response.text
    assert "Channel Configuration" in response.text
    assert "Scan to Login" in response.text
    assert "/api/channels" in response.text
    assert "/api/approvals" in response.text
    assert "/api/system/service" in response.text
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


def test_skills_endpoint_marks_managed_skills_as_removable(monkeypatch, tmp_path):
    """The Skills API should distinguish managed installs from configured and runtime skills."""

    from localclaw.channels import web as web_channel
    from localclaw.skills import registry as skill_registry_module

    managed_dir = tmp_path / "managed"
    configured_dir = tmp_path / "configured"
    for directory in (managed_dir, configured_dir):
        directory.mkdir()

    registry = SkillRegistry()
    managed_skill = create_skill_from_dict(
        {
            "name": "managed-demo",
            "version": "1.0.0",
            "description": "Managed skill",
            "type": "workflow",
            "metadata": {
                "source_path": str(managed_dir / "managed-demo" / "SKILL.md"),
                "skill_key": "managed-demo",
                "aliases": ["managed"],
            },
        }
    )
    configured_skill = create_skill_from_dict(
        {
            "name": "configured-demo",
            "version": "1.0.0",
            "description": "Configured directory skill",
            "type": "workflow",
            "metadata": {
                "source_path": str(configured_dir / "configured-demo" / "SKILL.md"),
            },
        }
    )
    runtime_skill = create_skill_from_dict(
        {
            "name": "runtime-demo",
            "version": "1.0.0",
            "description": "Runtime skill",
            "type": "atomic",
            "metadata": {},
        }
    )
    registry.register(managed_skill)
    registry.register(configured_skill)
    registry.register(runtime_skill)

    settings = Settings(
        _env_file=None,
        managed_skills_dir=managed_dir,
        extra_skill_dirs=[configured_dir],
    )

    monkeypatch.setattr(web_channel, "get_settings", lambda: settings)
    monkeypatch.setattr(skill_registry_module, "get_skill_registry", lambda: registry)
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.get("/api/skills")

    assert response.status_code == 200
    payload = response.json()
    managed = next(skill for skill in payload if skill["name"] == "managed-demo")
    configured = next(skill for skill in payload if skill["name"] == "configured-demo")
    runtime = next(skill for skill in payload if skill["name"] == "runtime-demo")

    assert managed["removable"] is True
    assert managed["source_scope"] == "managed"
    assert managed["skill_key"] == "managed-demo"
    assert "managed" in managed["aliases"]

    assert configured["removable"] is False
    assert configured["source_scope"] == "configured"

    assert runtime["removable"] is False
    assert runtime["source_scope"] == "runtime"


def test_background_service_endpoints(monkeypatch):
    """The Settings tab should be able to read and mutate service status via API."""

    from localclaw.channels import web as web_channel

    status_payload = {
        "supported": True,
        "platform": "win32",
        "service_name": "LocalClaw",
        "display_name": "LocalClaw Runtime",
        "installed": False,
        "state": "NOT_INSTALLED",
        "running": False,
        "startup_type": "UNKNOWN",
        "binary_path": "",
        "can_manage": True,
        "python_executable": "C:/Python/python.exe",
        "script_path": "G:/myaist/LocalClaw/run_server.py",
        "command": '"C:/Python/python.exe" "G:/myaist/LocalClaw/run_server.py"',
        "message": "Service is not installed.",
    }

    action_payload = {
        "ok": True,
        "action": "install",
        "changed": True,
        "message": "Service installed successfully.",
        "status": {**status_payload, "installed": True, "state": "STOPPED", "message": ""},
    }

    monkeypatch.setattr(web_channel, "get_settings", lambda: Settings())
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)
    monkeypatch.setattr(web_channel, "get_background_service_status", lambda: status_payload)
    monkeypatch.setattr(web_channel, "install_background_service", lambda: action_payload)
    monkeypatch.setattr(web_channel, "start_background_service", lambda: {**action_payload, "action": "start"})
    monkeypatch.setattr(web_channel, "stop_background_service", lambda: {**action_payload, "action": "stop"})
    monkeypatch.setattr(web_channel, "uninstall_background_service", lambda: {**action_payload, "action": "uninstall"})

    app = web_channel.create_app()
    with TestClient(app) as client:
        status_response = client.get("/api/system/service")
        install_response = client.post("/api/system/service/install")
        start_response = client.post("/api/system/service/start")
        stop_response = client.post("/api/system/service/stop")
        uninstall_response = client.post("/api/system/service/uninstall")

    assert status_response.status_code == 200
    assert status_response.json()["service_name"] == "LocalClaw"
    assert status_response.json()["installed"] is False

    assert install_response.status_code == 200
    assert install_response.json()["ok"] is True
    assert install_response.json()["action"] == "install"

    assert start_response.status_code == 200
    assert start_response.json()["action"] == "start"

    assert stop_response.status_code == 200
    assert stop_response.json()["action"] == "stop"

    assert uninstall_response.status_code == 200
    assert uninstall_response.json()["action"] == "uninstall"

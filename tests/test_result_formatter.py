"""Tests for chat-friendly task result formatting."""

from fastapi.testclient import TestClient

from localclaw.core.models import ExecutionResult, Intent, Message, Task, TaskState


def _sample_weather_body() -> dict:
    return {
        "nearest_area": [
            {
                "areaName": [{"value": "Shanghai"}],
                "country": [{"value": "China"}],
            }
        ],
        "weather": [
            {
                "date": "2026-03-25",
                "mintempC": "11",
                "maxtempC": "19",
                "hourly": [
                    {
                        "time": "1200",
                        "chanceofrain": "5",
                        "precipMM": "0.0",
                        "weatherDesc": [{"value": "Sunny"}],
                    }
                ],
            },
            {
                "date": "2026-03-26",
                "mintempC": "13",
                "maxtempC": "18",
                "hourly": [
                    {
                        "time": "900",
                        "chanceofrain": "65",
                        "precipMM": "1.2",
                        "weatherDesc": [{"value": "Light rain"}],
                    },
                    {
                        "time": "1200",
                        "chanceofrain": "82",
                        "precipMM": "2.1",
                        "weatherDesc": [{"value": "Moderate rain"}],
                    },
                ],
            },
        ],
    }


def _sample_news_rss() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>Headline One - Reuters</title>
      <link>https://example.com/news/1</link>
      <source url="https://reuters.com">Reuters</source>
    </item>
    <item>
      <title>Headline Two - AP News</title>
      <link>https://example.com/news/2</link>
      <source url="https://apnews.com">AP News</source>
    </item>
    <item>
      <title>Headline Three - BBC</title>
      <link>https://example.com/news/3</link>
      <source url="https://bbc.com">BBC</source>
    </item>
  </channel>
</rss>
"""


def test_format_task_for_chat_weather_forecast():
    """Weather step results should become a readable forecast answer."""

    from localclaw.channels.result_formatter import format_task_for_chat

    task = Task(
        state=TaskState.COMPLETED,
        message=Message(content="明天下雨不？", user_id="web", channel="web"),
        intent=Intent(
            intent="check_weather",
            params={"day_offset": 1, "day_label": "明天"},
            raw_message="明天下雨不？",
        ),
    )
    task.result = ExecutionResult.success(
        message="Task completed successfully",
        data={
            "step-weather": {
                "status_code": 200,
                "body": _sample_weather_body(),
            }
        },
    )

    reply = format_task_for_chat(task)

    assert "明天" in reply
    assert "下雨" in reply
    assert "Shanghai" in reply
    assert "降雨概率：82%" in reply


def test_format_task_for_chat_rss_headlines():
    """RSS headline responses should become a readable news list."""

    from localclaw.channels.result_formatter import format_task_for_chat

    task = Task(
        state=TaskState.COMPLETED,
        message=Message(content="从网上给我2个最新AI新闻", user_id="web", channel="web"),
        intent=Intent(
            intent="tool.http_get",
            params={"limit": 2, "topic": "AI"},
            raw_message="从网上给我2个最新AI新闻",
        ),
    )
    task.result = ExecutionResult.success(
        message="Task completed successfully",
        data={
            "step-news": {
                "status_code": 200,
                "body": _sample_news_rss(),
            }
        },
    )

    reply = format_task_for_chat(task)

    assert "AI最新新闻（2条）" in reply
    assert "1. Headline One | Reuters" in reply
    assert "2. Headline Two | AP News" in reply
    assert "https://example.com/news/1" in reply
    assert "<?xml" not in reply


def test_format_task_for_chat_desktop_folders_only():
    """Folder-list intents should suppress plain files from the formatted reply."""

    from localclaw.channels.result_formatter import format_task_for_chat

    task = Task(
        state=TaskState.COMPLETED,
        message=Message(content="看看我桌面有那些文件夹？", user_id="web", channel="web"),
        intent=Intent(
            intent="list_folders",
            params={"path": "~/Desktop", "folders_only": True},
            raw_message="看看我桌面有那些文件夹？",
        ),
    )
    task.result = ExecutionResult.success(
        message="Task completed successfully",
        data={
            "step-files": {
                "path": "C:/Users/admin/Desktop",
                "directories": ["Projects", "Notes"],
                "files": ["todo.txt", "readme.md"],
            }
        },
    )

    reply = format_task_for_chat(task)

    assert "Directories:" in reply
    assert "- Projects" in reply
    assert "Files:" not in reply
    assert "todo.txt" not in reply


def test_api_message_returns_formatted_weather_reply(monkeypatch):
    """The chat API should return the formatted task reply, not the generic success placeholder."""

    from localclaw.channels import web as web_channel

    async def fake_process_message(message: Message) -> Task:
        task = Task(
            message=message,
            user_id=message.user_id,
            channel=message.channel,
            state=TaskState.COMPLETED,
            intent=Intent(
                intent="check_weather",
                params={"day_offset": 1, "day_label": "明天"},
                raw_message=message.content,
            ),
        )
        task.result = ExecutionResult.success(
            message="Task completed successfully",
            data={
                "step-weather": {
                    "status_code": 200,
                    "body": _sample_weather_body(),
                }
            },
        )
        return task

    class FakeEngine:
        async def process_message(self, message: Message) -> Task:
            return await fake_process_message(message)

    monkeypatch.setattr(web_channel, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(web_channel, "initialize_system", lambda: None)

    app = web_channel.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/message",
            json={"content": "明天下雨不？", "user_id": "web", "channel": "web"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "Task completed successfully" not in data["message"]
    assert "明天" in data["message"]
    assert "下雨" in data["message"]

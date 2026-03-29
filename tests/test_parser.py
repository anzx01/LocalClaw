"""Tests for the parser module."""

import pytest

from localclaw.core.models import Message
from localclaw.core.parser import (
    DSLParser,
    ParseRule,
    Parser,
    RuleParser,
    create_default_parser,
)


@pytest.mark.asyncio
async def test_parse_rule():
    """Test ParseRule matching."""
    rule = ParseRule(
        pattern=r"^hello\s+(?P<name>\w+)$",
        intent="greeting",
        params_template={"name": "$name"},
    )
    
    result = rule.match("hello world")
    assert result is not None
    assert result["name"] == "world"
    
    result = rule.match("goodbye world")
    assert result is None


@pytest.mark.asyncio
async def test_rule_parser():
    """Test RuleParser."""
    parser = RuleParser()
    parser.add_rule(ParseRule(
        pattern=r"^test\s+(?P<arg>\w+)$",
        intent="test",
        params_template={"arg": "$arg"},
    ))
    
    message = Message(content="test value")
    intent = await parser.parse(message)
    
    assert intent is not None
    assert intent.intent == "test"
    assert intent.params["arg"] == "value"


@pytest.mark.asyncio
async def test_dsl_parser():
    """Test DSLParser."""
    parser = DSLParser()
    
    message = Message(content="/hello name=world")
    intent = await parser.parse(message)
    
    assert intent is not None
    assert intent.intent == "skill.hello"
    assert intent.params["name"] == "world"


@pytest.mark.asyncio
async def test_dsl_parser_supports_openclaw_skill_keys():
    """Slash commands should accept dotted and dashed OpenClaw skill identifiers."""
    parser = DSLParser()

    message = Message(content="/repo.fs action=list path=.")
    intent = await parser.parse(message)

    assert intent is not None
    assert intent.intent == "skill.repo.fs"
    assert intent.params["action"] == "list"
    assert intent.params["path"] == "."


@pytest.mark.asyncio
async def test_dsl_parser_positional():
    """Test DSLParser with positional args."""
    parser = DSLParser()
    
    message = Message(content="/echo arg1 arg2 key=value")
    intent = await parser.parse(message)
    
    assert intent is not None
    assert intent.intent == "skill.echo"
    assert intent.params["key"] == "value"
    assert "arg1" in intent.params["_args"]
    assert "arg2" in intent.params["_args"]


@pytest.mark.asyncio
async def test_dsl_parser_safe_command_shortcut():
    """The /cmd shortcut should map directly to safe_shell."""
    parser = DSLParser()

    message = Message(content="/cmd git status")
    intent = await parser.parse(message)

    assert intent is not None
    assert intent.intent == "tool.safe_shell"
    assert intent.params["command"] == "git status"


@pytest.mark.asyncio
async def test_default_parser_natural_language_command():
    """Natural-language command requests should map to safe command execution."""
    parser = create_default_parser()

    message = Message(content="执行命令 git status")
    intent = await parser.parse(message)

    assert intent.intent == "run_command"
    assert intent.params["command"] == "git status"


@pytest.mark.asyncio
async def test_default_parser():
    """Test default parser with built-in rules."""
    parser = create_default_parser()
    
    message = Message(content="hello world")
    intent = await parser.parse(message)
    
    assert intent.intent == "greeting"
    assert intent.params.get("name") == "world"


@pytest.mark.asyncio
async def test_default_parser_help():
    """Test default parser help command."""
    parser = create_default_parser()
    
    message = Message(content="help")
    intent = await parser.parse(message)
    
    assert intent.intent == "help"


@pytest.mark.asyncio
async def test_default_parser_date_and_time_queries():
    """Chinese date/time questions should map to built-in deterministic intents."""
    parser = create_default_parser()

    date_intent = await parser.parse(Message(content="今天周几？"))
    time_intent = await parser.parse(Message(content="现在几点了？"))

    assert date_intent.intent == "date_query"
    assert time_intent.intent == "time_now"


@pytest.mark.asyncio
async def test_default_parser_chinese_help_request():
    """Chinese capability questions should map to help instead of unknown."""
    parser = create_default_parser()

    message = Message(content="你会干啥？")
    intent = await parser.parse(message)

    assert intent.intent == "help"


@pytest.mark.asyncio
async def test_default_parser_unknown():
    """Test default parser with unknown input."""
    parser = create_default_parser()
    
    message = Message(content="something random")
    intent = await parser.parse(message)
    
    assert intent.intent == "unknown"


@pytest.mark.asyncio
async def test_default_parser_desktop_folders_request():
    """Chinese desktop folder questions should map to a folder-list intent."""

    parser = create_default_parser()

    message = Message(content="看看我桌面有那些文件夹？")
    intent = await parser.parse(message)

    assert intent.intent == "list_folders"
    assert intent.params["path"] == "~/Desktop"
    assert intent.params["folders_only"] == "true"


@pytest.mark.asyncio
async def test_default_parser_desktop_file_read_request():
    """Desktop file open requests should map to read_file."""

    parser = create_default_parser()

    message = Message(content="打开桌面的AI量化工具.txt文件")
    intent = await parser.parse(message)

    assert intent.intent == "read_file"
    assert intent.params["path"] == "~/Desktop/AI量化工具.txt"


@pytest.mark.asyncio
async def test_default_parser_desktop_file_read_request_with_space():
    """Desktop file open requests should allow a space before the filename."""

    parser = create_default_parser()

    message = Message(content="打开桌面的 AI量化工具.txt")
    intent = await parser.parse(message)

    assert intent.intent == "read_file"
    assert intent.params["path"] == "~/Desktop/AI量化工具.txt"


@pytest.mark.asyncio
async def test_llm_parse_only_uses_local_model(monkeypatch):
    """When enabled, the local model should be the primary parser for all input."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            class Response:
                content = (
                    '{"intent":"check_weather","params":{"location":"","day_offset":2,"day_label":"后天"}}'
                )

            return Response()

    monkeypatch.setattr("localclaw.core.parser.get_llm_provider", lambda: FakeProvider())

    parser = create_default_parser(llm_enabled=True, llm_parse_only=True)
    message = Message(content="后天冷不？")
    intent = await parser.parse(message)

    assert intent.intent == "check_weather"
    assert intent.params["day_offset"] == 2
    assert intent.params["day_label"] == "后天"
    assert intent.source == "llm"


@pytest.mark.asyncio
async def test_llm_parse_only_extracts_last_json_object_from_reasoning(monkeypatch):
    """LLM parsing should recover the final JSON object from verbose reasoning output."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            del prompt, max_tokens, temperature

            class Response:
                content = (
                    'Example: {"intent":"unknown","params":{}}\n'
                    "</think>\n"
                    '{"intent":"check_weather","params":{"location":"上海","day_offset":1}}'
                )

            return Response()

    monkeypatch.setattr("localclaw.core.parser.get_llm_provider", lambda: FakeProvider())

    parser = create_default_parser(llm_enabled=True, llm_parse_only=True)
    intent = await parser.parse(Message(content="明天上海天气呢"))

    assert intent.intent == "check_weather"
    assert intent.params == {"location": "上海", "day_offset": 1}
    assert intent.source == "llm"


@pytest.mark.asyncio
async def test_llm_parse_only_does_not_fallback_to_legacy_rules(monkeypatch):
    """If the local model returns unknown, llm_parse_only should not fall back to regex rules."""

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, temperature=0.0):
            class Response:
                content = '{"intent":"unknown","params":{}}'

            return Response()

    monkeypatch.setattr("localclaw.core.parser.get_llm_provider", lambda: FakeProvider())

    parser = create_default_parser(llm_enabled=True, llm_parse_only=True)
    message = Message(content="hello world")
    intent = await parser.parse(message)

    assert intent.intent == "unknown"
    assert intent.source == "llm"


@pytest.mark.asyncio
async def test_llm_parser_can_select_skill_plugin(monkeypatch):
    """The local model should be able to return a skill intent directly."""

    captured = {}

    class FakeRegistry:
        def get_model_invocable_info(self):
            return [
                {
                    "name": "fs",
                    "description": "File system workflow plugin",
                    "inputs": {"action": "string", "path": "string"},
                }
            ]

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            captured["prompt"] = prompt

            class Response:
                content = '{"intent":"skill.fs","params":{"action":"list","path":"."}}'

            return Response()

    monkeypatch.setattr("localclaw.core.parser.get_skill_registry", lambda: FakeRegistry())
    monkeypatch.setattr("localclaw.core.parser.get_llm_provider", lambda: FakeProvider())

    parser = create_default_parser(llm_enabled=True, llm_parse_only=True)
    intent = await parser.parse(Message(content="总结这个目录下的文件"))

    assert intent.intent == "skill.fs"
    assert intent.params["action"] == "list"
    assert intent.params["path"] == "."
    assert "Installed skills:" in captured["prompt"]
    assert "- fs | invoke as: skill.fs: File system workflow plugin" in captured["prompt"]


@pytest.mark.asyncio
async def test_llm_parser_retries_with_short_prompt_when_first_result_is_unknown(monkeypatch):
    """Smaller local models should get a second shorter prompt before giving up."""

    calls = {"count": 0}

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            calls["count"] += 1

            class Response:
                content = '{"intent":"unknown","params":{}}'

            if calls["count"] == 2:
                Response.content = '{"intent":"help","params":{}}'
            return Response()

    monkeypatch.setattr("localclaw.core.parser.get_llm_provider", lambda: FakeProvider())

    parser = create_default_parser(llm_enabled=True, llm_parse_only=True)
    intent = await parser.parse(Message(content="你会干啥？"))

    assert calls["count"] == 2
    assert intent.intent == "help"
    assert intent.source == "llm"


@pytest.mark.asyncio
async def test_llm_parser_retries_after_primary_prompt_failure(monkeypatch):
    """If the primary local-model prompt fails, parser should still try the shorter retry prompt."""

    calls = {"count": 0}

    class FakeProvider:
        async def is_available(self):
            return True

        async def generate(self, prompt, max_tokens=None, temperature=0.0):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("timeout")

            class Response:
                content = '{"intent":"help","params":{}}'

            return Response()

    monkeypatch.setattr("localclaw.core.parser.get_llm_provider", lambda: FakeProvider())

    parser = create_default_parser(llm_enabled=True, llm_parse_only=True)
    intent = await parser.parse(Message(content="你会干啥？"))

    assert calls["count"] == 2
    assert intent.intent == "help"
    assert intent.source == "llm"

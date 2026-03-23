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
async def test_default_parser_unknown():
    """Test default parser with unknown input."""
    parser = create_default_parser()
    
    message = Message(content="something random")
    intent = await parser.parse(message)
    
    assert intent.intent == "unknown"

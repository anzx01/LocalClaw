"""Parser module for intent recognition."""

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from localclaw.core.models import Intent, Message
from localclaw.llm.provider import get_llm_provider


logger = logging.getLogger(__name__)


@dataclass
class ParseRule:
    """A rule for parsing user messages."""

    pattern: str
    intent: str
    params_template: Dict[str, str] = field(default_factory=dict)
    priority: int = 0

    def __post_init__(self) -> None:
        self._compiled_pattern = re.compile(self.pattern, re.IGNORECASE)

    def match(self, text: str) -> Optional[Dict[str, Any]]:
        """Match text against the pattern and extract params."""
        match = self._compiled_pattern.match(text)
        if not match:
            return None

        params: Dict[str, Any] = {}
        for key, value_pattern in self.params_template.items():
            if value_pattern.startswith("$"):
                group_name = value_pattern[1:]
                params[key] = match.group(group_name) if group_name in match.groupdict() else ""
            else:
                params[key] = value_pattern

        for key, value in match.groupdict().items():
            if key not in params and value is not None:
                params[key] = value

        return params


class ParserBackend(ABC):
    """Abstract base class for parser backends."""

    @abstractmethod
    async def parse(self, message: Message) -> Optional[Intent]:
        """Parse a message and return an intent."""


class RuleParser(ParserBackend):
    """Rule-based parser using regex patterns."""

    def __init__(self) -> None:
        self._rules: List[ParseRule] = []

    def add_rule(self, rule: ParseRule) -> None:
        """Add a parsing rule."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def add_rules(self, rules: List[ParseRule]) -> None:
        """Add multiple parsing rules."""
        for rule in rules:
            self.add_rule(rule)

    def clear_rules(self) -> None:
        """Clear all rules."""
        self._rules.clear()

    async def parse(self, message: Message) -> Optional[Intent]:
        """Parse a message using rules."""
        text = message.content.strip()

        for rule in self._rules:
            params = rule.match(text)
            if params is not None:
                return Intent(
                    intent=rule.intent,
                    params=params,
                    confidence=1.0,
                    source="rule",
                    raw_message=text,
                )

        return None


class DSLParser(ParserBackend):
    """Parser for DSL-style commands like /skill_name param1 param2."""

    DSL_PATTERN = re.compile(r"^/(\w+)(?:\s+(.*))?$")
    PARAM_PATTERN = re.compile(r"(\w+)=(\S+)|(\S+)")

    async def parse(self, message: Message) -> Optional[Intent]:
        """Parse a DSL command."""
        text = message.content.strip()
        match = self.DSL_PATTERN.match(text)

        if not match:
            return None

        skill_name = match.group(1)
        params_str = match.group(2) or ""

        params: Dict[str, Any] = {}
        positional_args: List[str] = []

        for param_match in self.PARAM_PATTERN.finditer(params_str):
            if param_match.group(1) and param_match.group(2):
                params[param_match.group(1)] = param_match.group(2)
            elif param_match.group(3):
                positional_args.append(param_match.group(3))

        if positional_args:
            params["_args"] = positional_args

        return Intent(
            intent=f"skill.{skill_name}",
            params=params,
            confidence=1.0,
            source="dsl",
            raw_message=text,
        )


class LLMParser(ParserBackend):
    """Optional LLM-based parser for natural language understanding."""

    async def parse(self, message: Message) -> Optional[Intent]:
        """Parse using LLM. Returns None if LLM is not available."""
        try:
            llm_provider = get_llm_provider()
            if not await llm_provider.is_available():
                return None

            prompt = f"""You map user requests to LocalClaw tools.
Return JSON only in the form {{"tool":"tool_name","params":{{...}}}}.

Available tools:
- file_list(path)
- file_read(path)
- file_write(path, content)
- file_delete(path)
- file_mkdir(path)
- shell(command)
- http_get(url)
- get_weather(location)
- web_search(query)

Rules:
- Use get_weather for weather, temperature, rain, or forecast questions.
- Use web_search when the user explicitly asks to search the web.
- Use file_* tools for filesystem requests.
- If nothing fits, return {{"tool":"","params":{{}}}}.

User request: {message.content}
"""

            response = await llm_provider.generate(prompt, temperature=0.0)
            content = response.content.strip()

            if content.startswith("```json") and "```" in content:
                content = content.split("```json", 1)[1].split("```", 1)[0].strip()
            elif content.startswith("```") and "```" in content:
                content = content.split("```", 1)[1].split("```", 1)[0].strip()

            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                content = content[json_start:json_end]

            result = json.loads(content)
            params = result.get("params", {})
            if not isinstance(params, dict):
                params = {}

            tool_name = result.get("tool", "")
            if tool_name == "get_weather":
                intent = "check_weather"
            elif tool_name == "web_search":
                intent = "web_search"
            else:
                intent = f"tool.{tool_name}" if tool_name else "unknown"

            return Intent(
                intent=intent,
                params=params,
                confidence=0.9,
                source="llm",
                raw_message=message.content,
            )
        except Exception as exc:
            logger.debug("LLM parsing skipped: %s", exc)
            return None


class Parser:
    """Main parser combining multiple backends."""

    def __init__(self, llm_enabled: bool = False) -> None:
        self._rule_parser = RuleParser()
        self._dsl_parser = DSLParser()
        self._llm_parser: Optional[LLMParser] = LLMParser() if llm_enabled else None
        self._default_intent = "unknown"

    def add_rule(self, rule: ParseRule) -> None:
        """Add a parsing rule."""
        self._rule_parser.add_rule(rule)

    def add_rules(self, rules: List[ParseRule]) -> None:
        """Add multiple parsing rules."""
        self._rule_parser.add_rules(rules)

    def set_default_intent(self, intent: str) -> None:
        """Set the default intent when no match is found."""
        self._default_intent = intent

    async def parse(self, message: Message) -> Intent:
        """Parse a message and return an intent.

        Order of parsing:
        1. DSL commands (/skill_name params)
        2. Rule-based matching
        3. LLM parsing (if enabled)
        4. Default intent
        """
        intent = await self._dsl_parser.parse(message)
        if intent:
            return intent

        intent = await self._rule_parser.parse(message)
        if intent:
            return intent

        if self._llm_parser:
            intent = await self._llm_parser.parse(message)
            if intent:
                return intent

        return Intent(
            intent=self._default_intent,
            params={"text": message.content},
            confidence=0.0,
            source="default",
            raw_message=message.content,
        )


def create_default_parser(llm_enabled: bool = False) -> Parser:
    """Create a parser with default rules."""
    parser = Parser(llm_enabled=llm_enabled)

    default_rules = [
        ParseRule(
            pattern=r"^hello\s*(?P<name>\w+)?$",
            intent="greeting",
            params_template={"name": "$name"},
            priority=10,
        ),
        ParseRule(
            pattern=r"^hi\s*(?P<name>\w+)?$",
            intent="greeting",
            params_template={"name": "$name"},
            priority=10,
        ),
        ParseRule(
            pattern=r"^help$",
            intent="help",
            priority=20,
        ),
        ParseRule(
            pattern=r"^list\s+skills$",
            intent="list_skills",
            priority=20,
        ),
        ParseRule(
            pattern=r"^status$",
            intent="status",
            priority=20,
        ),
        ParseRule(
            pattern=r"^echo\s+(?P<text>.+)$",
            intent="echo",
            params_template={"text": "$text"},
            priority=5,
        ),
        ParseRule(
            pattern=r"^(?:列出|看看|查看|显示).*?(?:桌面|文件夹|目录|文件)",
            intent="file_list",
            params_template={"path": "~/Desktop"},
            priority=15,
        ),
        ParseRule(
            pattern=r"^(?:查看|列出)?(?:文件夹|目录)\s*(?P<path>.+?)\s*(?:中的|里的)?(?:文件|内容)?$",
            intent="file_list",
            params_template={"path": "$path"},
            priority=25,
        ),
        ParseRule(
            pattern=r"^目录\s*(?P<path>.+?)\s*(?:中的|里的)?(?:文件|内容)?$",
            intent="file_list",
            params_template={"path": "$path"},
            priority=25,
        ),
        ParseRule(
            pattern=r"^(?:list|ls)\s*(?P<path>.*)?$",
            intent="file_list",
            params_template={"path": "$path"},
            priority=15,
        ),
        ParseRule(
            pattern=r"^今天.*?(?:星期|几号|日期)",
            intent="date_query",
            priority=15,
        ),
        ParseRule(
            pattern=r"^what.*?(?:date|day|time)",
            intent="date_query",
            priority=15,
        ),
        ParseRule(
            pattern=r"^(?:今天|明天|后天).*?(?:天气|下雨|下雪|晴天|气温)",
            intent="check_weather",
            priority=15,
        ),
        ParseRule(
            pattern=r"^(?:天气|下雨|下雪|晴天|气温)",
            intent="check_weather",
            priority=15,
        ),
        ParseRule(
            pattern=r"^.*?(?:冷不冷|热不热|冷吗|热吗|温度|气温)",
            intent="check_weather",
            priority=15,
        ),
    ]

    parser.add_rules(default_rules)
    return parser

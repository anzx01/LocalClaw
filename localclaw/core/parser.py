"""Parser module for intent recognition."""

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from localclaw.core.models import Intent, Message
from localclaw.llm.provider import get_llm_provider
from localclaw.skills.registry import get_skill_registry


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
        self._rules.sort(key=lambda item: item.priority, reverse=True)

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
            if params is None:
                continue
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
        normalized_name = skill_name.lower()
        raw_command = params_str.strip()

        if normalized_name in {"cmd", "run", "safe_shell"}:
            return Intent(
                intent="tool.safe_shell",
                params={"command": raw_command},
                confidence=1.0,
                source="dsl",
                raw_message=text,
            )

        if normalized_name in {"shell", "sh"}:
            return Intent(
                intent="tool.shell",
                params={"command": raw_command},
                confidence=1.0,
                source="dsl",
                raw_message=text,
            )

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

    def _build_skill_catalog(self) -> str:
        """Build a compact list of model-invocable skills for the prompt."""
        registry = get_skill_registry()
        lines: List[str] = []
        for info in registry.get_model_invocable_info():
            name = info.get("name", "unknown")
            description = str(info.get("description", "")).strip()
            inputs = info.get("inputs", {}) or {}

            line = f"- {name}"
            if description:
                line += f": {description}"
            if inputs:
                line += f" | inputs: {', '.join(inputs.keys())}"
            lines.append(line)

        if not lines:
            return "- No external skills are currently available for model invocation."
        return "\n".join(lines)

    def _build_prompt(self, message: Message) -> str:
        """Build the local-LLM parsing prompt."""
        skill_catalog = self._build_skill_catalog()
        user_request = json.dumps(message.content, ensure_ascii=False)
        return f"""Return JSON only.
Classify the user request for LocalClaw.

Allowed intents:
greeting, help, echo, list_skills, status, date_query, run_command, run_shell_command, file_list, read_file, write_file, append_file, delete_file, create_directory, check_weather, unknown

Rules:
- "/cmd <command>" -> {{"intent":"run_command","params":{{"command":"<command>"}}}}
- "/shell <command>" -> {{"intent":"run_shell_command","params":{{"command":"<command>"}}}}
- Chinese help/capability questions like "你会干啥？", "你能做什么", "有什么功能", "你可以帮我做什么" -> {{"intent":"help","params":{{}}}}
- Greetings like "hello", "hi", "你好" -> greeting
- Weather questions -> check_weather
- If a listed skill is clearly a better match than a built-in intent, return "skill.<name>"
- If nothing fits, return {{"intent":"unknown","params":{{}}}}

Installed skills:
{skill_catalog}

User: {user_request}
"""

    def _build_retry_prompt(self, message: Message) -> str:
        """Build a shorter retry prompt for smaller local models."""
        user_request = json.dumps(message.content, ensure_ascii=False)
        return f"""Return JSON only.
Chinese capability/help questions like "你会干啥？", "你能做什么", "有什么功能", "你可以帮我做什么" must map to {{"intent":"help","params":{{}}}}.
Greetings like "hello" or "你好" map to greeting.
Weather questions map to check_weather.
"/cmd <command>" maps to run_command.
"/shell <command>" maps to run_shell_command.
If unsure, return {{"intent":"unknown","params":{{}}}}.
User: {user_request}
"""

    def _intent_from_model_output(self, content: str, raw_message: str) -> Optional[Intent]:
        """Parse a model response into an Intent."""
        content = content.strip()

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

        intent_name = result.get("intent", "")
        if intent_name:
            return Intent(
                intent=intent_name,
                params=params,
                confidence=0.9,
                source="llm",
                raw_message=raw_message,
            )

        tool_name = result.get("tool", "")
        intent = f"tool.{tool_name}" if tool_name else "unknown"
        return Intent(
            intent=intent,
            params=params,
            confidence=0.9,
            source="llm",
            raw_message=raw_message,
        )

    async def parse(self, message: Message) -> Optional[Intent]:
        """Parse using LLM. Returns None if LLM is not available."""
        try:
            llm_provider = get_llm_provider()
            if not await llm_provider.is_available():
                return None

            prompt = self._build_prompt(message)
            try:
                response = await llm_provider.generate(prompt, max_tokens=96, temperature=0.0)
                intent = self._intent_from_model_output(response.content, message.content)
                if intent and intent.intent != "unknown":
                    return intent
            except Exception as exc:
                logger.debug("Primary LLM parsing prompt failed: %s", exc)
                intent = None

            retry_prompt = self._build_retry_prompt(message)
            retry_response = await llm_provider.generate(retry_prompt, max_tokens=64, temperature=0.0)
            retry_intent = self._intent_from_model_output(retry_response.content, message.content)
            if retry_intent:
                return retry_intent
            return intent
        except Exception as exc:
            logger.debug("LLM parsing skipped: %s", exc)
            return None


class Parser:
    """Main parser combining multiple backends."""

    def __init__(self, llm_enabled: bool = False, llm_parse_only: bool = False) -> None:
        self._rule_parser = RuleParser()
        self._dsl_parser = DSLParser()
        self._llm_parser: Optional[LLMParser] = LLMParser() if llm_enabled else None
        self._llm_parse_only = llm_parse_only
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
        """Parse a message and return an intent."""
        if self._llm_parser:
            intent = await self._llm_parser.parse(message)
            if intent and intent.intent != "unknown":
                return intent
            if self._llm_parse_only:
                return Intent(
                    intent=self._default_intent,
                    params={"text": message.content},
                    confidence=0.0,
                    source="llm",
                    raw_message=message.content,
                )

        intent = await self._dsl_parser.parse(message)
        if intent:
            return intent

        intent = await self._rule_parser.parse(message)
        if intent:
            return intent

        return Intent(
            intent=self._default_intent,
            params={"text": message.content},
            confidence=0.0,
            source="default",
            raw_message=message.content,
        )


def create_default_parser(llm_enabled: bool = False, llm_parse_only: bool = False) -> Parser:
    """Create a parser with default rules."""
    parser = Parser(llm_enabled=llm_enabled, llm_parse_only=llm_parse_only)
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
            pattern=r"^(?:help|帮助|幫助|你会干啥|你会做什么|你会啥|你能干啥|你能干什么|你能做什么|你可以做什么|你能帮我做什么|你都会啥|你是做什么的|有啥功能|有什么功能|功能介绍)[\s\?\!？。！]*$",
            intent="help",
            priority=25,
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
            pattern=r"^(?:执行|运行|自动执行|帮我执行|帮我运行)(?:命令)?(?:[:：\s]+)(?P<command>.+)$",
            intent="run_command",
            params_template={"command": "$command"},
            priority=30,
        ),
        ParseRule(
            pattern=r"^(?:run|exec)(?:\s+command)?\s+(?P<command>.+)$",
            intent="run_command",
            params_template={"command": "$command"},
            priority=30,
        ),
        ParseRule(
            pattern=r"^(?:shell执行|用shell执行|raw shell)\s+(?P<command>.+)$",
            intent="run_shell_command",
            params_template={"command": "$command"},
            priority=30,
        ),
        ParseRule(
            pattern=r"^(?:列出|看看|查看|显示).*?(?:桌面|文件夹|目录|文件)$",
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

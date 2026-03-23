"""Parser module for intent recognition."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from localclaw.core.models import Intent, Message


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
        pass


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
    
    DSL_PATTERN = re.compile(r'^/(\w+)(?:\s+(.*))?$')
    PARAM_PATTERN = re.compile(r'(\w+)=(\S+)|(\S+)')
    
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
    
    def __init__(self, model: str = "llama2", base_url: Optional[str] = None) -> None:
        self._model = model
        self._base_url = base_url
        self._client = None
    
    def _init_client(self) -> None:
        """Initialize the LLM client lazily."""
        if self._client is not None:
            return
        
        try:
            import ollama
            self._client = ollama.Client(host=self._base_url) if self._base_url else ollama.Client()
        except ImportError:
            raise RuntimeError("ollama package not installed. Install with: pip install ollama")
    
    async def parse(self, message: Message) -> Optional[Intent]:
        """Parse using LLM. Returns None if LLM is not available."""
        self._init_client()
        
        if self._client is None:
            return None
        
        try:
            import asyncio
            
            prompt = f"""Analyze the following user message and extract the intent and parameters.
Return a JSON object with:
- intent: the action the user wants to perform
- params: any parameters mentioned

User message: {message.content}

JSON response:"""
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.chat(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                )
            )
            
            import json
            content = response.get("message", {}).get("content", "{}")
            result = json.loads(content)
            
            return Intent(
                intent=result.get("intent", "unknown"),
                params=result.get("params", {}),
                confidence=0.8,
                source="llm",
                raw_message=message.content,
            )
        except Exception:
            return None


class Parser:
    """Main parser combining multiple backends."""
    
    def __init__(self, llm_enabled: bool = False, llm_model: str = "llama2", llm_base_url: Optional[str] = None) -> None:
        self._rule_parser = RuleParser()
        self._dsl_parser = DSLParser()
        self._llm_parser: Optional[LLMParser] = None
        
        if llm_enabled:
            self._llm_parser = LLMParser(model=llm_model, base_url=llm_base_url)
        
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


def create_default_parser() -> Parser:
    """Create a parser with default rules."""
    parser = Parser()
    
    default_rules = [
        ParseRule(
            pattern=r'^hello\s*(?P<name>\w+)?$',
            intent="greeting",
            params_template={"name": "$name"},
            priority=10,
        ),
        ParseRule(
            pattern=r'^hi\s*(?P<name>\w+)?$',
            intent="greeting",
            params_template={"name": "$name"},
            priority=10,
        ),
        ParseRule(
            pattern=r'^help$',
            intent="help",
            priority=20,
        ),
        ParseRule(
            pattern=r'^list\s+skills$',
            intent="list_skills",
            priority=20,
        ),
        ParseRule(
            pattern=r'^status$',
            intent="status",
            priority=20,
        ),
        ParseRule(
            pattern=r'^echo\s+(?P<text>.+)$',
            intent="echo",
            params_template={"text": "$text"},
            priority=5,
        ),
    ]
    
    parser.add_rules(default_rules)
    return parser

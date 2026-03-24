"""Parser module for intent recognition."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from localclaw.core.models import Intent, Message
from localclaw.llm.provider import get_llm_provider


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
    
    def __init__(self) -> None:
        pass
    
    async def parse(self, message: Message) -> Optional[Intent]:
        """Parse using LLM. Returns None if LLM is not available."""
        try:
            llm_provider = get_llm_provider()
            
            prompt = f"""你是一个智能助手。分析用户请求，决定调用哪个工具。

可用工具:
- file_list: 列出目录内容，参数: path (Windows路径如 "D:/", "C:/Users/用户名/Desktop", "桌面"表示桌面)
- file_read: 读取文件，参数: path
- file_write: 写入文件，参数: path, content
- file_delete: 删除文件，参数: path
- file_mkdir: 创建目录，参数: path
- shell: 执行命令，参数: command
- http_get: HTTP请求，参数: url
- get_weather: 获取天气，参数: location (城市名，如"北京"、"Beijing")
- web_search: 网络搜索，参数: query (搜索关键词)

用户请求: {message.content}

返回JSON格式:
{{"tool": "工具名", "params": {{"参数": "值"}}}}

注意：
- 天气相关的问题（如"冷不"、"热不"、"下雨吗"、"天气"）使用 get_weather
- 需要搜索网络信息时使用 web_search
- 文件操作使用 file_* 系列工具

JSON:"""
            
            response = await llm_provider.generate(prompt)
            
            import json
            content = response.content.strip()
            if content.startswith('```json') and '```' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif content.startswith('```') and '```' in content:
                content = content.split('```')[1].split('```')[0].strip()
            
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
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
        except Exception as e:
            print(f"LLM parsing error: {e}")
            import traceback
            traceback.print_exc()
            return None


class Parser:
    """Main parser combining multiple backends."""
    
    def __init__(self, llm_enabled: bool = False) -> None:
        self._rule_parser = RuleParser()
        self._dsl_parser = DSLParser()
        self._llm_parser: Optional[LLMParser] = None
        
        if llm_enabled:
            self._llm_parser = LLMParser()
        
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
        2. LLM parsing (if enabled)
        3. Rule-based matching
        4. Default intent
        """
        intent = await self._dsl_parser.parse(message)
        if intent:
            return intent
        
        if self._llm_parser:
            intent = await self._llm_parser.parse(message)
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


def create_default_parser(llm_enabled: bool = False) -> Parser:
    """Create a parser with default rules."""
    parser = Parser(llm_enabled=llm_enabled)
    
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
        ParseRule(
            pattern=r'^(?:列出|看看|查看|显示).*?(?:桌面|文件夹|目录|文件)',
            intent="file_list",
            params_template={"path": "~/Desktop"},
            priority=15,
        ),
        ParseRule(
            pattern=r'^文件夹(?P<path>[^下]+?)下.*?(?:文件|内容)',
            intent="file_list",
            params_template={"path": "$path"},
            priority=25,
        ),
        ParseRule(
            pattern=r'^目录(?P<path>[^下]+?)下.*?(?:文件|内容)',
            intent="file_list",
            params_template={"path": "$path"},
            priority=25,
        ),
        ParseRule(
            pattern=r'^(?:list|ls)\s*(?P<path>.*)?$',
            intent="file_list",
            params_template={"path": "$path"},
            priority=15,
        ),
        ParseRule(
            pattern=r'^今天.*?(?:星期|几号|日期)',
            intent="date_query",
            priority=15,
        ),
        ParseRule(
            pattern=r'^what.*?(?:date|day|time)',
            intent="date_query",
            priority=15,
        ),
        ParseRule(
            pattern=r'^(?:今天|明天|后天).*?(?:天气|下雨|下雪|晴)',
            intent="check_weather",
            priority=15,
        ),
        ParseRule(
            pattern=r'^(?:天气|下雨|下雪|晴)',
            intent="check_weather",
            priority=15,
        ),
        ParseRule(
            pattern=r'^.*?(?:冷不|热不|冷吗|热吗|温度)',
            intent="check_weather",
            priority=15,
        ),
    ]
    
    parser.add_rules(default_rules)
    return parser

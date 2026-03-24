"""Planner module for generating execution plans."""

import json
import logging
from typing import Any, Dict, List, Optional

from localclaw.core.models import (
    Context,
    ErrorPolicy,
    Intent,
    Message,
    Plan,
    RetryPolicy,
    Step,
    StepType,
)
from localclaw.llm.provider import get_llm_provider
from localclaw.skills.base import SkillState


logger = logging.getLogger(__name__)


class Planner:
    """Generates execution plans from intents and skills."""
    
    def __init__(self) -> None:
        self._skill_registry: Optional[Any] = None
        self._intent_handlers: Dict[str, callable] = {}
    
    def set_skill_registry(self, registry: Any) -> None:
        """Set the skill registry for skill lookups."""
        self._skill_registry = registry
    
    def register_intent_handler(self, intent: str, handler: callable) -> None:
        """Register a handler for a specific intent."""
        self._intent_handlers[intent] = handler

    def _build_skill_catalog(self) -> str:
        """Build a compact list of model-invocable skills for the planner prompt."""
        if self._skill_registry is None:
            return "- No external skills are currently available for model invocation."

        try:
            infos = self._skill_registry.get_model_invocable_info()
        except Exception:
            infos = []

        lines: List[str] = []
        for info in infos:
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

    def _build_understanding_prompt(self, message: Message) -> str:
        """Build the local-model understanding prompt used before planning."""
        skill_catalog = self._build_skill_catalog()
        user_request = json.dumps(message.content, ensure_ascii=False)
        return f"""Return JSON only.
Classify the LocalClaw user request.

Allowed intents:
greeting, help, echo, list_skills, status, date_query, run_command, run_shell_command, file_list, read_file, write_file, append_file, delete_file, create_directory, check_weather, unknown

Rules:
- "/cmd <command>" -> {{"intent":"run_command","params":{{"command":"<command>"}}}}
- "/shell <command>" -> {{"intent":"run_shell_command","params":{{"command":"<command>"}}}}
- Chinese help/capability questions like "你会干啥？", "你能做什么", "有什么功能", "你可以帮我做什么" -> {{"intent":"help","params":{{}}}}
- Greetings like "hello", "hi", "你好" -> greeting
- Weather questions -> check_weather
- If an installed skill clearly fits better than a built-in intent, return "skill.<name>"
- If nothing fits, return {{"intent":"unknown","params":{{}}}}

Installed skills:
{skill_catalog}

User: {user_request}
"""

    def _build_understanding_retry_prompt(self, message: Message) -> str:
        """Build a shorter retry prompt for smaller local models."""
        user_request = json.dumps(message.content, ensure_ascii=False)
        return f"""Return JSON only.
Chinese help/capability questions like "你会干啥？", "你能做什么", "有什么功能", "你可以帮我做什么" -> {{"intent":"help","params":{{}}}}.
Greetings like "hello" or "你好" -> greeting.
Weather questions -> check_weather.
"/cmd <command>" -> run_command.
"/shell <command>" -> run_shell_command.
If unsure, return {{"intent":"unknown","params":{{}}}}.
User: {user_request}
"""

    def _intent_from_model_output(self, content: str, raw_message: str) -> Intent:
        """Parse a model response into an Intent object."""
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
                source="planner_llm",
                raw_message=raw_message,
            )

        tool_name = result.get("tool", "")
        resolved_intent = f"tool.{tool_name}" if tool_name else "unknown"
        return Intent(
            intent=resolved_intent,
            params=params,
            confidence=0.9,
            source="planner_llm",
            raw_message=raw_message,
        )

    async def _understand_message_with_llm(self, message: Message) -> Intent:
        """Use the local model to understand a message before planning."""
        llm_provider = get_llm_provider()
        if not await llm_provider.is_available():
            return Intent(
                intent="unknown",
                params={},
                confidence=0.0,
                source="planner_llm",
                raw_message=message.content,
            )

        try:
            response = await llm_provider.generate(
                self._build_understanding_prompt(message),
                max_tokens=96,
                temperature=0.0,
            )
            intent = self._intent_from_model_output(response.content, message.content)
            if intent.intent != "unknown":
                return intent
        except Exception as exc:
            logger.debug("Primary planner LLM understanding failed: %s", exc)

        try:
            retry_response = await llm_provider.generate(
                self._build_understanding_retry_prompt(message),
                max_tokens=64,
                temperature=0.0,
            )
            return self._intent_from_model_output(retry_response.content, message.content)
        except Exception as exc:
            logger.debug("Retry planner LLM understanding failed: %s", exc)
            return Intent(
                intent="unknown",
                params={},
                confidence=0.0,
                source="planner_llm",
                raw_message=message.content,
            )

    async def plan_from_message(self, message: Message, context: Optional[Context] = None) -> Plan:
        """Understand a raw message with the local model and build a plan directly."""
        intent = await self._understand_message_with_llm(message)
        plan = await self.plan(intent, context)
        if plan.intent is None:
            plan.intent = intent
        return plan
    
    async def plan(self, intent: Intent, context: Optional[Context] = None) -> Plan:
        """Generate an execution plan from an intent."""
        if intent.intent in self._intent_handlers:
            steps = await self._intent_handlers[intent.intent](intent, context)
            return Plan(steps=steps, intent=intent)
        
        if intent.intent.startswith("skill."):
            skill_name = intent.intent[6:]
            return self._plan_from_skill(skill_name, intent.params, intent)
        
        if intent.intent.startswith("tool."):
            tool_name = intent.intent[5:]
            return self._plan_tool_call(tool_name, intent.params, intent)

        if intent.intent == "run_command":
            return self._plan_tool_call(
                "safe_shell",
                {"command": intent.params.get("command", "")},
                intent,
            )

        if intent.intent == "run_shell_command":
            return self._plan_tool_call(
                "shell",
                {"command": intent.params.get("command", "")},
                intent,
            )
        
        if intent.intent == "greeting":
            return self._plan_greeting(intent)
        
        if intent.intent == "help":
            return self._plan_help(intent)
        
        if intent.intent == "echo":
            return self._plan_echo(intent)
        
        if intent.intent == "list_skills":
            return self._plan_list_skills(intent)
        
        if intent.intent == "status":
            return self._plan_status(intent)
        
        if intent.intent == "date_today":
            return self._plan_date_today(intent)
        
        if intent.intent == "date_query":
            return self._plan_date_today(intent)
        
        if intent.intent == "time_now":
            return self._plan_time_now(intent)
        
        if intent.intent == "get_day_of_week":
            return self._plan_from_skill("day_of_week", intent.params, intent)
        if intent.intent == "get_date":
            return self._plan_from_skill("date", intent.params, intent)
        if intent.intent == "check_weather" or intent.intent == "get_weather":
            location = intent.params.get("location", "Beijing")
            plan = Plan(intent=intent)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="get_weather",
                    tool_name="http_get",
                    input={"url": f"https://wttr.in/{location}?format=j1"},
                    timeout=10.0,
                )
            )
            return plan
        if intent.intent == "query_capabilities":
            return self._plan_from_skill("list_skills", intent.params, intent)
        if intent.intent == "list_folders":
            # Get desktop path
            import os
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            # Create plan to list desktop folders
            plan = Plan(intent=intent)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="list_desktop_folders",
                    tool_name="file_list",
                    input={"path": desktop_path}
                )
            )
            return plan
        if intent.intent == "search_skill":
            # Create plan to search for skills
            plan = Plan(intent=intent)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="search_skills",
                    tool_name="clawhub_search",
                    input={"query": intent.params.get("skill", "")}
                )
            )
            return plan
        if intent.intent == "list_files" or intent.intent == "list" or intent.intent == "file_list":
            import os
            import re
            path = intent.params.get("path", ".")
            if path in ["~/Desktop", "Desktop", "桌面", "~/桌面"]:
                path = os.path.join(os.path.expanduser("~"), "Desktop")
            elif path and not os.path.isabs(path):
                drive_match = re.match(r'^([A-Za-z]):[/\\]?$', path)
                if drive_match:
                    path = drive_match.group(1) + ":/"
                elif os.path.exists(os.path.join(os.path.expanduser("~"), "Desktop", path)):
                    path = os.path.join(os.path.expanduser("~"), "Desktop", path)
                elif os.path.exists(os.path.join(os.getcwd(), path)):
                    path = os.path.join(os.getcwd(), path)
            plan = Plan(intent=intent)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="list_files",
                    tool_name="file_list",
                    input={"path": path}
                )
            )
            return plan
        if intent.intent == "create_directory" or intent.intent == "mkdir":
            # Create plan to create directory
            plan = Plan(intent=intent)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="create_directory",
                    tool_name="file_mkdir",
                    input={"path": intent.params.get("path", intent.params.get("dir_name", intent.params.get("directory", ""))), "parents": intent.params.get("parents", True)}
                )
            )
            return plan
        if intent.intent == "append_file" or intent.intent == "fs_append":
            # Create plan to append file content
            plan = Plan(intent=intent)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="append_file",
                    tool_name="file_append",
                    input={"path": intent.params.get("path", intent.params.get("file", "")), "content": intent.params.get("content", "")}
                )
            )
            return plan
        if intent.intent == "delete_file" or intent.intent == "delete":
            # Create plan to delete file or directory
            plan = Plan(intent=intent)
            # Try to get path from different parameter names
            path = intent.params.get("path", intent.params.get("file", intent.params.get("file_path", "")))
            # If path is not provided, try to build it from directory and filename
            if not path:
                directory = intent.params.get("directory", "")
                filename = intent.params.get("filename", "")
                if directory and filename:
                    import os
                    path = os.path.join(directory, filename)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="delete_file",
                    tool_name="file_delete",
                    input={"path": path, "recursive": intent.params.get("recursive", True)}
                )
            )
            return plan
        if intent.intent == "write_file" or intent.intent == "fs.write":
            # Create plan to write file content
            plan = Plan(intent=intent)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="write_file",
                    tool_name="file_write",
                    input={"path": intent.params.get("path", ""), "content": intent.params.get("content", ""), "mode": intent.params.get("mode", "write")}
                )
            )
            return plan
        if intent.intent == "read_file" or intent.intent == "fs.read":
            # Create plan to read file content
            plan = Plan(intent=intent)
            plan.steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name="read_file",
                    tool_name="file_read",
                    input={"path": intent.params.get("path", intent.params.get("file_path", ""))}
                )
            )
            return plan
        
        return self._plan_unknown(intent)
    
    def _plan_tool_call(self, tool_name: str, params: Dict[str, Any], intent: Intent) -> Plan:
        """Create a plan for direct tool call from LLM."""
        import os
        import platform
        
        plan = Plan(intent=intent)
        
        processed_params = {}
        for key, value in params.items():
            if key == "path" and value:
                if isinstance(value, str):
                    value_lower = value.lower()
                    if value_lower in ["桌面", "desktop"]:
                        value = os.path.join(os.path.expanduser("~"), "Desktop")
                    elif "用户名" in value or "username" in value_lower or "your_username" in value_lower:
                        value = os.path.join(os.path.expanduser("~"), "Desktop")
                    elif len(value) == 2 and value[1] == ":":
                        value = value[0] + ":/"
                    elif value.startswith("/Users/") or value.startswith("/home/"):
                        value = os.path.join(os.path.expanduser("~"), "Desktop")
            processed_params[key] = value
        
        plan.steps.append(
            Step(
                type=StepType.TOOL_CALL,
                name=f"call_{tool_name}",
                tool_name=tool_name,
                input=processed_params,
            )
        )
        return plan
    
    def _plan_from_skill(self, skill_name: str, params: Dict[str, Any], intent: Intent) -> Plan:
        """Create a plan from a skill definition."""
        if self._skill_registry is None:
            return Plan(
                steps=[
                    Step(
                        type=StepType.TRANSFORM,
                        name="error",
                        template="Skill registry not available",
                        error="Skill registry not initialized",
                    )
                ],
                intent=intent,
                skill_name=skill_name,
            )
        
        skill = self._skill_registry.get(skill_name)
        if skill is None:
            return Plan(
                steps=[
                    Step(
                        type=StepType.TRANSFORM,
                        name="skill_not_found",
                        template=f"Skill '{skill_name}' not found",
                    )
                ],
                intent=intent,
                skill_name=skill_name,
            )

        if getattr(skill, "state", None) not in (SkillState.ENABLED, SkillState.RUNNING):
            definition = skill.get_definition() if hasattr(skill, "get_definition") else None
            availability = definition.metadata.get("availability", {}) if definition else {}
            blocked_reason = availability.get("reason") or "Skill is not currently enabled"
            return Plan(
                steps=[
                    Step(
                        type=StepType.TRANSFORM,
                        name="skill_blocked",
                        template=f"Skill '{skill_name}' is blocked: {blocked_reason}",
                    )
                ],
                intent=intent,
                skill_name=skill_name,
            )

        steps = self._convert_skill_to_steps(skill, params, source_skill_name=skill_name)
        return Plan(steps=steps, intent=intent, skill_name=skill_name)
    
    def _convert_skill_to_steps(self, skill: Any, params: Dict[str, Any], source_skill_name: Optional[str] = None) -> List[Step]:
        """Convert a skill definition to execution steps."""
        steps: List[Step] = []
        
        if hasattr(skill, "get_definition"):
            definition = skill.get_definition()
            actions = definition.actions
        elif hasattr(skill, "actions"):
            actions = skill.actions
        elif isinstance(skill, dict):
            actions = skill.get("actions", [])
        else:
            actions = []
        
        for i, action in enumerate(actions):
            action_type = action.get("type", "transform")
            
            if action_type == "tool_call":
                step = Step(
                    type=StepType.TOOL_CALL,
                    name=f"step_{i}_{action.get('tool', 'unknown')}",
                    tool_name=action.get("tool"),
                    source_skill_name=source_skill_name,
                    input=self._resolve_params(action.get("params", {}), params),
                    timeout=action.get("timeout", 30.0),
                )
            elif action_type == "skill_call":
                step = Step(
                    type=StepType.SKILL_CALL,
                    name=f"step_{i}_{action.get('skill', 'unknown')}",
                    skill_name=action.get("skill"),
                    source_skill_name=source_skill_name,
                    input=self._resolve_params(action.get("params", {}), params),
                )
            elif action_type == "condition":
                step = Step(
                    type=StepType.CONDITION,
                    name=f"step_{i}_condition",
                    condition=action.get("condition"),
                    source_skill_name=source_skill_name,
                    sub_steps=self._convert_actions_to_steps(action.get("then", []), params, source_skill_name),
                )
            elif action_type == "loop":
                step = Step(
                    type=StepType.LOOP,
                    name=f"step_{i}_loop",
                    loop_var=action.get("var", "item"),
                    loop_over=action.get("over"),
                    source_skill_name=source_skill_name,
                    sub_steps=self._convert_actions_to_steps(action.get("actions", []), params, source_skill_name),
                )
            elif action_type == "parallel":
                step = Step(
                    type=StepType.PARALLEL,
                    name=f"step_{i}_parallel",
                    source_skill_name=source_skill_name,
                    parallel_steps=self._convert_actions_to_steps(action.get("actions", []), params, source_skill_name),
                )
            else:
                step = Step(
                    type=StepType.TRANSFORM,
                    name=f"step_{i}_transform",
                    template=action.get("template", ""),
                    source_skill_name=source_skill_name,
                    input=params,
                )
            
            if action.get("retry_policy"):
                rp = action.get("retry_policy")
                step.retry_policy = RetryPolicy(
                    max_retries=rp.get("max_retries", 3) if isinstance(rp, dict) else 3,
                    delay=rp.get("delay", 1.0) if isinstance(rp, dict) else 1.0,
                    backoff=rp.get("backoff", 2.0) if isinstance(rp, dict) else 2.0,
                )
            
            if action.get("error_policy"):
                ep = action.get("error_policy")
                step.error_policy = ErrorPolicy(
                    on_failure=ep.get("on_failure", "abort") if isinstance(ep, dict) else "abort",
                    fallback_step=ep.get("fallback_step") if isinstance(ep, dict) else None,
                    fallback_message=ep.get("fallback_message") if isinstance(ep, dict) else None,
                )
            
            steps.append(step)
        
        return steps
    
    def _convert_actions_to_steps(
        self,
        actions: List[Dict],
        params: Dict[str, Any],
        source_skill_name: Optional[str] = None,
    ) -> List[Step]:
        """Convert a list of action definitions to steps."""
        return self._convert_skill_to_steps({"actions": actions}, params, source_skill_name=source_skill_name)
    
    def _resolve_params(self, template_params: Dict[str, Any], input_params: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve parameter templates with actual values."""
        resolved: Dict[str, Any] = {}
        
        for key, value in template_params.items():
            if isinstance(value, str) and value.startswith("$"):
                param_name = value[1:]
                resolved[key] = input_params.get(param_name, "")
            elif isinstance(value, str) and "{{" in value:
                from jinja2 import Template
                resolved[key] = Template(value).render(**input_params)
            else:
                resolved[key] = value
        
        return resolved
    
    def _plan_greeting(self, intent: Intent) -> Plan:
        """Create a greeting plan."""
        name = intent.params.get("name", "World")
        return Plan(
            steps=[
                Step(
                    type=StepType.TRANSFORM,
                    name="greeting",
                    template=f"Hello, {name}!",
                    input=intent.params,
                )
            ],
            intent=intent,
        )
    
    def _plan_help(self, intent: Intent) -> Plan:
        """Create a help plan."""
        help_text = """LocalClaw 可以帮你做这些事：
- 直接聊天提问：例如“你会干啥”“今天几号”“后天天冷不冷”
- 执行常规命令：`/cmd git status`
- 执行高风险原始 shell：`/shell git pull`（会进入审批）
- 调用 skill：`/skill_name key=value`
- 查看技能：`list skills`
- 查看状态：`status`
- 回显测试：`echo 你好`

常见自然语言示例：
- “执行命令 pytest”
- “总结这个目录里的文件”
- “查看 README.md”
- “帮我生成日报”

如果你不确定怎么说，直接用自然语言描述任务也可以。"""
        return Plan(
            steps=[
                Step(
                    type=StepType.TRANSFORM,
                    name="help",
                    template=help_text,
                )
            ],
            intent=intent,
        )
    
    def _plan_echo(self, intent: Intent) -> Plan:
        """Create an echo plan."""
        text = intent.params.get("text", "")
        return Plan(
            steps=[
                Step(
                    type=StepType.TRANSFORM,
                    name="echo",
                    template=text,
                    input=intent.params,
                )
            ],
            intent=intent,
        )
    
    def _plan_list_skills(self, intent: Intent) -> Plan:
        """Create a list skills plan."""
        return Plan(
            steps=[
                Step(
                    type=StepType.TOOL_CALL,
                    name="list_skills",
                    tool_name="list_skills",
                    input={},
                )
            ],
            intent=intent,
        )
    
    def _plan_status(self, intent: Intent) -> Plan:
        """Create a status plan."""
        return Plan(
            steps=[
                Step(
                    type=StepType.TOOL_CALL,
                    name="status",
                    tool_name="system_status",
                    input={},
                )
            ],
            intent=intent,
        )
    
    def _plan_unknown(self, intent: Intent) -> Plan:
        """Create a plan for unknown intents."""
        return Plan(
            steps=[
                Step(
                    type=StepType.TRANSFORM,
                    name="unknown",
                    template=f"Unknown intent: {intent.intent}. Type 'help' for available commands.",
                )
            ],
            intent=intent,
        )


def create_default_planner() -> Planner:
    """Create a planner with default configuration."""
    return Planner()

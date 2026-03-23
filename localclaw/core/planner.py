"""Planner module for generating execution plans."""

from typing import Any, Dict, List, Optional

from localclaw.core.models import (
    Context,
    ErrorPolicy,
    Intent,
    Plan,
    RetryPolicy,
    Step,
    StepType,
)


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
    
    async def plan(self, intent: Intent, context: Optional[Context] = None) -> Plan:
        """Generate an execution plan from an intent."""
        if intent.intent in self._intent_handlers:
            steps = await self._intent_handlers[intent.intent](intent, context)
            return Plan(steps=steps, intent=intent)
        
        if intent.intent.startswith("skill."):
            skill_name = intent.intent[6:]
            return self._plan_from_skill(skill_name, intent.params, intent)
        
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
        
        if intent.intent == "time_now":
            return self._plan_time_now(intent)
        
        return self._plan_unknown(intent)
    
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
        
        steps = self._convert_skill_to_steps(skill, params)
        return Plan(steps=steps, intent=intent, skill_name=skill_name)
    
    def _convert_skill_to_steps(self, skill: Any, params: Dict[str, Any]) -> List[Step]:
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
                    input=self._resolve_params(action.get("params", {}), params),
                    timeout=action.get("timeout", 30.0),
                )
            elif action_type == "skill_call":
                step = Step(
                    type=StepType.SKILL_CALL,
                    name=f"step_{i}_{action.get('skill', 'unknown')}",
                    skill_name=action.get("skill"),
                    input=self._resolve_params(action.get("params", {}), params),
                )
            elif action_type == "condition":
                step = Step(
                    type=StepType.CONDITION,
                    name=f"step_{i}_condition",
                    condition=action.get("condition"),
                    sub_steps=self._convert_actions_to_steps(action.get("then", []), params),
                )
            elif action_type == "loop":
                step = Step(
                    type=StepType.LOOP,
                    name=f"step_{i}_loop",
                    loop_var=action.get("var", "item"),
                    loop_over=action.get("over"),
                    sub_steps=self._convert_actions_to_steps(action.get("actions", []), params),
                )
            elif action_type == "parallel":
                step = Step(
                    type=StepType.PARALLEL,
                    name=f"step_{i}_parallel",
                    parallel_steps=self._convert_actions_to_steps(action.get("actions", []), params),
                )
            else:
                step = Step(
                    type=StepType.TRANSFORM,
                    name=f"step_{i}_transform",
                    template=action.get("template", ""),
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
    
    def _convert_actions_to_steps(self, actions: List[Dict], params: Dict[str, Any]) -> List[Step]:
        """Convert a list of action definitions to steps."""
        return self._convert_skill_to_steps({"actions": actions}, params)
    
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
        help_text = """LocalClaw Help:
- hello [name]: Say hello
- /skill_name [params]: Execute a skill
- help: Show this help
- list skills: List available skills
- status: Show system status
- echo <text>: Echo back text"""
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

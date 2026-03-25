"""Execution Engine - The core state machine for task execution."""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from jinja2 import Template

from localclaw.config.settings import Settings, get_settings
from localclaw.core.interpolation import resolve_interpolated_value
from localclaw.core.models import (
    AgentDecision,
    AgentDecisionMode,
    Context,
    ErrorType,
    ExecutionResult,
    Intent,
    Message,
    Plan,
    Step,
    StepStatus,
    StepType,
    Task,
    TaskState,
)
from localclaw.core.openclaw_runtime import OpenClawRuntime
from localclaw.core.parser import Parser, create_default_parser
from localclaw.core.planner import Planner, create_default_planner
from localclaw.core.verifier import VerificationDecision, Verifier, create_default_verifier
from localclaw.skills.registry.registry import SkillRegistry, get_skill_registry
from localclaw.tools.base import ToolRegistry, get_tool_registry


logger = logging.getLogger(__name__)


class EngineError(Exception):
    """Exception raised by the engine."""
    
    def __init__(self, message: str, error_type: ErrorType = ErrorType.SYSTEM_ERROR) -> None:
        super().__init__(message)
        self.error_type = error_type


class ExecutionEngine:
    """Core execution engine with state machine."""
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        parser: Optional[Parser] = None,
        planner: Optional[Planner] = None,
        verifier: Optional[Verifier] = None,
        tool_registry: Optional[ToolRegistry] = None,
        skill_registry: Optional[SkillRegistry] = None,
        openclaw_runtime: Optional[OpenClawRuntime] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._parser_override = parser is not None
        self._parser = parser or create_default_parser(
            llm_enabled=self._settings.llm_enabled,
            llm_parse_only=self._settings.llm_parse_only,
        )
        self._planner = planner or create_default_planner()
        self._tool_registry = tool_registry or get_tool_registry()
        self._skill_registry = skill_registry or get_skill_registry()
        self._openclaw_runtime = openclaw_runtime or OpenClawRuntime(
            skill_registry=self._skill_registry,
            tool_registry=self._tool_registry,
        )
        self._verifier = verifier or create_default_verifier(
            settings=self._settings,
            skill_registry=self._skill_registry,
        )
        
        self._planner.set_skill_registry(self._skill_registry)
        
        self._tasks: Dict[str, Task] = {}
        self._task_history: deque = deque(maxlen=1000)
        self._logger = logging.getLogger("localclaw.engine")
        
        self._on_step_start: Optional[Callable[[Step, Task], None]] = None
        self._on_step_complete: Optional[Callable[[Step, Task], None]] = None
        self._on_task_complete: Optional[Callable[[Task], None]] = None
        self._on_approval_required: Optional[Callable[[Step, Task], None]] = None
        
        self._state_file = self._settings.data_dir / "task_state.json"

    def _build_local_model_required_message(self) -> str:
        """Return the user-facing message shown when the local model is disabled."""
        return (
            "未启用本地大模型。LocalClaw 不再回退旧 parser 兼容链，"
            "请先安装并启用本地大模型，例如执行 `ollama pull qwen3:4b`、`ollama serve`，"
            "然后把 `LOCALCLAW_LLM_ENABLED=true`。"
        )
    
    def set_callbacks(
        self,
        on_step_start: Optional[Callable[[Step, Task], None]] = None,
        on_step_complete: Optional[Callable[[Step, Task], None]] = None,
        on_task_complete: Optional[Callable[[Task], None]] = None,
        on_approval_required: Optional[Callable[[Step, Task], None]] = None,
    ) -> None:
        """Set callback functions for events."""
        self._on_step_start = on_step_start
        self._on_step_complete = on_step_complete
        self._on_task_complete = on_task_complete
        self._on_approval_required = on_approval_required
    
    async def process_message(self, message: Message) -> Task:
        """Process a message and return the resulting task."""
        task = Task(
            message=message,
            user_id=message.user_id,
            channel=message.channel,
        )
        
        self._tasks[task.id] = task
        
        try:
            task.advance_state(TaskState.PARSED)
            if not self._settings.llm_enabled and not self._parser_override:
                raise EngineError(
                    self._build_local_model_required_message(),
                    ErrorType.PARSE_ERROR,
                )

            if self._settings.llm_enabled and self._settings.llm_parse_only and not self._parser_override:
                decision = await self._openclaw_runtime.decide(message, task.context)
                self._logger.info(
                    "Task %s: OpenClaw runtime decision mode '%s'",
                    task.id,
                    decision.mode.value,
                )

                if decision.mode == AgentDecisionMode.ANSWER and decision.answer:
                    self._complete_with_direct_answer(task, decision)
                else:
                    intent = decision.to_intent()
                    if intent is None:
                        self._logger.info(
                            "Task %s: OpenClaw runtime returned no executable action; retrying planner understanding",
                            task.id,
                        )
                        plan = await self._planner.plan_from_message(
                            message,
                            task.context,
                            allow_parser_fallback=False,
                        )
                    else:
                        task.intent = intent
                        plan = await self._planner.plan(intent, task.context)
                        if plan.intent is None:
                            plan.intent = intent
                    resolved_intent = plan.intent.intent if plan.intent else "unknown"
                    if resolved_intent == "unknown":
                        self._logger.info(
                            "Task %s: Planner still returned unknown; trying local-model chat fallback",
                            task.id,
                        )
                        fallback_decision = await self._openclaw_runtime.fallback_to_chat_answer(
                            message,
                            task.context,
                        )
                        if fallback_decision.mode == AgentDecisionMode.ANSWER and fallback_decision.answer:
                            self._complete_with_direct_answer(task, fallback_decision)
                            plan = None
                        else:
                            task.plan = plan
                            task.intent = plan.intent
                    else:
                        task.plan = plan
                        task.intent = plan.intent
                    self._logger.info(
                        "Task %s: Planned from local model with intent '%s'",
                        task.id,
                        resolved_intent,
                    )
            else:
                intent = await self._parser.parse(message)
                task.intent = intent
                self._logger.info(f"Task {task.id}: Parsed intent '{intent.intent}'")
                plan = await self._planner.plan(intent, task.context)
                task.plan = plan

            if task.state != TaskState.COMPLETED:
                if task.intent is not None:
                    task.context.inputs.update(task.intent.params)

                task.advance_state(TaskState.PLANNED)
                if task.plan is not None:
                    self._logger.info(f"Task {task.id}: Created plan with {len(task.plan.steps)} steps")

                task.advance_state(TaskState.RUNNING)
                await self._execute_plan(task)
            
        except Exception as e:
            self._logger.error(f"Task {task.id} failed: {e}")
            if isinstance(e, EngineError):
                task.error_type = e.error_type
            else:
                task.error_type = ErrorType.SYSTEM_ERROR
            task.error = str(e)
            task.result = ExecutionResult.from_error(task.error, task.error_type)
            task.advance_state(TaskState.FAILED)

        if task.state not in (TaskState.VERIFYING, TaskState.PAUSED):
            self._task_history.append(task)
            if task.id in self._tasks:
                del self._tasks[task.id]

        if self._on_task_complete and task.state not in (TaskState.VERIFYING, TaskState.PAUSED):
            self._on_task_complete(task)
        
        return task

    def _complete_with_direct_answer(self, task: Task, decision: AgentDecision) -> None:
        """Finish a task immediately with the model's direct answer."""

        answer = (decision.answer or "").strip()
        if not answer:
            raise EngineError("Local model returned an empty direct answer", ErrorType.PARSE_ERROR)

        task.intent = Intent(
            intent="answer",
            params={"answer": answer},
            confidence=decision.confidence,
            source=decision.source,
            raw_message=decision.raw_message,
        )
        task.plan = Plan(intent=task.intent, steps=[])
        task.context.inputs.update(task.intent.params)
        task.advance_state(TaskState.PLANNED)
        task.advance_state(TaskState.RUNNING)
        task.result = ExecutionResult.success(
            data={"result": answer, "message": answer},
        )
        task.advance_state(TaskState.COMPLETED)
    
    async def _execute_plan(self, task: Task, start_index: int = 0) -> None:
        """Execute all steps in a plan."""
        if not task.plan:
            task.result = ExecutionResult.from_error("No plan to execute")
            task.advance_state(TaskState.FAILED)
            return
        
        for step_index in range(start_index, len(task.plan.steps)):
            task.current_step_index = step_index
            step = task.plan.steps[step_index]
            
            try:
                result = await self._execute_step(step, task)

                if step.status == StepStatus.PENDING and result.error_type == ErrorType.PERMISSION_ERROR:
                    task.result = result
                    task.error = None
                    task.error_type = None
                    task.advance_state(TaskState.VERIFYING)
                    return
                
                if result.status == "error":
                    if step.error_policy.on_failure == "abort":
                        task.result = result
                        task.error = result.message
                        task.error_type = result.error_type
                        task.advance_state(TaskState.FAILED)
                        return
                    elif step.error_policy.on_failure == "skip":
                        step.status = StepStatus.SKIPPED
                        continue
                
                task.context.set_step_output(step.id, result.data)
                
            except Exception as e:
                self._logger.error(f"Step {step.id} failed: {e}")
                step.status = StepStatus.FAILED
                step.error = str(e)
                
                if step.error_policy.on_failure == "abort":
                    task.result = ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)
                    task.error = str(e)
                    task.advance_state(TaskState.FAILED)
                    return
        
        task.result = ExecutionResult.success(
            message="Task completed successfully",
            data=self._collect_results(task),
        )
        task.advance_state(TaskState.COMPLETED)
    
    async def _execute_step(self, step: Step, task: Task) -> ExecutionResult:
        """Execute a single step with retry logic."""
        step.status = StepStatus.RUNNING
        step.started_at = datetime.now()
        
        if self._on_step_start:
            self._on_step_start(step, task)
        
        verification = await self._verifier.verify_before_execution(step, task.context)
        
        if verification.decision == VerificationDecision.REJECT:
            step.status = StepStatus.FAILED
            step.error = verification.message
            return ExecutionResult.from_error(verification.message, ErrorType.PERMISSION_ERROR)
        
        if verification.decision == VerificationDecision.ASK_HUMAN:
            if self._on_approval_required:
                self._on_approval_required(step, task)
            step.status = StepStatus.PENDING
            return ExecutionResult.from_error("Waiting for approval", ErrorType.PERMISSION_ERROR)
        
        result = await self._execute_step_with_retry(step, task)
        
        step.completed_at = datetime.now()
        
        if result.status == "success":
            post_verification = await self._verifier.verify_after_execution(step, result, task.context)
            if post_verification.decision == VerificationDecision.REJECT:
                step.status = StepStatus.FAILED
                step.error = post_verification.message
                return ExecutionResult.from_error(post_verification.message, ErrorType.VALIDATION_ERROR)
        
        if self._on_step_complete:
            self._on_step_complete(step, task)
        
        return result
    
    async def _execute_step_with_retry(self, step: Step, task: Task) -> ExecutionResult:
        """Execute a step with retry logic."""
        max_retries = step.retry_policy.max_retries
        delay = step.retry_policy.delay
        
        for attempt in range(max_retries + 1):
            try:
                result = await self._execute_step_inner(step, task)
                
                if result.status == "success":
                    step.status = StepStatus.COMPLETED
                    return result
                
                if result.error_type not in step.retry_policy.retry_on:
                    return result
                
                if attempt < max_retries:
                    self._logger.info(f"Step {step.id} retry {attempt + 1}/{max_retries}")
                    step.retry_count = attempt + 1
                    await asyncio.sleep(delay)
                    delay *= step.retry_policy.backoff
                else:
                    return result
                    
            except Exception as e:
                self._logger.error(f"Step {step.id} exception on attempt {attempt}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= step.retry_policy.backoff
                else:
                    step.status = StepStatus.FAILED
                    step.error = str(e)
                    return ExecutionResult.from_error(str(e), ErrorType.SYSTEM_ERROR)
        
        return ExecutionResult.from_error("Max retries exceeded", ErrorType.TOOL_ERROR)
    
    async def _execute_step_inner(self, step: Step, task: Task) -> ExecutionResult:
        """Execute a step based on its type."""
        if step.type == StepType.TOOL_CALL:
            return await self._execute_tool_step(step, task)
        elif step.type == StepType.SKILL_CALL:
            return await self._execute_skill_step(step, task)
        elif step.type == StepType.AGENT_CALL:
            return await self._execute_agent_step(step, task)
        elif step.type == StepType.TRANSFORM:
            return await self._execute_transform_step(step, task)
        elif step.type == StepType.CONDITION:
            return await self._execute_condition_step(step, task)
        elif step.type == StepType.LOOP:
            return await self._execute_loop_step(step, task)
        elif step.type == StepType.PARALLEL:
            return await self._execute_parallel_step(step, task)
        else:
            return ExecutionResult.from_error(f"Unknown step type: {step.type}", ErrorType.SYSTEM_ERROR)
    
    async def _execute_tool_step(self, step: Step, task: Task) -> ExecutionResult:
        """Execute a tool call step."""
        tool_name = step.tool_name
        if not tool_name:
            return ExecutionResult.from_error("No tool specified", ErrorType.VALIDATION_ERROR)
        
        params = self._resolve_params(step.input, task.context)
        
        return await self._tool_registry.execute(tool_name, **params)
    
    async def _execute_skill_step(self, step: Step, task: Task) -> ExecutionResult:
        """Execute a skill call step."""
        skill_name = step.skill_name
        if not skill_name:
            return ExecutionResult.from_error("No skill specified", ErrorType.VALIDATION_ERROR)
        
        skill = self._skill_registry.get(skill_name)
        if skill is None:
            return ExecutionResult.from_error(f"Skill not found: {skill_name}", ErrorType.VALIDATION_ERROR)

        from localclaw.skills.base import SkillState

        if getattr(skill, "state", None) not in (SkillState.ENABLED, SkillState.RUNNING):
            info = skill.get_definition().metadata.get("availability", {})
            reason = info.get("reason") or "Skill is not enabled"
            return ExecutionResult.from_error(
                f"Skill '{skill_name}' is blocked: {reason}",
                ErrorType.PERMISSION_ERROR,
            )
        
        params = self._resolve_params(step.input, task.context)
        
        return await skill.execute(task.context, **params)
    
    async def _execute_agent_step(self, step: Step, task: Task) -> ExecutionResult:
        """Execute an agent call step."""
        from localclaw.agents.manager import get_agent_manager
        
        agent_name = step.agent_name
        if not agent_name:
            return ExecutionResult.from_error("No agent specified", ErrorType.VALIDATION_ERROR)
        
        skill_name = step.skill_name
        if not skill_name:
            return ExecutionResult.from_error("No skill specified for agent call", ErrorType.VALIDATION_ERROR)
        
        params = self._resolve_params(step.input, task.context)
        
        manager = get_agent_manager()
        return await manager.call_agent(agent_name, skill_name, params, task.context)
    
    async def _execute_transform_step(self, step: Step, task: Task) -> ExecutionResult:
        """Execute a transform step."""
        if not step.template:
            return ExecutionResult.from_error("No template specified", ErrorType.VALIDATION_ERROR)
        
        template_vars = self._get_template_vars(task.context)
        template_vars.update(step.input)
        
        try:
            template = Template(step.template)
            result = template.render(**template_vars)
            return ExecutionResult.success(data={"result": result, "message": result})
        except Exception as e:
            return ExecutionResult.from_error(f"Template error: {e}", ErrorType.SYSTEM_ERROR)
    
    async def _execute_condition_step(self, step: Step, task: Task) -> ExecutionResult:
        """Execute a conditional step."""
        if not step.condition:
            return ExecutionResult.from_error("No condition specified", ErrorType.VALIDATION_ERROR)
        
        template_vars = self._get_template_vars(task.context)
        
        try:
            condition_result = bool(eval(step.condition, {"__builtins__": {}}, template_vars))
        except Exception as e:
            return ExecutionResult.from_error(f"Condition evaluation error: {e}", ErrorType.SYSTEM_ERROR)
        
        if condition_result:
            sub_results = []
            for sub_step in step.sub_steps:
                result = await self._execute_step(sub_step, task)
                sub_results.append(result)
                if result.status == "error":
                    return result

            if len(sub_results) == 1:
                return sub_results[0]

            return ExecutionResult.success(
                data={
                    "condition_result": True,
                    "sub_results": [result.data for result in sub_results],
                }
            )

        return ExecutionResult.success(data={"condition_result": False})
    
    async def _execute_loop_step(self, step: Step, task: Task) -> ExecutionResult:
        """Execute a loop step."""
        if not step.loop_over:
            return ExecutionResult.from_error("No loop target specified", ErrorType.VALIDATION_ERROR)
        
        template_vars = self._get_template_vars(task.context)
        
        try:
            items = eval(step.loop_over, {"__builtins__": {}}, template_vars)
        except Exception as e:
            return ExecutionResult.from_error(f"Loop target evaluation error: {e}", ErrorType.SYSTEM_ERROR)
        
        results = []
        for item in items:
            task.context.set_variable(step.loop_var or "item", item)
            
            for sub_step in step.sub_steps:
                result = await self._execute_step(sub_step, task)
                results.append(result)
                if result.status == "error":
                    return result
        
        return ExecutionResult.success(data={"results": results})
    
    async def _execute_parallel_step(self, step: Step, task: Task) -> ExecutionResult:
        """Execute parallel steps."""
        tasks = [self._execute_step(s, task) for s in step.parallel_steps]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                results[i] = ExecutionResult.from_error(str(result), ErrorType.SYSTEM_ERROR)
        
        errors = [r for r in results if isinstance(r, ExecutionResult) and r.status == "error"]
        if errors:
            return errors[0]
        
        return ExecutionResult.success(data={"results": [r.data for r in results if isinstance(r, ExecutionResult)]})
    
    def _resolve_params(self, params: Dict[str, Any], context: Context) -> Dict[str, Any]:
        """Resolve parameter values from context."""
        template_vars = self._get_template_vars(context)
        return {
            key: resolve_interpolated_value(value, template_vars)
            for key, value in params.items()
        }
    
    def _get_template_vars(self, context: Context) -> Dict[str, Any]:
        """Get all variables available for templates."""
        vars_dict: Dict[str, Any] = {}
        vars_dict.update(context.inputs)
        vars_dict.update(context.variables)
        vars_dict.update(context.memory)
        
        for step_id, output in context.step_outputs.items():
            vars_dict[f"step_{step_id}"] = output
        
        return vars_dict
    
    def _collect_results(self, task: Task) -> Dict[str, Any]:
        """Collect results from all completed steps."""
        results: Dict[str, Any] = {}
        
        if task.plan:
            for step in task.plan.steps:
                if step.status == StepStatus.COMPLETED:
                    results[step.id] = task.context.get_step_output(step.id)
        
        return results
    
    def approve_step(self, task_id: str, step_id: str) -> bool:
        """Approve a pending step."""
        if task_id not in self._tasks:
            return False
        
        return self._verifier.approve_step(step_id)

    async def approve_and_resume_step(self, task_id: str, step_id: str) -> Optional[Task]:
        """Approve a pending step and continue task execution."""
        approved = self.approve_step(task_id, step_id)
        if not approved:
            return None
        return await self.resume_task(task_id)

    async def resume_task(self, task_id: str) -> Optional[Task]:
        """Resume a task that is waiting for approval."""
        task = self._tasks.get(task_id)
        if task is None:
            return None

        if task.state not in (TaskState.VERIFYING, TaskState.PAUSED):
            return task

        task.advance_state(TaskState.RUNNING)
        await self._execute_plan(task, start_index=task.current_step_index)

        if task.state not in (TaskState.VERIFYING, TaskState.PAUSED):
            self._task_history.append(task)
            if task.id in self._tasks:
                del self._tasks[task.id]
            if self._on_task_complete:
                self._on_task_complete(task)

        return task
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)
    
    def get_running_tasks(self) -> List[Task]:
        """Get all running tasks."""
        return [t for t in self._tasks.values() if t.state == TaskState.RUNNING]

    def get_active_tasks(self) -> List[Task]:
        """Get all tasks that are still kept in active memory."""
        return list(self._tasks.values())
    
    def get_task_history(self, limit: int = 100) -> List[Task]:
        """Get task history."""
        return list(self._task_history)[-limit:]
    
    async def save_state(self) -> None:
        """Save current state to file."""
        state = {
            "tasks": {tid: t.model_dump() for tid, t in self._tasks.items()},
            "history": [t.model_dump() for t in list(self._task_history)[-100:]],
        }
        
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, default=str, indent=2)
    
    async def load_state(self) -> None:
        """Load state from file."""
        if not self._state_file.exists():
            return
        
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            
            for tid, tdata in state.get("tasks", {}).items():
                self._tasks[tid] = Task(**tdata)
            
            for tdata in state.get("history", []):
                self._task_history.append(Task(**tdata))
                
        except Exception as e:
            self._logger.error(f"Failed to load state: {e}")


_engine: Optional[ExecutionEngine] = None


def get_engine() -> ExecutionEngine:
    """Get the global engine instance."""
    global _engine
    if _engine is None:
        _engine = ExecutionEngine()
    return _engine


async def process_message(message: Message) -> Task:
    """Process a message using the global engine."""
    return await get_engine().process_message(message)

"""Execution Engine - The core state machine for task execution."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from jinja2 import Template

from localclaw.config.settings import Settings, get_settings
from localclaw.core.models import (
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
from localclaw.core.parser import Parser, create_default_parser
from localclaw.core.planner import Planner, create_default_planner
from localclaw.core.verifier import VerificationDecision, Verifier, create_default_verifier
from localclaw.skills.registry import SkillRegistry, get_skill_registry
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
    ) -> None:
        self._settings = settings or get_settings()
        self._parser = parser or create_default_parser()
        self._planner = planner or create_default_planner()
        self._verifier = verifier or create_default_verifier()
        self._tool_registry = tool_registry or get_tool_registry()
        self._skill_registry = skill_registry or get_skill_registry()
        
        self._planner.set_skill_registry(self._skill_registry)
        
        self._tasks: Dict[str, Task] = {}
        self._task_history: List[Task] = []
        self._logger = logging.getLogger("localclaw.engine")
        
        self._on_step_start: Optional[Callable[[Step, Task], None]] = None
        self._on_step_complete: Optional[Callable[[Step, Task], None]] = None
        self._on_task_complete: Optional[Callable[[Task], None]] = None
        self._on_approval_required: Optional[Callable[[Step, Task], None]] = None
        
        self._state_file = self._settings.data_dir / "task_state.json"
    
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
            intent = await self._parser.parse(message)
            task.intent = intent
            self._logger.info(f"Task {task.id}: Parsed intent '{intent.intent}'")
            
            task.advance_state(TaskState.PLANNED)
            plan = await self._planner.plan(intent, task.context)
            task.plan = plan
            self._logger.info(f"Task {task.id}: Created plan with {len(plan.steps)} steps")
            
            task.advance_state(TaskState.RUNNING)
            await self._execute_plan(task)
            
        except Exception as e:
            self._logger.error(f"Task {task.id} failed: {e}")
            task.error = str(e)
            task.error_type = ErrorType.SYSTEM_ERROR
            task.advance_state(TaskState.FAILED)
        
        self._task_history.append(task)
        if task.id in self._tasks:
            del self._tasks[task.id]
        
        if self._on_task_complete:
            self._on_task_complete(task)
        
        return task
    
    async def _execute_plan(self, task: Task) -> None:
        """Execute all steps in a plan."""
        if not task.plan:
            task.result = ExecutionResult.from_error("No plan to execute")
            task.advance_state(TaskState.FAILED)
            return
        
        for step_index in range(len(task.plan.steps)):
            task.current_step_index = step_index
            step = task.plan.steps[step_index]
            
            try:
                result = await self._execute_step(step, task)
                
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
            for sub_step in step.sub_steps:
                result = await self._execute_step(sub_step, task)
                if result.status == "error":
                    return result
        
        return ExecutionResult.success(data={"condition_result": condition_result})
    
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
        resolved: Dict[str, Any] = {}
        
        for key, value in params.items():
            if isinstance(value, str):
                if value.startswith("$"):
                    var_name = value[1:]
                    resolved[key] = context.get_variable(var_name) or context.inputs.get(var_name, "")
                elif "{{" in value:
                    template = Template(value)
                    template_vars = self._get_template_vars(context)
                    resolved[key] = template.render(**template_vars)
                else:
                    resolved[key] = value
            else:
                resolved[key] = value
        
        return resolved
    
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
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)
    
    def get_running_tasks(self) -> List[Task]:
        """Get all running tasks."""
        return [t for t in self._tasks.values() if t.state == TaskState.RUNNING]
    
    def get_task_history(self, limit: int = 100) -> List[Task]:
        """Get task history."""
        return self._task_history[-limit:]
    
    async def save_state(self) -> None:
        """Save current state to file."""
        state = {
            "tasks": {tid: t.model_dump() for tid, t in self._tasks.items()},
            "history": [t.model_dump() for t in self._task_history[-100:]],
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

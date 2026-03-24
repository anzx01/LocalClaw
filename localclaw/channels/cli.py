"""CLI channel for LocalClaw."""

import asyncio
import logging
import sys
from typing import Optional

import click

from localclaw.config.settings import get_settings
from localclaw.core.engine import ExecutionEngine, get_engine
from localclaw.core.models import ExecutionResult, Message, Task, TaskState
from localclaw.llm.provider import initialize_llm_provider
from localclaw.security.audit import configure_audit_logger
from localclaw.skills.loader import load_skills_from_settings, register_builtin_skills
from localclaw.tools.base import Tool, get_tool_registry
from localclaw.tools.clawhub_tool import register_clawhub_tools
from localclaw.tools.file_tool import register_file_tools
from localclaw.tools.http_tool import register_http_tools
from localclaw.tools.shell_tool import register_shell_tools


logger = logging.getLogger(__name__)


class SystemStatusTool(Tool):
    """Tool for getting system status."""

    name = "system_status"
    description = "Get system status information"
    inputs = {}
    outputs = {"status": "string", "version": "string"}

    async def execute(self, **kwargs) -> ExecutionResult:
        from localclaw import __version__

        settings = get_settings()
        return ExecutionResult.success(
            message="System status",
            data={
                "status": "running",
                "version": __version__,
                "mode": settings.mode.value,
                "model_provider": settings.model_provider.value,
                "model_name": settings.model_name,
            },
        )


class ListSkillsTool(Tool):
    """Tool for listing skills."""

    name = "list_skills"
    description = "List all available skills"
    inputs = {}
    outputs = {"skills": "list"}

    async def execute(self, **kwargs) -> ExecutionResult:
        from localclaw.skills.registry import get_skill_registry

        skill_info = get_skill_registry().get_all_info()
        available = [skill["name"] for skill in skill_info if skill.get("availability") == "available"]
        blocked = [skill["name"] for skill in skill_info if skill.get("availability") == "blocked"]

        response = "当前可用能力：\n"
        response += "- 问候、回显、日期和天气查询\n"
        response += "- 文件、HTTP 和命令行工具（高风险操作需要审批）\n"
        response += f"- 已加载技能 {len(skill_info)} 个，可用 {len(available)} 个"
        if blocked:
            response += f"，受限 {len(blocked)} 个"

        return ExecutionResult.success(
            message="已列出技能",
            data={"skills": skill_info, "result": response},
        )


def setup_logging(level: str = "INFO") -> None:
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def initialize_system() -> ExecutionEngine:
    """Initialize the LocalClaw system."""
    settings = get_settings()
    settings.ensure_directories()

    configure_audit_logger(settings.audit_log)

    if settings.llm_enabled:
        provider = initialize_llm_provider(settings)
        logger.info(
            "LLM provider initialized: %s (%s)",
            provider.get_config().provider_type.value,
            provider.get_config().model,
        )

    tool_registry = get_tool_registry()
    tool_registry.register(SystemStatusTool())
    tool_registry.register(ListSkillsTool())
    register_file_tools()
    register_http_tools()
    register_shell_tools()
    register_clawhub_tools()

    register_builtin_skills()

    load_skills_from_settings(settings)

    from localclaw.core.parser import create_default_parser
    from localclaw.core.verifier import create_default_verifier

    parser = create_default_parser(
        llm_enabled=settings.llm_enabled,
        llm_parse_only=settings.llm_parse_only,
    )
    from localclaw.skills.registry import get_skill_registry
    verifier = create_default_verifier(settings=settings, skill_registry=get_skill_registry())
    verifier.set_auto_approve_low(True)
    verifier.set_require_confirmation_high(True)

    engine = ExecutionEngine(
        settings=settings,
        parser=parser,
        verifier=verifier,
    )

    from localclaw.core.engine import _engine
    import localclaw.core.engine as engine_module

    engine_module._engine = engine
    return engine


def format_task_result(task: Task) -> str:
    """Format a task result for display."""
    if task.state == TaskState.COMPLETED:
        result_data = task.result.data
        if "result" not in result_data and "message" not in result_data and len(result_data) == 1:
            first_value = next(iter(result_data.values()))
            if isinstance(first_value, dict):
                result_data = first_value
        if "result" in result_data:
            return result_data["result"]
        if "message" in result_data:
            return result_data["message"]
        if "content" in result_data:
            return result_data["content"]
        return str(result_data)
    if task.state == TaskState.FAILED:
        return f"Error: {task.error}"
    if task.state == TaskState.VERIFYING:
        return task.result.message if task.result else "Waiting for approval"
    return f"Task state: {task.state.value}"


async def process_single_message(message: str, user_id: str = "cli") -> str:
    """Process a single message and return the result."""
    engine = get_engine()

    msg = Message(
        content=message,
        user_id=user_id,
        channel="cli",
    )

    task = await engine.process_message(msg)
    return format_task_result(task)


def run_interactive(user_id: str = "cli") -> None:
    """Run interactive CLI mode."""
    click.echo("LocalClaw Interactive Mode")
    click.echo("Type 'help' for available commands, 'exit' to quit.\n")

    engine = get_engine()

    while True:
        try:
            message = click.prompt("", type=str, default="").strip()

            if not message:
                continue

            if message.lower() in ("exit", "quit", "q"):
                click.echo("Goodbye!")
                break

            if message.lower() == "clear":
                click.clear()
                continue

            msg = Message(
                content=message,
                user_id=user_id,
                channel="cli",
            )

            task = asyncio.run(engine.process_message(msg))
            click.echo(f"\n{format_task_result(task)}\n")

        except KeyboardInterrupt:
            click.echo("\nGoodbye!")
            break
        except Exception as exc:
            click.echo(f"Error: {exc}\n")


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--config", type=click.Path(), help="Path to config file")
def cli(debug: bool, config: Optional[str]) -> None:
    """LocalClaw - Local-first Agent Runtime System."""
    log_level = "DEBUG" if debug else "INFO"
    setup_logging(log_level)

    if config:
        logger.info("Loading config from: %s", config)


@cli.command()
@click.argument("message", required=False)
@click.option("--user", "-u", default="cli", help="User ID")
@click.option("--interactive", "-i", is_flag=True, help="Run in interactive mode")
def run(message: Optional[str], user: str, interactive: bool) -> None:
    """Run LocalClaw with a message or in interactive mode."""
    initialize_system()

    if interactive or message is None:
        run_interactive(user)
    else:
        result = asyncio.run(process_single_message(message, user))
        click.echo(result)


@cli.command()
@click.option("--host", "-h", default="127.0.0.1", help="Server host")
@click.option("--port", "-p", default=8000, help="Server port")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the LocalClaw web server."""
    import uvicorn

    initialize_system()

    click.echo(f"Starting LocalClaw server at http://{host}:{port}")

    uvicorn.run(
        "localclaw.channels.web:create_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
    )


@cli.command()
def skills() -> None:
    """List all registered skills."""
    from localclaw.skills.registry import get_skill_registry

    initialize_system()

    registry = get_skill_registry()
    skill_list = registry.list_skills()

    if not skill_list:
        click.echo("No skills registered.")
        return

    click.echo("Registered Skills:\n")
    for name in skill_list:
        info = registry.get_skill_info(name)
        if info:
            line = f"  {name} (v{info['version']}) - {info['description']}"
            if info.get("availability") == "blocked":
                reason = info.get("availability_details", {}).get("reason") or "blocked"
                line += f" [blocked: {reason}]"
            click.echo(line)


@cli.command()
def tools() -> None:
    """List all registered tools."""
    from localclaw.tools.base import get_tool_registry

    initialize_system()

    registry = get_tool_registry()
    tool_list = registry.list_tools()

    if not tool_list:
        click.echo("No tools registered.")
        return

    click.echo("Registered Tools:\n")
    for name in tool_list:
        tool = registry.get(name)
        if tool:
            click.echo(f"  {name} - {tool.description}")


@cli.command()
@click.argument("skill_name")
@click.option("--params", "-p", help="JSON parameters for the skill")
def execute(skill_name: str, params: Optional[str]) -> None:
    """Execute a skill directly."""
    import json

    from localclaw.skills.registry import get_skill_registry

    initialize_system()

    registry = get_skill_registry()
    skill = registry.get(skill_name)

    if not skill:
        click.echo(f"Skill not found: {skill_name}")
        return

    if skill.state.value == "stopped":
        info = registry.get_skill_info(skill_name) or {}
        reason = info.get("availability_details", {}).get("reason") or "Skill is blocked"
        click.echo(reason)
        return

    if params:
        try:
            json.loads(params)
        except json.JSONDecodeError:
            click.echo("Invalid JSON parameters")
            return

    message = Message(
        content=f"/{skill_name}",
        user_id="cli",
        channel="cli",
    )

    engine = get_engine()
    task = asyncio.run(engine.process_message(message))

    click.echo(format_task_result(task))


@cli.command()
def config() -> None:
    """Show current configuration."""
    settings = get_settings()

    click.echo("Current Configuration:\n")
    click.echo(f"  Mode: {settings.mode.value}")
    click.echo(f"  LLM Enabled: {settings.llm_enabled}")
    click.echo(f"  Model Provider: {settings.model_provider.value}")
    click.echo(f"  Model Name: {settings.model_name}")
    click.echo(f"  Model Base URL: {settings.get_model_base_url()}")
    click.echo(f"  Bundled Skills: {settings.skills_dir}")
    click.echo(f"  Managed Skills: {settings.managed_skills_dir}")
    click.echo(f"  Workspace Skills: {settings.workspace_skills_dir}")
    click.echo(f"  Data Directory: {settings.data_dir}")
    click.echo(f"  Memory Database: {settings.memory_db}")
    click.echo(f"  Audit Log: {settings.audit_log}")
    click.echo(f"  Log Level: {settings.log_level}")
    click.echo(f"  Server: {settings.server_host}:{settings.server_port}")


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()

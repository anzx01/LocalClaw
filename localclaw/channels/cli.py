"""CLI channel for LocalClaw."""

import asyncio
import logging
import sys
from typing import Optional

import click

from localclaw.channels.result_formatter import format_task_for_chat
from localclaw.config.settings import get_settings
from localclaw.core.bootstrap import initialize_system
from localclaw.core.engine import get_engine
from localclaw.core.models import ExecutionResult, Message, Task, TaskState


logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def format_task_result(task: Task) -> str:
    """Format a task result for display."""
    if task.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.VERIFYING):
        return format_task_for_chat(task)
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
    click.echo(f"  ClawHub Base URL: {settings.get_clawhub_base_url()}")
    click.echo(f"  ClawHub Token Configured: {bool(settings.get_clawhub_token())}")
    click.echo(f"  Configured Skill Dirs: {[str(path) for path in settings.extra_skill_dirs]}")
    click.echo(f"  Managed Skills: {settings.managed_skills_dir}")
    click.echo(f"  Marketplace Catalog: {settings.bundled_skill_catalog_dir}")
    click.echo(f"  Data Directory: {settings.data_dir}")
    click.echo(f"  Memory Database: {settings.memory_db}")
    click.echo(f"  Audit Log: {settings.audit_log}")
    click.echo(f"  Progress Log: {settings.progress_log}")
    click.echo(f"  Log Level: {settings.log_level}")
    click.echo(f"  Server: {settings.server_host}:{settings.server_port}")


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()

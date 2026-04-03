#!/usr/bin/env python
"""Test invoice-summary with real desktop folders."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from localclaw.core.bootstrap import initialize_system
from localclaw.core.engine import get_engine
from localclaw.core.models import Message


async def main():
    """Test invoice-summary with real folders."""
    print("Initializing system...")
    initialize_system()

    engine = get_engine()

    # Use DSL command with desktop paths
    desktop = Path.home() / "Desktop"
    dir1 = str(desktop / "长沙出差发票")
    dir2 = str(desktop / "北京出差发票")

    msg = Message(
        content=f"/invoice-summary dir1={dir1} dir2={dir2}",
        user_id="test",
        channel="cli",
    )

    print(f"\nProcessing message: {msg.content}")
    print("=" * 80)

    task = await engine.process_message(msg)

    print(f"\nTask ID: {task.id}")
    print(f"State: {task.state.value}")
    print(f"Intent: {task.intent.intent if task.intent else 'None'}")
    print(f"Plan steps: {len(task.plan.steps) if task.plan else 0}")

    if task.plan and task.plan.steps:
        print("\nPlan steps:")
        for i, step in enumerate(task.plan.steps, 1):
            print(f"  {i}. {step.type.value}: {step.name}")
            if step.tool_name:
                print(f"     Tool: {step.tool_name}")

    if task.result:
        print(f"\nResult status: {task.result.status}")
        if task.result.message:
            print(f"\nResult message:")
            print(task.result.message)

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

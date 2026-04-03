#!/usr/bin/env python
"""Test script for invoice-summary skill."""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from localclaw.core.bootstrap import initialize_system
from localclaw.core.engine import get_engine
from localclaw.core.models import Message


async def main():
    """Test invoice-summary skill."""
    print("Initializing system...")
    initialize_system()

    engine = get_engine()

    # Test DSL command
    msg = Message(
        content="/invoice-summary dir1=test_invoices/beijing dir2=test_invoices/changsha",
        user_id="test",
        channel="cli",
    )

    print(f"\nProcessing message: {msg.content}")
    print("=" * 60)

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
            print(f"Result message:\n{task.result.message[:500]}")

    print("\n" + "=" * 60)
    print("Test completed")


if __name__ == "__main__":
    asyncio.run(main())

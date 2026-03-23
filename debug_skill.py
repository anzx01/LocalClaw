"""Debug script to test the day_of_week skill."""

import asyncio
from localclaw.core.engine import get_engine
from localclaw.core.models import Message
from localclaw.channels.web import initialize_system

async def test_skill():
    """Test the day_of_week skill."""
    # Initialize the system
    initialize_system()
    
    engine = get_engine()
    
    # Test message
    message = Message(content="今天星期几", user_id="test", channel="test")
    
    # Process the message
    task = await engine.process_message(message)
    
    # Print the results
    print(f"Task ID: {task.id}")
    print(f"Status: {task.state.value}")
    print(f"Intent: {task.intent.intent if task.intent else 'None'}")
    print(f"Plan steps: {len(task.plan.steps) if task.plan else 0}")
    
    if task.plan:
        for i, step in enumerate(task.plan.steps):
            print(f"Step {i}: {step.type.value} - {step.name}")
            print(f"  Template: {step.template}")
            print(f"  Status: {step.status.value}")
            print(f"  Error: {step.error}")
    
    print(f"Result: {task.result}")
    print(f"Result data: {task.result.data if task.result else 'None'}")
    print(f"Context step outputs: {task.context.step_outputs}")

if __name__ == "__main__":
    asyncio.run(test_skill())

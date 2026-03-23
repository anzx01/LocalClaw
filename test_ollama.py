"""Test script to directly test the Ollama client."""

import asyncio
from localclaw.llm.ollama import initialize_ollama, OllamaConfig, get_ollama_client

async def test_ollama():
    """Test the Ollama client."""
    # Initialize Ollama
    ollama_config = OllamaConfig(
        base_url="http://localhost:11434",
        model="gemma3:4b",
    )
    initialize_ollama(ollama_config)
    
    # Get the client
    client = get_ollama_client()
    
    # Test prompt
    prompt = """Analyze the following user message and extract the intent and parameters.
Return a JSON object with:
- intent: the action the user wants to perform
- params: any parameters mentioned

User message: 今天星期几

JSON response:"""
    
    # Generate response
    try:
        response = await client.generate(prompt)
        print(f"Response content: '{response.content}'")
        print(f"Model: {response.model}")
        print(f"Tokens used: {response.tokens_used}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_ollama())

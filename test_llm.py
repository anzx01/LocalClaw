import asyncio
from localclaw.llm.provider import get_llm_provider

async def test():
    p = get_llm_provider()
    print("Provider:", p)
    r = await p.generate('Hello', max_tokens=10)
    print('Response:', r.content)

asyncio.run(test())

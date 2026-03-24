"""OpenAI-compatible local LLM provider."""

from typing import Dict, List, Optional

import httpx

from localclaw.llm.provider import LLMConfig, LLMProvider, LLMResponse


class OpenAICompatibleProvider(LLMProvider):
    """Provider for local OpenAI-compatible endpoints such as Ollama, LM Studio and vLLM."""

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._base_url = self._normalize_base_url(config.base_url)

    @staticmethod
    def _normalize_base_url(base_url: Optional[str]) -> str:
        normalized = (base_url or "http://127.0.0.1:1234/v1").rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        return normalized

    def _build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Generate a completion using chat-completions semantics."""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, max_tokens=max_tokens, temperature=temperature)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Call the OpenAI-compatible chat completions endpoint."""
        payload = {
            "model": self._config.model,
            "messages": messages,
            "max_tokens": max_tokens or self._config.max_tokens,
            "temperature": self._config.temperature if temperature is None else temperature,
        }

        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._build_headers(),
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices", [])
        choice = choices[0] if choices else {}
        message = choice.get("message", {})
        usage = data.get("usage", {})

        return LLMResponse(
            content=message.get("content", ""),
            model=data.get("model", self._config.model),
            provider=self._config.provider_type.value,
            tokens_used=usage.get("total_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
            metadata={
                "id": data.get("id"),
                "created": data.get("created"),
                "usage": usage,
            },
        )

    async def is_available(self) -> bool:
        """Check whether the local endpoint is reachable."""
        try:
            async with httpx.AsyncClient(timeout=min(self._config.timeout, 5.0)) as client:
                response = await client.get(
                    f"{self._base_url}/models",
                    headers=self._build_headers(),
                )
            return response.status_code == 200
        except Exception:
            return False

"""Ollama LLM client integration."""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from localclaw.llm.provider import LLMConfig, LLMProvider, LLMProviderType, LLMResponse, set_llm_provider


logger = logging.getLogger(__name__)


@dataclass
class OllamaConfig:
    """Configuration for Ollama client."""
    base_url: str = "http://localhost:11434"
    model: str = "llama2"
    timeout: float = 120.0
    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    repeat_penalty: float = 1.1
    context_window: int = 4096


class OllamaClient(LLMProvider):
    """Ollama LLM client."""
    
    def __init__(self, config: Optional[OllamaConfig] = None) -> None:
        llm_config = LLMConfig(
            provider_type=LLMProviderType.OLLAMA,
            model=config.model if config else "llama2",
            base_url=config.base_url if config else "http://localhost:11434",
            timeout=config.timeout if config else 120.0,
            max_tokens=config.max_tokens if config else 2048,
            temperature=config.temperature if config else 0.7,
        )
        super().__init__(llm_config)
        
        self._ollama_config = config or OllamaConfig()
        self._ollama_config.base_url = self._ollama_config.base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._ollama_config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Generate a response from Ollama."""
        session = await self._get_session()
        
        url = f"{self._ollama_config.base_url}/api/generate"
        
        payload = {
            "model": self._ollama_config.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens or self._ollama_config.max_tokens,
                "temperature": temperature or self._ollama_config.temperature,
                "top_p": self._ollama_config.top_p,
                "top_k": self._ollama_config.top_k,
                "repeat_penalty": self._ollama_config.repeat_penalty,
                "num_ctx": self._ollama_config.context_window,
            },
        }
        
        if system_prompt:
            payload["system"] = system_prompt
        
        try:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(f"Ollama error: {response.status} - {error_text}")
                
                data = await response.json()
                
                return LLMResponse(
                    content=data.get("response", ""),
                    model=self._ollama_config.model,
                    provider="ollama",
                    tokens_used=data.get("eval_count", 0) + data.get("prompt_eval_count", 0),
                    metadata={
                        "total_duration": data.get("total_duration"),
                        "load_duration": data.get("load_duration"),
                        "prompt_eval_count": data.get("prompt_eval_count"),
                        "eval_count": data.get("eval_count"),
                    },
                )
        except aiohttp.ClientError as e:
            self._logger.error(f"Ollama connection error: {e}")
            raise RuntimeError(f"Ollama connection error: {e}")
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Generate a chat response from Ollama."""
        session = await self._get_session()
        
        url = f"{self._ollama_config.base_url}/api/chat"
        
        payload = {
            "model": self._ollama_config.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens or self._ollama_config.max_tokens,
                "temperature": temperature or self._ollama_config.temperature,
                "top_p": self._ollama_config.top_p,
                "top_k": self._ollama_config.top_k,
                "repeat_penalty": self._ollama_config.repeat_penalty,
                "num_ctx": self._ollama_config.context_window,
            },
        }
        
        try:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(f"Ollama error: {response.status} - {error_text}")
                
                data = await response.json()
                
                message = data.get("message", {})
                
                return LLMResponse(
                    content=message.get("content", ""),
                    model=self._ollama_config.model,
                    provider="ollama",
                    tokens_used=data.get("eval_count", 0) + data.get("prompt_eval_count", 0),
                    metadata={
                        "total_duration": data.get("total_duration"),
                        "role": message.get("role", "assistant"),
                    },
                )
        except aiohttp.ClientError as e:
            self._logger.error(f"Ollama connection error: {e}")
            raise RuntimeError(f"Ollama connection error: {e}")
    
    async def is_available(self) -> bool:
        """Check if Ollama is available."""
        try:
            url = f"{self._ollama_config.base_url}/api/tags"
            timeout = aiohttp.ClientTimeout(total=min(self._ollama_config.timeout, 5.0))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    return response.status == 200
        except Exception:
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        """List available models."""
        session = await self._get_session()

        try:
            url = f"{self._ollama_config.base_url}/api/tags"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("models", [])
                return []
        except Exception:
            return []
    
    async def pull_model(self, model_name: str) -> bool:
        """Pull a model from Ollama registry."""
        session = await self._get_session()
        
        try:
            url = f"{self._ollama_config.base_url}/api/pull"
            payload = {"name": model_name, "stream": False}
            
            async with session.post(url, json=payload) as response:
                return response.status == 200
        except Exception:
            return False
    
    def set_model(self, model_name: str) -> None:
        """Set the model to use."""
        self._ollama_config.model = model_name
        self._config.model = model_name


_client: Optional[OllamaClient] = None


def get_ollama_client() -> OllamaClient:
    """Get the global Ollama client instance."""
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client


def initialize_ollama(config: Optional[OllamaConfig] = None) -> OllamaClient:
    """Initialize Ollama client and set as global LLM provider."""
    global _client
    _client = OllamaClient(config)
    set_llm_provider(_client)
    return _client

"""LLM provider abstraction."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


class LLMProviderType(str, Enum):
    """Types of LLM providers."""
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    MOCK = "mock"


@dataclass
class LLMResponse:
    """Response from an LLM."""
    content: str
    model: str
    provider: str
    tokens_used: int = 0
    finish_reason: str = "stop"
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "tokens_used": self.tokens_used,
            "finish_reason": self.finish_reason,
            "metadata": self.metadata,
        }


@dataclass
class LLMConfig:
    """Configuration for LLM provider."""
    provider_type: LLMProviderType = LLMProviderType.MOCK
    model: str = "default"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.7
    timeout: float = 60.0


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._logger = logging.getLogger(f"localclaw.llm.{config.provider_type.value}")
    
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Generate a response from the LLM."""
        pass
    
    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Generate a response from a chat conversation."""
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the LLM provider is available."""
        pass
    
    def get_config(self) -> LLMConfig:
        """Get the provider configuration."""
        return self._config


class MockLLMProvider(LLMProvider):
    """Mock LLM provider for testing."""
    
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Generate a mock response."""
        return LLMResponse(
            content=f"Mock response to: {prompt[:100]}...",
            model=self._config.model,
            provider="mock",
            tokens_used=len(prompt.split()),
        )
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Generate a mock chat response."""
        last_message = messages[-1] if messages else {"content": ""}
        return LLMResponse(
            content=f"Mock chat response to: {last_message.get('content', '')[:100]}...",
            model=self._config.model,
            provider="mock",
            tokens_used=sum(len(m.get("content", "").split()) for m in messages),
        )
    
    async def is_available(self) -> bool:
        """Mock provider is always available."""
        return True


_provider: Optional[LLMProvider] = None


def get_llm_provider() -> LLMProvider:
    """Get the global LLM provider instance."""
    global _provider
    if _provider is None:
        _provider = MockLLMProvider(LLMConfig())
    return _provider


def set_llm_provider(provider: LLMProvider) -> None:
    """Set the global LLM provider."""
    global _provider
    _provider = provider

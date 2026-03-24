"""LLM provider abstraction."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from localclaw.config.settings import Settings, get_settings


logger = logging.getLogger(__name__)


class LLMProviderType(str, Enum):
    """Types of LLM providers."""

    OLLAMA = "ollama"
    LMSTUDIO = "lmstudio"
    VLLM = "vllm"
    OPENAI_COMPAT_LOCAL = "openai_compat_local"
    MOCK = "mock"
    UNAVAILABLE = "unavailable"


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

    provider_type: LLMProviderType = LLMProviderType.UNAVAILABLE
    model: str = "default"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    api: str = "openai-compatible"
    context_window: int = 32768
    cost_input: float = 0.0
    cost_output: float = 0.0
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

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Generate a response from a chat conversation."""

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the LLM provider is available."""

    def get_config(self) -> LLMConfig:
        """Get the provider configuration."""
        return self._config


class UnavailableLLMProvider(LLMProvider):
    """Placeholder provider used when no LLM is configured."""

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        raise RuntimeError("No LLM provider configured")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        raise RuntimeError("No LLM provider configured")

    async def is_available(self) -> bool:
        return False


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


def build_llm_config(settings: Optional[Settings] = None) -> LLMConfig:
    """Build a provider config from settings."""
    resolved_settings = settings or get_settings()
    provider_type = LLMProviderType(resolved_settings.model_provider.value)
    return LLMConfig(
        provider_type=provider_type,
        model=resolved_settings.model_name,
        api_key=resolved_settings.get_model_api_key(),
        base_url=resolved_settings.get_model_base_url(),
        api=resolved_settings.model_api,
        context_window=resolved_settings.model_context_window,
        cost_input=resolved_settings.model_cost_input,
        cost_output=resolved_settings.model_cost_output,
        timeout=resolved_settings.default_timeout,
    )


def create_llm_provider(
    config: Optional[LLMConfig] = None,
    settings: Optional[Settings] = None,
) -> LLMProvider:
    """Create an LLM provider from config or settings."""
    resolved_config = config or build_llm_config(settings)

    if resolved_config.provider_type == LLMProviderType.MOCK:
        return MockLLMProvider(resolved_config)

    if resolved_config.provider_type == LLMProviderType.UNAVAILABLE:
        return UnavailableLLMProvider(resolved_config)

    if resolved_config.provider_type == LLMProviderType.OLLAMA and resolved_config.api != "openai-compatible":
        from localclaw.llm.ollama import OllamaClient, OllamaConfig

        return OllamaClient(
            OllamaConfig(
                base_url=(resolved_config.base_url or "http://127.0.0.1:11434").rstrip("/"),
                model=resolved_config.model,
                timeout=resolved_config.timeout,
                max_tokens=resolved_config.max_tokens,
                temperature=resolved_config.temperature,
                context_window=resolved_config.context_window,
            )
        )

    if resolved_config.provider_type in {
        LLMProviderType.OLLAMA,
        LLMProviderType.LMSTUDIO,
        LLMProviderType.VLLM,
        LLMProviderType.OPENAI_COMPAT_LOCAL,
    }:
        from localclaw.llm.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(resolved_config)

    return UnavailableLLMProvider(resolved_config)


_provider: Optional[LLMProvider] = None


def get_llm_provider() -> LLMProvider:
    """Get the global LLM provider instance."""
    global _provider
    if _provider is None:
        _provider = UnavailableLLMProvider(LLMConfig())
    return _provider


def set_llm_provider(provider: LLMProvider) -> None:
    """Set the global LLM provider."""
    global _provider
    _provider = provider


def initialize_llm_provider(settings: Optional[Settings] = None) -> LLMProvider:
    """Initialize the global LLM provider from current settings."""
    provider = create_llm_provider(settings=settings)
    set_llm_provider(provider)
    return provider

"""Global configuration for LocalClaw."""

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Mode(str, Enum):
    """Execution mode for LocalClaw."""

    ZERO = "zero"
    LOCAL = "local"
    HYBRID = "hybrid"


class ModelProvider(str, Enum):
    """Supported local-first model providers."""

    OLLAMA = "ollama"
    LMSTUDIO = "lmstudio"
    VLLM = "vllm"
    OPENAI_COMPAT_LOCAL = "openai_compat_local"
    MOCK = "mock"


class Settings(BaseSettings):
    """Global settings for LocalClaw."""

    model_config = SettingsConfigDict(
        env_prefix="LOCALCLAW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Mode = Field(default=Mode.LOCAL, description="Execution mode: zero, local, or hybrid")
    llm_enabled: bool = Field(default=True, description="Enable LLM integration")

    skills_dir: Path = Field(default=PROJECT_ROOT / "skills", description="Bundled skill definitions")
    managed_skills_dir: Path = Field(
        default=Path.home() / ".localclaw" / "skills",
        description="User-managed local skill directory",
    )
    workspace_skills_dir: Path = Field(
        default=Path.cwd() / "skills",
        description="Workspace-local skill directory",
    )
    extra_skill_dirs: list[Path] = Field(
        default_factory=list,
        description="Additional skill directories loaded with lowest precedence",
    )
    data_dir: Path = Field(default=Path("./data"), description="Directory for data storage")

    memory_db: Path = Field(default=Path("./data/memory.db"), description="Path to memory database")
    audit_log: Path = Field(default=Path("./data/audit.jsonl"), description="Path to audit log file")

    log_level: str = Field(default="INFO", description="Logging level")

    server_host: str = Field(default="127.0.0.1", description="Server host")
    server_port: int = Field(default=8000, description="Server port")

    default_timeout: float = Field(default=30.0, description="Default timeout for operations in seconds")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    retry_delay: float = Field(default=1.0, description="Delay between retries in seconds")

    model_provider: ModelProvider = Field(
        default=ModelProvider.OLLAMA,
        description="Preferred LLM provider for local inference",
    )
    model_name: str = Field(default="qwen2.5-coder:7b", description="Model name to use")
    model_base_url: Optional[str] = Field(
        default=None,
        description="Base URL for the configured model provider",
    )
    model_api: str = Field(
        default="openai-compatible",
        description="Model API style, usually openai-compatible for local endpoints",
    )
    model_context_window: int = Field(default=32768, description="Configured context window")
    model_cost_input: float = Field(default=0.0, description="Input token cost")
    model_cost_output: float = Field(default=0.0, description="Output token cost")

    # Backward-compatible provider fields.
    ollama_base_url: Optional[str] = Field(default=None, description="Ollama API base URL")
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI-compatible API key")
    openai_base_url: Optional[str] = Field(default=None, description="OpenAI-compatible API base URL")

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.managed_skills_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        self.memory_db.parent.mkdir(parents=True, exist_ok=True)

    def get_skill_search_paths(self) -> list[Path]:
        """Return skill directories in precedence order from low to high."""
        ordered_paths = [
            *self.extra_skill_dirs,
            self.skills_dir,
            self.managed_skills_dir,
            self.workspace_skills_dir,
        ]

        unique_paths: list[Path] = []
        seen: set[Path] = set()
        for path in ordered_paths:
            resolved = path.resolve()
            if resolved in seen or not resolved.exists():
                continue
            seen.add(resolved)
            unique_paths.append(path)
        return unique_paths

    @property
    def uses_openai_compatible_api(self) -> bool:
        """Whether the current provider should be accessed with an OpenAI-compatible API."""
        if self.model_api.lower() == "openai-compatible":
            return True
        return self.model_provider in {
            ModelProvider.LMSTUDIO,
            ModelProvider.VLLM,
            ModelProvider.OPENAI_COMPAT_LOCAL,
        }

    def get_model_base_url(self) -> str:
        """Resolve the active model base URL."""
        if self.model_base_url:
            base_url = self.model_base_url
        elif self.model_provider == ModelProvider.OLLAMA:
            base_url = self.ollama_base_url or "http://127.0.0.1:11434"
        elif self.model_provider == ModelProvider.VLLM:
            base_url = self.openai_base_url or "http://127.0.0.1:8000/v1"
        else:
            base_url = self.openai_base_url or "http://127.0.0.1:1234/v1"

        normalized = base_url.rstrip("/")
        if self.uses_openai_compatible_api and not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        if not self.uses_openai_compatible_api and normalized.endswith("/v1"):
            normalized = normalized[:-3].rstrip("/")
        return normalized

    def get_model_api_key(self) -> Optional[str]:
        """Resolve the API key for the active model provider."""
        return self.openai_api_key


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_directories()
    return _settings


def reload_settings() -> Settings:
    """Reload settings from environment and files."""
    global _settings
    _settings = Settings()
    _settings.ensure_directories()
    return _settings

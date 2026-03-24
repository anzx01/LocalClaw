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


class SkillInstallProtectionMode(str, Enum):
    """Post-install protection policy for third-party skills."""

    OFF = "off"
    DISABLE_HIGH_RISK = "disable_high_risk"
    ISOLATE = "isolate"


class Settings(BaseSettings):
    """Global settings for LocalClaw."""

    model_config = SettingsConfigDict(
        env_prefix="LOCALCLAW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Mode = Field(default=Mode.LOCAL, description="Execution mode: zero, local, or hybrid")
    llm_enabled: bool = Field(default=True, description="Enable the default local-model understanding flow")
    llm_parse_only: bool = Field(
        default=True,
        description="Route all user input through the local LLM understanding chain without automatic legacy parser fallback",
    )

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
        default="ollama",
        description="Model API style, such as ollama or openai-compatible",
    )
    model_context_window: int = Field(default=32768, description="Configured context window")
    model_cost_input: float = Field(default=0.0, description="Input token cost")
    model_cost_output: float = Field(default=0.0, description="Output token cost")

    # Backward-compatible provider fields.
    ollama_base_url: Optional[str] = Field(default=None, description="Ollama API base URL")
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI-compatible API key")
    openai_base_url: Optional[str] = Field(default=None, description="OpenAI-compatible API base URL")

    wechat_personal_enabled: bool = Field(
        default=False,
        description="Enable the experimental personal WeChat bridge webhook",
    )
    wechat_personal_inbound_token: Optional[str] = Field(
        default=None,
        description="Shared secret expected from the personal WeChat bridge",
    )
    wechat_personal_proxy_url: Optional[str] = Field(
        default=None,
        description="Optional bridge proxy endpoint used to push replies back out",
    )
    wechat_personal_api_key: Optional[str] = Field(
        default=None,
        description="Optional API key sent to the personal WeChat bridge proxy",
    )
    wechat_personal_reply_via_proxy: bool = Field(
        default=False,
        description="Whether replies should also be posted to the configured bridge proxy URL",
    )

    whatsapp_enabled: bool = Field(
        default=False,
        description="Enable the WhatsApp Cloud API webhook channel",
    )
    whatsapp_verify_token: Optional[str] = Field(
        default=None,
        description="Webhook verification token for the WhatsApp Cloud API",
    )
    whatsapp_app_secret: Optional[str] = Field(
        default=None,
        description="Meta app secret used for x-hub-signature-256 verification",
    )
    whatsapp_access_token: Optional[str] = Field(
        default=None,
        description="System user access token for sending WhatsApp Cloud API replies",
    )
    whatsapp_phone_number_id: Optional[str] = Field(
        default=None,
        description="WhatsApp Cloud API phone number ID used for outbound replies",
    )
    whatsapp_graph_base_url: str = Field(
        default="https://graph.facebook.com",
        description="Base URL for the WhatsApp Cloud API Graph endpoint",
    )
    whatsapp_graph_api_version: str = Field(
        default="v23.0",
        description="Graph API version for WhatsApp Cloud API calls",
    )
    whatsapp_reply_via_cloud_api: bool = Field(
        default=False,
        description="Whether LocalClaw should send WhatsApp text replies via the Cloud API",
    )

    skill_install_protection_mode: SkillInstallProtectionMode = Field(
        default=SkillInstallProtectionMode.DISABLE_HIGH_RISK,
        description="Protection mode automatically applied to newly installed third-party skills",
    )
    skill_isolation_require_approval: bool = Field(
        default=True,
        description="Whether isolated skills must ask for approval before protected tool execution",
    )
    skill_isolation_block_critical: bool = Field(
        default=True,
        description="Whether isolated skills should still block critical tools such as raw shell execution",
    )

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

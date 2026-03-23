"""Global configuration for LocalClaw."""

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(str, Enum):
    """Execution mode for LocalClaw."""
    ZERO = "zero"
    LOCAL = "local"
    HYBRID = "hybrid"


class Settings(BaseSettings):
    """Global settings for LocalClaw."""
    
    model_config = SettingsConfigDict(
        env_prefix="LOCALCLAW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    mode: Mode = Field(default=Mode.ZERO, description="Execution mode: zero, local, or hybrid")
    llm_enabled: bool = Field(default=False, description="Enable LLM integration")
    
    skills_dir: Path = Field(default=Path("./skills"), description="Directory for skill definitions")
    data_dir: Path = Field(default=Path("./data"), description="Directory for data storage")
    
    memory_db: Path = Field(default=Path("./data/memory.db"), description="Path to memory database")
    audit_log: Path = Field(default=Path("./data/audit.jsonl"), description="Path to audit log file")
    
    log_level: str = Field(default="INFO", description="Logging level")
    
    server_host: str = Field(default="127.0.0.1", description="Server host")
    server_port: int = Field(default=8000, description="Server port")
    
    default_timeout: float = Field(default=30.0, description="Default timeout for operations in seconds")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    retry_delay: float = Field(default=1.0, description="Delay between retries in seconds")
    
    ollama_base_url: Optional[str] = Field(default=None, description="Ollama API base URL")
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API key")
    openai_base_url: Optional[str] = Field(default=None, description="OpenAI API base URL")
    
    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        self.memory_db.parent.mkdir(parents=True, exist_ok=True)


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

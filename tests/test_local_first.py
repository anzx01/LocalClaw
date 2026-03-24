"""Tests for local-first configuration and skill loading."""

from localclaw.config.settings import ModelProvider, Mode, Settings, SkillInstallProtectionMode
from localclaw.llm.ollama import OllamaClient
from localclaw.llm.openai_compatible import OpenAICompatibleProvider
from localclaw.llm.provider import LLMConfig, LLMProviderType, create_llm_provider
from localclaw.skills.loader import SkillLoader, load_skills_from_settings
from localclaw.skills.registry import SkillRegistry


def test_settings_default_to_local_first():
    """Default settings should prefer local model execution."""
    settings = Settings(_env_file=None)

    assert settings.mode == Mode.LOCAL
    assert settings.llm_enabled is True
    assert settings.llm_parse_only is True
    assert settings.model_provider == ModelProvider.OLLAMA
    assert settings.uses_openai_compatible_api is False
    assert not settings.get_model_base_url().endswith("/v1")
    assert settings.skill_install_protection_mode == SkillInstallProtectionMode.DISABLE_HIGH_RISK
    assert settings.skill_isolation_require_approval is True


def test_create_ollama_provider_from_default_settings():
    """Default Ollama settings should use the native Ollama provider."""
    settings = Settings(_env_file=None)

    provider = create_llm_provider(settings=settings)

    assert isinstance(provider, OllamaClient)
    assert provider.get_config().provider_type == LLMProviderType.OLLAMA


def test_create_openai_compatible_provider_from_config():
    """Generic local OpenAI-compatible endpoints should use the shared provider."""
    provider = create_llm_provider(
        LLMConfig(
            provider_type=LLMProviderType.OPENAI_COMPAT_LOCAL,
            model="test-model",
            base_url="http://127.0.0.1:1234",
        )
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.get_config().provider_type == LLMProviderType.OPENAI_COMPAT_LOCAL


def test_skill_loader_registers_skill_markdown(tmp_path):
    """Directory-style skills with SKILL.md should load and register."""
    skill_dir = tmp_path / "demo_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo_skill
version: 1.0.0
description: Demo markdown skill
type: atomic
tools:
  - safe_shell
user-invocable: false
actions:
  - type: transform
    template: "demo"
---
# Demo Skill

This skill is defined with markdown front matter.
""",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)

    count = loader.register_from_directory(tmp_path, recursive=True)

    assert count == 1
    info = registry.get_skill_info("demo_skill")
    assert info is not None
    assert info["availability"] == "available"
    assert info["state"] == "enabled"
    assert info["user_invocable"] is False


def test_skill_loader_marks_missing_requirements_as_blocked(tmp_path):
    """Missing env vars should keep a skill installed but blocked."""
    skill_dir = tmp_path / "blocked_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: blocked_skill
version: 1.0.0
description: Blocked markdown skill
type: atomic
requires:
  env:
    - LOCALCLAW_TEST_MISSING_ENV
actions:
  - type: transform
    template: "blocked"
---
# Blocked Skill

This one requires a missing environment variable.
""",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)

    count = loader.register_from_directory(tmp_path, recursive=True)

    assert count == 1
    info = registry.get_skill_info("blocked_skill")
    assert info is not None
    assert info["availability"] == "blocked"
    assert "LOCALCLAW_TEST_MISSING_ENV" in info["availability_details"]["missing_env"]
    assert info["state"] == "stopped"


def test_settings_skill_search_paths_follow_openclaw_precedence(tmp_path):
    """Extra dirs should load first, workspace last, without duplicate real paths."""
    extra_dir = tmp_path / "extra"
    bundled_dir = tmp_path / "bundled"
    managed_dir = tmp_path / "managed"
    workspace_dir = tmp_path / "workspace"
    for directory in (extra_dir, bundled_dir, managed_dir, workspace_dir):
        directory.mkdir()

    settings = Settings(
        _env_file=None,
        skills_dir=bundled_dir,
        managed_skills_dir=managed_dir,
        workspace_skills_dir=workspace_dir,
        extra_skill_dirs=[extra_dir, bundled_dir],
    )

    search_paths = settings.get_skill_search_paths()

    assert search_paths == [extra_dir, bundled_dir, managed_dir, workspace_dir]


def test_skill_loader_supports_openclaw_nested_metadata_and_precedence(tmp_path):
    """Nested metadata.openclaw.requires should be honored and higher precedence should win."""
    extra_dir = tmp_path / "extra"
    bundled_dir = tmp_path / "bundled"
    managed_dir = tmp_path / "managed"
    workspace_dir = tmp_path / "workspace"
    for directory in (extra_dir, bundled_dir, managed_dir, workspace_dir):
        directory.mkdir()

    (extra_dir / "shared.json").write_text(
        '{"name":"shared","description":"extra","actions":[{"type":"transform","template":"extra"}]}',
        encoding="utf-8",
    )
    (bundled_dir / "shared.json").write_text(
        '{"name":"shared","description":"bundled","actions":[{"type":"transform","template":"bundled"}]}',
        encoding="utf-8",
    )
    (managed_dir / "shared.json").write_text(
        '{"name":"shared","description":"managed","actions":[{"type":"transform","template":"managed"}]}',
        encoding="utf-8",
    )

    nested_dir = workspace_dir / "nested_skill"
    nested_dir.mkdir()
    (nested_dir / "SKILL.md").write_text(
        """---
name: shared
description: workspace
metadata:
  openclaw:
    requires:
      anyBins:
        - definitely-missing-binary
        - python
    primaryEnv: TEST_API_KEY
    homepage: https://example.com
actions:
  - type: transform
    template: "workspace"
---
# Shared Skill

Workspace wins.
""",
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        skills_dir=bundled_dir,
        managed_skills_dir=managed_dir,
        workspace_skills_dir=workspace_dir,
        extra_skill_dirs=[extra_dir],
    )
    registry = SkillRegistry()

    count = load_skills_from_settings(settings, registry)

    assert count == 4
    info = registry.get_skill_info("shared")
    assert info is not None
    assert info["description"] == "workspace"
    assert info["availability"] == "available"
    assert info["metadata"]["primary_env"] == "TEST_API_KEY"
    assert info["metadata"]["homepage"] == "https://example.com"

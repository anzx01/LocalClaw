"""Tests for local-first configuration and skill loading."""

import logging

from localclaw.config.settings import ModelProvider, Mode, Settings, SkillInstallProtectionMode
from localclaw.llm.ollama import OllamaClient
from localclaw.llm.openai_compatible import OpenAICompatibleProvider
from localclaw.llm.provider import LLMConfig, LLMProviderType, create_llm_provider
from localclaw.skills.base import create_skill_from_dict
from localclaw.skills.loader import SkillLoader, load_skills_from_settings
from localclaw.skills.registry.clawhub import LocalSkillRegistry
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
    assert settings.extra_skill_dirs == []
    assert settings.skill_install_protection_mode == SkillInstallProtectionMode.DISABLE_HIGH_RISK
    assert settings.skill_isolation_require_approval is True


def test_create_skill_from_dict_normalizes_string_fields():
    """Skill scalar fields should be coerced to strings for stable API serialization."""
    skill = create_skill_from_dict(
        {
            "name": 123,
            "version": 2,
            "description": True,
            "actions": [{"type": "transform", "template": "ok"}],
        }
    )
    definition = skill.get_definition()

    assert definition.name == "123"
    assert definition.version == "2"
    assert definition.description == "True"


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


def test_skill_loader_ignores_package_manifests(tmp_path, caplog):
    """Node package manifests inside skills directories should be skipped silently."""
    unknown_dir = tmp_path / "unknown"
    unknown_dir.mkdir()
    (unknown_dir / "package.json").write_text(
        '{"name":"unknown","version":"1.0.0","type":"module"}',
        encoding="utf-8",
    )
    (unknown_dir / "package-lock.json").write_text(
        '{"name":"unknown","lockfileVersion":3,"requires":true}',
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)

    with caplog.at_level(logging.ERROR):
        count = loader.register_from_directory(tmp_path, recursive=True)

    assert count == 0
    assert not any("Error loading skill from" in record.message for record in caplog.records)


def test_skill_loader_ignores_ci_workflows_and_invalid_skill_types(tmp_path, caplog):
    """CI YAML and invalid skill type manifests should never be registered as skills."""
    unknown_dir = tmp_path / "unknown"
    workflow_dir = unknown_dir / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: CI
on:
  push:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""".strip(),
        encoding="utf-8",
    )
    (unknown_dir / "unknown.json").write_text(
        '{"name":"unknown","type":"module","version":"2"}',
        encoding="utf-8",
    )

    registry = SkillRegistry()
    loader = SkillLoader(registry)

    with caplog.at_level(logging.ERROR):
        count = loader.register_from_directory(tmp_path, recursive=True)

    assert count == 0
    assert registry.list_skills() == []
    assert not any("Error loading skill from" in record.message for record in caplog.records)


def test_skill_loader_marks_missing_requirements_as_blocked(tmp_path):
    """Missing env vars should keep availability blocked but not auto-disable the skill."""
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
    assert info["state"] == "enabled"


def test_skill_loader_load_from_file_defaults_to_enabled_even_when_blocked(tmp_path):
    """Directly loaded skills should default to enabled state."""

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
""",
        encoding="utf-8",
    )

    loader = SkillLoader(SkillRegistry())
    skill = loader.load_from_file(skill_dir)

    assert skill is not None
    assert skill.state.value == "enabled"
    assert skill.get_definition().metadata["availability"]["status"] == "blocked"


def test_settings_skill_search_paths_use_configured_dirs_then_managed(tmp_path):
    """Configured dirs should load first, managed installs last, without duplicate real paths."""
    extra_dir = tmp_path / "extra"
    second_extra_dir = tmp_path / "second-extra"
    managed_dir = tmp_path / "managed"
    for directory in (extra_dir, second_extra_dir, managed_dir):
        directory.mkdir()

    settings = Settings(
        _env_file=None,
        managed_skills_dir=managed_dir,
        extra_skill_dirs=[extra_dir, second_extra_dir, managed_dir],
    )

    search_paths = settings.get_skill_search_paths()

    assert search_paths == [extra_dir, second_extra_dir, managed_dir]


def test_default_skill_search_paths_include_only_managed_dir(tmp_path):
    """Without configured dirs, only the managed install directory should be loaded."""
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir(parents=True)

    settings = Settings(
        _env_file=None,
        managed_skills_dir=managed_dir,
    )

    search_paths = settings.get_skill_search_paths()

    assert search_paths == [managed_dir]


def test_load_skills_from_settings_uses_only_configured_and_managed_dirs(tmp_path):
    """Loading from settings should only pull skills from configured dirs and the managed install dir."""
    configured_dir = tmp_path / "configured"
    managed_dir = tmp_path / "managed"
    for directory in (configured_dir, managed_dir):
        directory.mkdir()

    (configured_dir / "configured.json").write_text(
        '{"name":"configured","description":"configured","actions":[{"type":"transform","template":"configured"}]}',
        encoding="utf-8",
    )
    (managed_dir / "managed.json").write_text(
        '{"name":"managed","description":"managed","actions":[{"type":"transform","template":"managed"}]}',
        encoding="utf-8",
    )

    registry = SkillRegistry()
    settings = Settings(
        _env_file=None,
        managed_skills_dir=managed_dir,
        extra_skill_dirs=[configured_dir],
    )

    count = load_skills_from_settings(settings, registry)

    assert count == 2
    assert set(registry.list_skills()) == {"configured", "managed"}


def test_skill_loader_supports_openclaw_nested_metadata_and_precedence(tmp_path):
    """Nested metadata.openclaw.requires should be honored and managed installs should win precedence."""
    extra_dir = tmp_path / "extra"
    managed_dir = tmp_path / "managed"
    for directory in (extra_dir, managed_dir):
        directory.mkdir()

    (extra_dir / "shared.json").write_text(
        '{"name":"shared","description":"extra","actions":[{"type":"transform","template":"extra"}]}',
        encoding="utf-8",
    )

    nested_dir = managed_dir / "nested_skill"
    nested_dir.mkdir()
    (nested_dir / "SKILL.md").write_text(
        """---
name: shared
description: managed
metadata:
  openclaw:
    requires:
      anyBins:
        - definitely-missing-binary
        - python
    skillKey: repo.shared
    aliases:
      - shared-tool
    primaryEnv: TEST_API_KEY
    homepage: https://example.com
actions:
  - type: transform
    template: "managed"
---
# Shared Skill

Managed install wins.
""",
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        managed_skills_dir=managed_dir,
        extra_skill_dirs=[extra_dir],
    )
    registry = SkillRegistry()

    count = load_skills_from_settings(settings, registry)

    assert count == 2
    info = registry.get_skill_info("shared")
    assert info is not None
    assert info["description"] == "managed"
    assert info["availability"] == "available"
    assert info["metadata"]["primary_env"] == "TEST_API_KEY"
    assert info["metadata"]["homepage"] == "https://example.com"
    assert info["skill_key"] == "repo.shared"
    assert "shared-tool" in info["aliases"]


def test_local_skill_registry_uses_managed_skill_dir(monkeypatch, tmp_path):
    """Marketplace installs should go to the managed user skill directory, not the bundled repo tree."""

    from localclaw.skills.registry import clawhub as clawhub_registry

    managed_dir = tmp_path / "managed"
    settings = Settings(
        _env_file=None,
        managed_skills_dir=managed_dir,
    )

    monkeypatch.setattr(clawhub_registry, "get_settings", lambda: settings)

    registry = LocalSkillRegistry()

    assert registry.skills_dir == managed_dir

"""Tests for skills."""

import pytest

from localclaw.core.models import Context, RiskLevel
from localclaw.skills.base import (
    Skill,
    SkillDefinition,
    SkillType,
    create_skill_from_dict,
)
from localclaw.skills.registry import SkillRegistry


class MockSkill(Skill):
    """Mock skill for testing."""
    
    name = "mock_skill"
    version = "1.0.0"
    description = "A mock skill for testing"
    skill_type = SkillType.ATOMIC
    inputs = {"input": "string"}
    outputs = {"output": "string"}
    risk_level = RiskLevel.LOW
    
    async def execute(self, context: Context, **kwargs):
        from localclaw.core.models import ExecutionResult
        return ExecutionResult.success(
            message="Mock skill executed",
            data={"output": kwargs.get("input", "").upper()},
        )


def test_skill_definition():
    """Test skill definition creation."""
    skill = MockSkill()
    definition = skill.get_definition()
    
    assert definition.name == "mock_skill"
    assert definition.version == "1.0.0"
    assert definition.type == SkillType.ATOMIC


def test_skill_state():
    """Test skill state management."""
    skill = MockSkill()
    
    assert skill.state.value == "installed"
    
    skill.enable()
    assert skill.state.value == "enabled"
    
    skill.disable()
    assert skill.state.value == "stopped"


def test_skill_registry():
    """Test skill registry."""
    registry = SkillRegistry()
    skill = MockSkill()
    
    registry.register(skill)
    
    assert registry.get("mock_skill") is skill
    assert "mock_skill" in registry.list_skills()
    
    registry.unregister("mock_skill")
    assert registry.get("mock_skill") is None


def test_create_skill_from_dict():
    """Test creating a skill from a dictionary."""
    skill_data = {
        "name": "test_skill",
        "version": "1.0.0",
        "description": "Test skill",
        "type": "atomic",
        "inputs": {"name": "string"},
        "outputs": {"message": "string"},
        "actions": [
            {"type": "transform", "template": "Hello, {{name}}!"}
        ],
        "permissions": {"risk_level": "low"},
    }
    
    skill = create_skill_from_dict(skill_data)
    
    assert skill.name == "test_skill"
    assert skill.version == "1.0.0"
    assert skill.skill_type == SkillType.ATOMIC


def test_registry_enable_disable():
    """Test enabling and disabling skills in registry."""
    registry = SkillRegistry()
    skill = MockSkill()
    registry.register(skill)
    
    registry.enable("mock_skill")
    assert skill.state.value == "enabled"
    
    registry.disable("mock_skill")
    assert skill.state.value == "stopped"


def test_registry_get_info():
    """Test getting skill info from registry."""
    registry = SkillRegistry()
    skill = MockSkill()
    registry.register(skill)
    
    info = registry.get_skill_info("mock_skill")
    
    assert info is not None
    assert info["name"] == "mock_skill"
    assert info["version"] == "1.0.0"

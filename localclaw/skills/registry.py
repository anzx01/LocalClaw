"""Skill registry for managing skills."""

import logging
from typing import Any, Dict, List, Optional, Type

from localclaw.skills.base import Skill, SkillState


logger = logging.getLogger(__name__)


class SkillRegistry:
    """Registry for managing skills."""
    
    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._triggers: Dict[str, List[str]] = {}
        self._logger = logging.getLogger("localclaw.skills.registry")
    
    def register(self, skill: Skill) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill
        self._logger.info(f"Registered skill: {skill.name} v{skill.version}")
        
        definition = skill.get_definition()
        for trigger in definition.triggers:
            if trigger.pattern:
                if trigger.pattern not in self._triggers:
                    self._triggers[trigger.pattern] = []
                self._triggers[trigger.pattern].append(skill.name)
    
    def register_class(self, skill_class: Type[Skill]) -> None:
        """Register a skill class by instantiating it."""
        skill = skill_class()
        self.register(skill)
    
    def unregister(self, name: str) -> bool:
        """Unregister a skill."""
        if name in self._skills:
            skill = self._skills[name]
            definition = skill.get_definition()
            
            for trigger in definition.triggers:
                if trigger.pattern and trigger.pattern in self._triggers:
                    if name in self._triggers[trigger.pattern]:
                        self._triggers[trigger.pattern].remove(name)
            
            del self._skills[name]
            self._logger.info(f"Unregistered skill: {name}")
            return True
        return False
    
    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)
    
    def get_all(self) -> Dict[str, Skill]:
        """Get all registered skills."""
        return self._skills.copy()
    
    def list_skills(self) -> List[str]:
        """List all registered skill names."""
        return list(self._skills.keys())
    
    def get_skills_by_state(self, state: SkillState) -> List[Skill]:
        """Get skills by their state."""
        return [s for s in self._skills.values() if s.state == state]
    
    def get_enabled_skills(self) -> List[Skill]:
        """Get all enabled skills."""
        return [
            s for s in self._skills.values()
            if s.state in (SkillState.ENABLED, SkillState.RUNNING)
        ]
    
    def get_skill_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get information about a skill."""
        skill = self.get(name)
        if skill is None:
            return None
        
        definition = skill.get_definition()
        return {
            "name": skill.name,
            "version": skill.version,
            "description": skill.description,
            "type": skill.skill_type.value,
            "state": skill.state.value,
            "inputs": skill.inputs,
            "outputs": skill.outputs,
            "tools": skill.tools,
            "permissions": definition.permissions,
        }
    
    def get_all_info(self) -> List[Dict[str, Any]]:
        """Get information about all skills."""
        return [
            self.get_skill_info(name)
            for name in self._skills
        ]
    
    def enable(self, name: str) -> bool:
        """Enable a skill."""
        skill = self.get(name)
        if skill:
            skill.enable()
            self._logger.info(f"Enabled skill: {name}")
            return True
        return False
    
    def disable(self, name: str) -> bool:
        """Disable a skill."""
        skill = self.get(name)
        if skill:
            skill.disable()
            self._logger.info(f"Disabled skill: {name}")
            return True
        return False
    
    def match_trigger(self, text: str) -> List[str]:
        """Find skills that match a trigger pattern."""
        import re
        matched: List[str] = []
        
        for pattern, skill_names in self._triggers.items():
            if re.search(pattern, text, re.IGNORECASE):
                matched.extend(skill_names)
        
        return list(set(matched))


_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """Get the global skill registry."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry


def register_skill(skill: Skill) -> None:
    """Register a skill in the global registry."""
    get_skill_registry().register(skill)


def get_skill(name: str) -> Optional[Skill]:
    """Get a skill from the global registry."""
    return get_skill_registry().get(name)

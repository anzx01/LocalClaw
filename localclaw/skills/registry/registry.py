"""Skill registry for managing skills."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from localclaw.skills.base import Skill, SkillState


logger = logging.getLogger(__name__)


class SkillRegistry:
    """Registry for managing skills."""
    
    def __init__(self, policy_store_path: Optional[Path] = None) -> None:
        self._skills: Dict[str, Skill] = {}
        self._triggers: Dict[str, List[str]] = {}
        self._skill_aliases: Dict[str, str] = {}
        self._skill_approval_required: Dict[str, bool] = {}
        self._policy_store_path = Path(policy_store_path) if policy_store_path else None
        self._logger = logging.getLogger("localclaw.skills.registry")
        self._load_approval_policies()
    
    def register(self, skill: Skill, enable: bool = True) -> None:
        """Register a skill."""
        if enable:
            skill.enable()
        else:
            skill.disable()

        self._skills[skill.name] = skill
        self._rebuild_alias_index()
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
            self._skill_approval_required.pop(name, None)
            self._persist_approval_policies()
            self._rebuild_alias_index()
            self._logger.info(f"Unregistered skill: {name}")
            return True
        return False
    
    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        resolved = self.resolve_name(name)
        if resolved is None:
            return None
        return self._skills.get(resolved)
    
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
        availability = definition.metadata.get("availability", {})
        skill_key = str(definition.metadata.get("skill_key", skill.name)).strip() or skill.name
        aliases = [
            alias
            for alias in definition.metadata.get("aliases", [])
            if str(alias).strip() and str(alias).strip().lower() != skill.name.lower()
        ]
        invocation_names = self._build_invocation_names(skill.name, skill_key, aliases)
        require_approval = self.get_skill_approval_required(skill.name)
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
            "availability": availability.get("status", "available"),
            "availability_details": availability,
            "user_invocable": definition.metadata.get("user_invocable", True),
            "skill_key": skill_key,
            "aliases": aliases,
            "invocation_names": invocation_names,
            "source_path": definition.metadata.get("source_path"),
            "source_format": definition.metadata.get("source_format"),
            "documentation": definition.metadata.get("documentation"),
            "metadata": definition.metadata,
            "require_approval": require_approval,
        }
    
    def get_all_info(self) -> List[Dict[str, Any]]:
        """Get information about all skills."""
        return [
            self.get_skill_info(name)
            for name in self._skills
        ]

    def get_model_invocable_info(self) -> List[Dict[str, Any]]:
        """Return skills that the model is allowed to invoke directly."""
        invocable: List[Dict[str, Any]] = []
        for name in self._skills:
            info = self.get_skill_info(name)
            if info is None:
                continue
            metadata = info.get("metadata", {}) or {}
            if info.get("availability") != "available":
                continue
            if not info.get("user_invocable", True):
                continue
            if metadata.get("disable_model_invocation", False):
                continue
            invocable.append(info)
        return invocable

    def resolve_name(self, identifier: str) -> Optional[str]:
        """Resolve a canonical skill name from a name, OpenClaw key, or alias."""
        if identifier is None:
            return None
        if identifier in self._skills:
            return identifier

        lowered = str(identifier).strip().lower()
        if not lowered:
            return None
        return self._skill_aliases.get(lowered)
    
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

    def get_skill_approval_required(self, name: str) -> bool:
        """Return whether a skill should always require human approval."""
        resolved = self.resolve_name(name)
        if resolved is None:
            return False
        return bool(self._skill_approval_required.get(resolved, False))

    def set_skill_approval_required(self, name: str, required: bool) -> bool:
        """Set whether a skill should always require human approval."""
        resolved = self.resolve_name(name)
        if resolved is None or resolved not in self._skills:
            return False
        self._skill_approval_required[resolved] = bool(required)
        self._persist_approval_policies()
        self._logger.info(
            "Updated skill approval policy: %s require_approval=%s",
            resolved,
            bool(required),
        )
        return True
    
    def match_trigger(self, text: str) -> List[str]:
        """Find skills that match a trigger pattern."""
        import re
        matched: List[str] = []
        
        for pattern, skill_names in self._triggers.items():
            if re.search(pattern, text, re.IGNORECASE):
                matched.extend(skill_names)
        
        return list(set(matched))

    def _rebuild_alias_index(self) -> None:
        """Rebuild skill lookup aliases from current registry contents."""
        self._skill_aliases = {}
        for skill in self._skills.values():
            info = self._build_skill_invocation_metadata(skill)
            for identifier in info["invocation_names"]:
                self._skill_aliases[identifier.lower()] = skill.name

    def _build_skill_invocation_metadata(self, skill: Skill) -> Dict[str, Any]:
        """Collect OpenClaw-compatible invocation names for a skill."""
        definition = skill.get_definition()
        skill_key = str(definition.metadata.get("skill_key", skill.name)).strip() or skill.name
        raw_aliases = definition.metadata.get("aliases", []) or []
        aliases = [str(alias).strip() for alias in raw_aliases if str(alias).strip()]
        return {
            "skill_key": skill_key,
            "aliases": aliases,
            "invocation_names": self._build_invocation_names(skill.name, skill_key, aliases),
        }

    def _build_invocation_names(
        self,
        canonical_name: str,
        skill_key: str,
        aliases: List[str],
    ) -> List[str]:
        """Return unique invocation identifiers in priority order."""
        ordered: List[str] = []
        seen: set[str] = set()

        for candidate in [canonical_name, skill_key, *aliases]:
            normalized = str(candidate).strip()
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(normalized)
        return ordered

    def _load_approval_policies(self) -> None:
        """Load persisted per-skill approval policies when configured."""
        if self._policy_store_path is None or not self._policy_store_path.exists():
            return
        try:
            payload = json.loads(self._policy_store_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._logger.warning("Failed to load skill approval policies: %s", exc)
            return

        policies = payload.get("require_approval", payload) if isinstance(payload, dict) else {}
        if not isinstance(policies, dict):
            return
        self._skill_approval_required = {
            str(name): bool(value)
            for name, value in policies.items()
            if str(name).strip()
        }

    def _persist_approval_policies(self) -> None:
        """Persist per-skill approval policies when configured."""
        if self._policy_store_path is None:
            return
        try:
            self._policy_store_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "require_approval": dict(sorted(self._skill_approval_required.items())),
            }
            self._policy_store_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            self._logger.warning("Failed to persist skill approval policies: %s", exc)


_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """Get the global skill registry."""
    global _registry
    if _registry is None:
        from localclaw.config.settings import get_settings

        settings = get_settings()
        _registry = SkillRegistry(policy_store_path=settings.data_dir / "skill_policies.json")
    return _registry


def register_skill(skill: Skill) -> None:
    """Register a skill in the global registry."""
    get_skill_registry().register(skill)


def get_skill(name: str) -> Optional[Skill]:
    """Get a skill from the global registry."""
    return get_skill_registry().get(name)

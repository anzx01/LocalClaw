"""Skill registry package with lazy ClawHub exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from localclaw.skills.registry.registry import SkillRegistry, get_skill, get_skill_registry, register_skill

__all__ = [
    "SkillRegistry",
    "get_skill_registry",
    "register_skill",
    "get_skill",
    "ClawHubClient",
    "BundledSkillCatalog",
    "LocalSkillRegistry",
    "get_bundled_skill_catalog",
    "get_clawhub_client",
    "get_local_registry",
    "clawhub",
]


def __getattr__(name: str) -> Any:
    """Lazily expose the ClawHub helpers without creating import cycles."""

    if name in {
        "ClawHubClient",
        "BundledSkillCatalog",
        "LocalSkillRegistry",
        "get_bundled_skill_catalog",
        "get_clawhub_client",
        "get_local_registry",
        "clawhub",
    }:
        module = import_module("localclaw.skills.registry.clawhub")
        if name == "clawhub":
            return module
        return getattr(module, name)
    raise AttributeError(name)

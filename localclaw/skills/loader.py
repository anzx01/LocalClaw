"""Skill loader for loading skills from files."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from localclaw.skills.base import Skill, create_skill_from_dict
from localclaw.skills.registry import SkillRegistry, get_skill_registry


logger = logging.getLogger(__name__)


class SkillLoader:
    """Loads skills from files and directories."""
    
    SUPPORTED_EXTENSIONS = {".json", ".yaml", ".yml"}
    
    def __init__(self, registry: Optional[SkillRegistry] = None) -> None:
        self._registry = registry or get_skill_registry()
        self._logger = logging.getLogger("localclaw.skills.loader")
    
    def load_from_file(self, file_path: Path) -> Optional[Skill]:
        """Load a skill from a file."""
        if not file_path.exists():
            self._logger.warning(f"Skill file not found: {file_path}")
            return None
        
        if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            self._logger.warning(f"Unsupported file format: {file_path.suffix}")
            return None
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                if file_path.suffix.lower() == ".json":
                    data = json.load(f)
                else:
                    data = yaml.safe_load(f)
            
            if not isinstance(data, dict):
                self._logger.error(f"Invalid skill definition in {file_path}")
                return None
            
            skill = create_skill_from_dict(data)
            self._logger.info(f"Loaded skill from {file_path}: {skill.name}")
            return skill
        except json.JSONDecodeError as e:
            self._logger.error(f"JSON parse error in {file_path}: {e}")
            return None
        except yaml.YAMLError as e:
            self._logger.error(f"YAML parse error in {file_path}: {e}")
            return None
        except Exception as e:
            self._logger.error(f"Error loading skill from {file_path}: {e}")
            return None
    
    def load_from_directory(self, dir_path: Path, recursive: bool = False) -> List[Skill]:
        """Load all skills from a directory."""
        if not dir_path.exists():
            self._logger.warning(f"Skills directory not found: {dir_path}")
            return []
        
        skills: List[Skill] = []
        
        if recursive:
            pattern = "**/*"
        else:
            pattern = "*"
        
        for file_path in dir_path.glob(pattern):
            if file_path.is_file() and file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                skill = self.load_from_file(file_path)
                if skill:
                    skills.append(skill)
        
        self._logger.info(f"Loaded {len(skills)} skills from {dir_path}")
        return skills
    
    def register_from_file(self, file_path: Path) -> bool:
        """Load and register a skill from a file."""
        skill = self.load_from_file(file_path)
        if skill:
            self._registry.register(skill)
            return True
        return False
    
    def register_from_directory(self, dir_path: Path, recursive: bool = False) -> int:
        """Load and register all skills from a directory."""
        skills = self.load_from_directory(dir_path, recursive)
        count = 0
        for skill in skills:
            self._registry.register(skill)
            count += 1
        return count
    
    def load_openclaw_skill(self, file_path: Path) -> Optional[Skill]:
        """Load an OpenClaw-compatible skill definition."""
        if not file_path.exists():
            self._logger.warning(f"OpenClaw skill file not found: {file_path}")
            return None
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                if file_path.suffix.lower() == ".json":
                    data = json.load(f)
                else:
                    data = yaml.safe_load(f)
            
            converted = self._convert_openclaw_to_localclaw(data)
            
            skill = create_skill_from_dict(converted)
            self._logger.info(f"Loaded OpenClaw skill from {file_path}: {skill.name}")
            return skill
        except Exception as e:
            self._logger.error(f"Error loading OpenClaw skill from {file_path}: {e}")
            return None
    
    def _convert_openclaw_to_localclaw(self, openclaw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenClaw skill format to LocalClaw format."""
        localclaw_data: Dict[str, Any] = {
            "name": openclaw_data.get("name", "unnamed"),
            "version": openclaw_data.get("version", "1.0.0"),
            "description": openclaw_data.get("description", ""),
            "type": openclaw_data.get("type", "atomic"),
            "inputs": openclaw_data.get("inputs", {}),
            "outputs": openclaw_data.get("outputs", {}),
            "actions": [],
            "permissions": openclaw_data.get("permissions", {"risk_level": "low"}),
            "triggers": openclaw_data.get("triggers", []),
            "metadata": {"source": "openclaw"},
        }
        
        if "command" in openclaw_data:
            localclaw_data["actions"].append({
                "type": "tool_call",
                "tool": openclaw_data["command"],
                "params": openclaw_data.get("args", {}),
            })
        
        if "script" in openclaw_data:
            localclaw_data["actions"].append({
                "type": "transform",
                "template": openclaw_data["script"],
            })
        
        if "handler" in openclaw_data:
            localclaw_data["actions"].append({
                "type": "skill_call",
                "skill": openclaw_data["handler"],
            })
        
        if "steps" in openclaw_data:
            for step in openclaw_data["steps"]:
                converted_step = self._convert_openclaw_step(step)
                if converted_step:
                    localclaw_data["actions"].append(converted_step)
        
        if "actions" in openclaw_data:
            for action in openclaw_data["actions"]:
                converted_action = self._convert_openclaw_step(action)
                if converted_action:
                    localclaw_data["actions"].append(converted_action)
        
        if not localclaw_data["actions"]:
            localclaw_data["actions"].append({
                "type": "transform",
                "template": "{{input}}",
            })
        
        return localclaw_data
    
    def _convert_openclaw_step(self, step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Convert an OpenClaw step to LocalClaw action."""
        step_type = step.get("type", "").lower()
        
        if step_type in ("tool", "tool_call", "command"):
            return {
                "type": "tool_call",
                "tool": step.get("tool") or step.get("command"),
                "params": step.get("params") or step.get("args", {}),
            }
        
        elif step_type in ("skill", "skill_call", "handler"):
            return {
                "type": "skill_call",
                "skill": step.get("skill") or step.get("handler"),
                "params": step.get("params", {}),
            }
        
        elif step_type in ("transform", "script", "template"):
            return {
                "type": "transform",
                "template": step.get("template") or step.get("script", ""),
            }
        
        elif step_type in ("condition", "if"):
            return {
                "type": "condition",
                "condition": step.get("condition"),
                "then": [self._convert_openclaw_step(s) for s in step.get("then", []) if self._convert_openclaw_step(s)],
            }
        
        elif step_type in ("loop", "foreach"):
            return {
                "type": "loop",
                "var": step.get("var", "item"),
                "over": step.get("over"),
                "actions": [self._convert_openclaw_step(s) for s in step.get("actions", []) if self._convert_openclaw_step(s)],
            }
        
        elif step_type in ("parallel", "concurrent"):
            return {
                "type": "parallel",
                "actions": [self._convert_openclaw_step(s) for s in step.get("actions", []) if self._convert_openclaw_step(s)],
            }
        
        return None


def load_skills_from_dir(dir_path: Path, recursive: bool = False) -> int:
    """Load and register all skills from a directory."""
    loader = SkillLoader()
    return loader.register_from_directory(dir_path, recursive)


def create_builtin_skills() -> List[Dict[str, Any]]:
    """Create definitions for built-in skills."""
    return [
        {
            "name": "hello",
            "version": "1.0.0",
            "description": "Say hello to someone",
            "type": "atomic",
            "inputs": {"name": "string"},
            "outputs": {"message": "string"},
            "actions": [
                {
                    "type": "transform",
                    "template": "Hello, {{name}}!",
                }
            ],
            "permissions": {"risk_level": "low"},
            "triggers": [{"type": "pattern", "pattern": r"^hello\s+(?P<name>\w+)$"}],
        },
        {
            "name": "echo",
            "version": "1.0.0",
            "description": "Echo back a message",
            "type": "atomic",
            "inputs": {"text": "string"},
            "outputs": {"message": "string"},
            "actions": [
                {
                    "type": "transform",
                    "template": "{{text}}",
                }
            ],
            "permissions": {"risk_level": "low"},
        },
        {
            "name": "system_status",
            "version": "1.0.0",
            "description": "Get system status information",
            "type": "atomic",
            "outputs": {"status": "string", "version": "string"},
            "actions": [
                {
                    "type": "tool_call",
                    "tool": "system_status",
                }
            ],
            "permissions": {"risk_level": "low"},
        },
        {
            "name": "list_skills",
            "version": "1.0.0",
            "description": "List all available skills",
            "type": "atomic",
            "outputs": {"skills": "list"},
            "actions": [
                {
                    "type": "tool_call",
                    "tool": "list_skills",
                }
            ],
            "permissions": {"risk_level": "low"},
        },
    ]


def register_builtin_skills() -> None:
    """Register built-in skills."""
    registry = get_skill_registry()
    for skill_data in create_builtin_skills():
        skill = create_skill_from_dict(skill_data)
        registry.register(skill)

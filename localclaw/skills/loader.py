"""Skill loader for loading skills from files."""

import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from localclaw.config.settings import Settings, get_settings
from localclaw.skills.base import Skill, create_skill_from_dict
from localclaw.skills.registry.registry import SkillRegistry, get_skill_registry


logger = logging.getLogger(__name__)


class SkillLoader:
    """Loads skills from files and directories."""

    SUPPORTED_EXTENSIONS = {".json", ".yaml", ".yml"}
    SKILL_MARKDOWN_NAME = "SKILL.md"
    NON_SKILL_FILENAMES = {
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }
    NON_SKILL_DIRECTORIES = {
        ".github",
        "node_modules",
        ".git",
        "__pycache__",
        "dist",
        "build",
    }

    def __init__(self, registry: Optional[SkillRegistry] = None) -> None:
        self._registry = registry or get_skill_registry()
        self._logger = logging.getLogger("localclaw.skills.loader")

    def load_from_file(self, file_path: Path) -> Optional[Skill]:
        """Load a skill from a file."""
        resolved_path = file_path
        if file_path.is_dir():
            resolved_path = file_path / self.SKILL_MARKDOWN_NAME

        if not resolved_path.exists():
            self._logger.warning(f"Skill file not found: {resolved_path}")
            return None

        if (
            resolved_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS
            and resolved_path.name.upper() != self.SKILL_MARKDOWN_NAME.upper()
        ):
            self._logger.warning(f"Unsupported file format: {resolved_path.suffix}")
            return None

        try:
            data = self._load_skill_data(resolved_path)
            if not isinstance(data, dict):
                self._logger.error(f"Invalid skill definition in {resolved_path}")
                return None
            if not self._looks_like_skill_definition(data, resolved_path):
                self._logger.debug("Skipping non-skill definition file: %s", resolved_path)
                return None

            prepared = self._prepare_skill_data(data, resolved_path)
            skill = create_skill_from_dict(prepared)
            availability = prepared.get("metadata", {}).get("availability", {})
            skill.enable()

            self._logger.info(
                "Loaded skill from %s: %s (%s)",
                resolved_path,
                skill.name,
                availability.get("status", "available"),
            )
            return skill
        except json.JSONDecodeError as e:
            self._logger.error(f"JSON parse error in {resolved_path}: {e}")
            return None
        except yaml.YAMLError as e:
            self._logger.error(f"YAML parse error in {resolved_path}: {e}")
            return None
        except Exception as e:
            self._logger.error(f"Error loading skill from {resolved_path}: {e}")
            return None

    def load_from_directory(self, dir_path: Path, recursive: bool = False) -> List[Skill]:
        """Load all skills from a directory."""
        if not dir_path.exists():
            self._logger.warning(f"Skills directory not found: {dir_path}")
            return []

        skills: List[Skill] = []

        for file_path in self._iter_skill_paths(dir_path, recursive):
            skill = self.load_from_file(file_path)
            if skill:
                skills.append(skill)

        self._logger.info(f"Loaded {len(skills)} skills from {dir_path}")
        return skills

    def register_from_file(self, file_path: Path) -> bool:
        """Load and register a skill from a file."""
        skill = self.load_from_file(file_path)
        if skill:
            self._registry.register(skill, enable=True)
            return True
        return False

    def register_from_directory(self, dir_path: Path, recursive: bool = False) -> int:
        """Load and register all skills from a directory."""
        skills = self.load_from_directory(dir_path, recursive)
        count = 0
        for skill in skills:
            self._registry.register(skill, enable=True)
            count += 1
        return count

    def load_openclaw_skill(self, file_path: Path) -> Optional[Skill]:
        """Load an OpenClaw-compatible skill definition."""
        if not file_path.exists():
            self._logger.warning(f"OpenClaw skill file not found: {file_path}")
            return None

        try:
            data = self._load_skill_data(file_path)
            converted = self._convert_openclaw_to_localclaw(data)

            skill = create_skill_from_dict(self._prepare_skill_data(converted, file_path))
            self._logger.info(f"Loaded OpenClaw skill from {file_path}: {skill.name}")
            return skill
        except Exception as e:
            self._logger.error(f"Error loading OpenClaw skill from {file_path}: {e}")
            return None

    def _iter_skill_paths(self, dir_path: Path, recursive: bool) -> Iterable[Path]:
        """Yield supported skill definition paths without duplicates."""
        seen: set[Path] = set()

        def add(path: Path) -> Optional[Path]:
            resolved = path.resolve()
            if resolved in seen:
                return None
            seen.add(resolved)
            return path

        own_skill_md = dir_path / self.SKILL_MARKDOWN_NAME
        if own_skill_md.exists():
            yielded = add(own_skill_md)
            if yielded:
                yield yielded

        candidates = dir_path.rglob("*") if recursive else dir_path.iterdir()
        for candidate in candidates:
            try:
                relative_parts = candidate.relative_to(dir_path).parts
            except Exception:
                relative_parts = candidate.parts
            if any(part.lower() in self.NON_SKILL_DIRECTORIES for part in relative_parts):
                continue

            if candidate.is_dir():
                skill_md = candidate / self.SKILL_MARKDOWN_NAME
                if skill_md.exists():
                    yielded = add(skill_md)
                    if yielded:
                        yield yielded
                continue

            if candidate.name.lower() in self.NON_SKILL_FILENAMES:
                continue

            if candidate.name.upper() == self.SKILL_MARKDOWN_NAME.upper() or candidate.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                yielded = add(candidate)
                if yielded:
                    yield yielded

    def _load_skill_data(self, file_path: Path) -> Dict[str, Any]:
        """Load raw skill data from JSON/YAML/SKILL.md."""
        if file_path.name.upper() == self.SKILL_MARKDOWN_NAME.upper():
            return self._load_skill_markdown(file_path)

        with open(file_path, "r", encoding="utf-8") as f:
            if file_path.suffix.lower() == ".json":
                return json.load(f)
            return yaml.safe_load(f)

    def _load_skill_markdown(self, file_path: Path) -> Dict[str, Any]:
        """Load a directory-style skill with YAML front matter."""
        text = file_path.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
        if not match:
            raise ValueError(f"{file_path} must start with YAML front matter")

        front_matter, body = match.groups()
        data = yaml.safe_load(front_matter) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Invalid SKILL.md metadata in {file_path}")

        body_text = body.strip()
        if body_text and not data.get("description"):
            first_paragraph = next(
                (line.strip() for line in body_text.splitlines() if line.strip() and not line.startswith("#")),
                "",
            )
            if first_paragraph:
                data["description"] = first_paragraph

        metadata = dict(data.get("metadata", {}))
        metadata.setdefault("documentation", body_text)
        metadata.setdefault("source_format", "skill_markdown")
        data["metadata"] = metadata
        return data

    def _prepare_skill_data(self, data: Dict[str, Any], source_path: Path) -> Dict[str, Any]:
        """Normalize metadata and evaluate skill availability."""
        prepared = dict(data)
        metadata = dict(prepared.get("metadata", {}))
        openclaw_metadata = self._extract_openclaw_metadata(metadata)
        requirements_raw = (
            prepared.get("requires")
            or metadata.get("requires")
            or openclaw_metadata.get("requires")
            or {}
        )
        requirements = requirements_raw if isinstance(requirements_raw, dict) else {}
        user_invocable = prepared.get(
            "user_invocable",
            prepared.get("user-invocable", metadata.get("user_invocable", True)),
        )
        disable_model_invocation = prepared.get(
            "disable_model_invocation",
            prepared.get("disable-model-invocation", metadata.get("disable_model_invocation", False)),
        )
        homepage = prepared.get("homepage") or openclaw_metadata.get("homepage")
        primary_env = openclaw_metadata.get("primaryEnv")
        skill_key = openclaw_metadata.get("skillKey", prepared.get("name", "unknown"))
        aliases = self._normalize_aliases(
            prepared.get("aliases"),
            metadata.get("aliases"),
            openclaw_metadata.get("aliases"),
        )
        availability = self._evaluate_eligibility(requirements, openclaw_metadata)

        metadata.update(
            {
                "openclaw": openclaw_metadata,
                "requires": requirements,
                "user_invocable": user_invocable,
                "disable_model_invocation": disable_model_invocation,
                "homepage": homepage,
                "primary_env": primary_env,
                "skill_key": skill_key,
                "aliases": aliases,
                "source_path": str(source_path),
                "availability": availability,
                "source_format": metadata.get(
                    "source_format",
                    "skill_markdown" if source_path.name.upper() == self.SKILL_MARKDOWN_NAME.upper() else source_path.suffix.lower().lstrip("."),
                ),
            }
        )
        prepared["metadata"] = metadata
        return prepared

    def _looks_like_skill_definition(self, data: Dict[str, Any], source_path: Path) -> bool:
        """Best-effort detection to avoid parsing project manifests as skills."""
        name = source_path.name.lower()
        if name in self.NON_SKILL_FILENAMES:
            return False
        if source_path.name.upper() == self.SKILL_MARKDOWN_NAME.upper():
            return True

        # GitHub Actions or CI workflow files are not skills.
        if "jobs" in data and ("on" in data or "permissions" in data):
            return False

        if "actions" in data and not isinstance(data.get("actions"), list):
            return False
        if "metadata" in data and not isinstance(data.get("metadata"), dict):
            return False
        if "requires" in data and not isinstance(data.get("requires"), dict):
            return False
        if "type" in data:
            raw_type = str(data.get("type") or "").strip().lower()
            if raw_type and raw_type not in {"atomic", "workflow", "agent"}:
                return False

        action_markers = (
            "actions",
            "steps",
            "command",
            "script",
            "handler",
        )
        if any(marker in data for marker in action_markers):
            return True

        schema_markers = (
            "tools",
            "triggers",
            "inputs",
            "outputs",
            "requires",
            "metadata",
        )
        if any(marker in data for marker in schema_markers) and (
            "name" in data or "description" in data or "type" in data
        ):
            return True

        # Common package manifest shape (Node/npm).
        if "scripts" in data or "dependencies" in data or "devDependencies" in data:
            return False

        return False

    def _normalize_aliases(self, *values: Any) -> List[str]:
        """Collect normalized skill aliases from multiple metadata locations."""
        aliases: List[str] = []
        seen: set[str] = set()

        for value in values:
            if value is None:
                continue
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                alias = str(candidate).strip()
                if not alias:
                    continue
                lowered = alias.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                aliases.append(alias)

        return aliases

    def _extract_openclaw_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Extract nested OpenClaw metadata from a skill definition."""
        openclaw_metadata = metadata.get("openclaw", {})
        return dict(openclaw_metadata) if isinstance(openclaw_metadata, dict) else {}

    def _evaluate_eligibility(
        self,
        requirements: Dict[str, Any],
        openclaw_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Check whether a skill is currently executable."""
        openclaw_metadata = openclaw_metadata or {}
        if openclaw_metadata.get("always") is True:
            return {
                "status": "available",
                "missing_bins": [],
                "missing_any_bins": [],
                "missing_env": [],
                "missing_config": [],
                "unsupported_os": [],
                "reason": "",
            }

        required_bins = list(requirements.get("bins", []) or [])
        required_any_bins = list(requirements.get("anyBins", []) or requirements.get("any_bins", []) or [])
        required_env = list(requirements.get("env", []) or [])
        required_config = list(requirements.get("config", []) or [])
        allowed_os = list(openclaw_metadata.get("os", []) or requirements.get("os", []) or [])

        missing_bins = [binary for binary in required_bins if shutil.which(binary) is None]
        missing_any_bins = required_any_bins if required_any_bins and not any(shutil.which(binary) for binary in required_any_bins) else []
        missing_env = [env_var for env_var in required_env if not os.getenv(env_var)]
        missing_config = [name for name in required_config if not self._is_config_enabled(name)]
        unsupported_os = allowed_os if allowed_os and sys.platform not in allowed_os else []

        status = "available"
        reasons: List[str] = []
        if missing_bins:
            reasons.append(f"missing bins: {', '.join(missing_bins)}")
        if missing_any_bins:
            reasons.append(f"missing anyBins: {', '.join(missing_any_bins)}")
        if missing_env:
            reasons.append(f"missing env: {', '.join(missing_env)}")
        if missing_config:
            reasons.append(f"disabled config: {', '.join(missing_config)}")
        if unsupported_os:
            reasons.append(f"unsupported os: {sys.platform}")
        if reasons:
            status = "blocked"

        return {
            "status": status,
            "missing_bins": missing_bins,
            "missing_any_bins": missing_any_bins,
            "missing_env": missing_env,
            "missing_config": missing_config,
            "unsupported_os": unsupported_os,
            "reason": "; ".join(reasons),
        }

    def _is_config_enabled(self, name: str) -> bool:
        """Check a boolean-like setting by attribute name."""
        settings = get_settings()
        value: Any = settings
        for segment in name.split("."):
            if isinstance(value, dict):
                value = value.get(segment)
            else:
                value = getattr(value, segment, None)
            if value is None:
                return False
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        return bool(value)

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


def load_skills_from_settings(
    settings: Optional[Settings] = None,
    registry: Optional[SkillRegistry] = None,
) -> int:
    """Load skills using OpenClaw-like precedence from configured directories."""
    resolved_settings = settings or get_settings()
    loader = SkillLoader(registry)
    count = 0
    for skill_dir in resolved_settings.get_skill_search_paths():
        count += loader.register_from_directory(skill_dir, recursive=True)
    return count

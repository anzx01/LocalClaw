"""ClawHub client for skill registry."""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

import aiohttp

from localclaw.config.settings import get_settings


logger = logging.getLogger(__name__)


class ClawHubClient:
    """ClawHub client for interacting with the skill registry."""

    def __init__(self, base_url: str = "https://clawhub.example.com"):
        """Initialize ClawHub client."""
        self.base_url = base_url
        self.session = None

    async def _ensure_session(self):
        """Ensure aiohttp session is created."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """Close the aiohttp session."""
        if self.session:
            await self.session.close()
            self.session = None

    async def search_skills(self, query: str = "", category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search for skills in ClawHub."""
        try:
            session = await self._ensure_session()
            params = {}
            if query:
                params["q"] = query
            if category:
                params["category"] = category

            async with session.get(f"{self.base_url}/api/skills/search", params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Failed to search skills: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error searching skills: {e}")
            return []

    async def get_skill_detail(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a skill."""
        try:
            session = await self._ensure_session()
            async with session.get(f"{self.base_url}/api/skills/{skill_id}") as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Failed to get skill detail: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error getting skill detail: {e}")
            return None

    async def download_skill(self, skill_id: str, target_dir: Path) -> bool:
        """Download a skill from ClawHub."""
        skill_data = await self.fetch_skill_bundle(skill_id)
        if skill_data is None:
            return False
        return self.save_skill_bundle(skill_id, skill_data, target_dir)

    async def fetch_skill_bundle(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the installable skill bundle without writing it locally."""
        try:
            session = await self._ensure_session()
            async with session.get(f"{self.base_url}/api/skills/{skill_id}/download") as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Failed to download skill: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error downloading skill: {e}")
            return None

    def save_skill_bundle(self, skill_id: str, skill_data: Dict[str, Any], target_dir: Path) -> bool:
        """Save a fetched skill bundle into the local skills directory."""
        try:
            skill_dir = target_dir / skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)

            with open(skill_dir / f"{skill_id}.json", "w", encoding="utf-8") as f:
                json.dump(skill_data, f, indent=2)

            files = skill_data.get("files", {})
            if isinstance(files, dict):
                for file_name, file_content in files.items():
                    file_path = skill_dir / file_name
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(str(file_content))

            return True
        except Exception as e:
            logger.error(f"Error saving skill bundle: {e}")
            return False

    async def get_categories(self) -> List[str]:
        """Get available skill categories."""
        try:
            session = await self._ensure_session()
            async with session.get(f"{self.base_url}/api/categories") as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Failed to get categories: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error getting categories: {e}")
            return []


class LocalSkillRegistry:
    """Local skill registry for managing downloaded skills."""

    def __init__(self):
        """Initialize local skill registry."""
        self.settings = get_settings()
        self.skills_dir = self.settings.skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def list_local_skills(self) -> List[str]:
        """List locally installed skills."""
        skills = []
        for item in self.skills_dir.iterdir():
            if item.is_dir():
                skills.append(item.name)
        return skills

    def get_skill_path(self, skill_name: str) -> Path:
        """Get the path to a skill directory."""
        return self.skills_dir / skill_name

    def is_skill_installed(self, skill_name: str) -> bool:
        """Check if a skill is installed locally."""
        skill_path = self.get_skill_path(skill_name)
        return skill_path.exists() and skill_path.is_dir()

    def remove_skill(self, skill_name: str) -> bool:
        """Remove a locally installed skill."""
        import shutil
        skill_path = self.get_skill_path(skill_name)
        if skill_path.exists() and skill_path.is_dir():
            try:
                shutil.rmtree(skill_path)
                return True
            except Exception as e:
                logger.error(f"Error removing skill: {e}")
                return False
        return False


def get_clawhub_client() -> ClawHubClient:
    """Get the ClawHub client instance."""
    return ClawHubClient()

def get_local_registry() -> LocalSkillRegistry:
    """Get the local skill registry instance."""
    return LocalSkillRegistry()

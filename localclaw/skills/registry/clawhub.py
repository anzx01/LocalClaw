"""ClawHub client for skill registry."""

import asyncio
import json
import logging
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Any, Tuple

import aiohttp
import yaml

from localclaw.config.settings import get_settings
from localclaw.skills.loader import SkillLoader


logger = logging.getLogger(__name__)

UPLOAD_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".tar.gz")
UPLOAD_FILE_SUFFIXES = (".json", ".yaml", ".yml", ".md")


class ClawHubClient:
    """ClawHub client for interacting with the skill registry."""

    DEFAULT_BASE_URL = "https://clawhub.ai"
    MAX_TEXT_FILE_BYTES = 256_000
    SKILL_MARKDOWN_NAME = "SKILL.md"
    SKILL_MARKDOWN_VARIANTS = ("SKILL.md", "skill.md", "skills.md", "SKILL.MD")

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        """Initialize ClawHub client."""
        settings = get_settings()
        resolved_base_url = (base_url or settings.get_clawhub_base_url()).strip().rstrip("/")
        self.base_url = resolved_base_url or self.DEFAULT_BASE_URL
        self.token = (token or settings.get_clawhub_token() or "").strip() or None
        self.session = None
        self._temp_dirs: List[Path] = []
        self.last_request_error: Optional[str] = None
        self.last_search_error: Optional[str] = None

    async def _ensure_session(self):
        """Ensure aiohttp session is created."""
        if self.session is None:
            # Force the OS-threaded resolver because aiohttp's async resolver can
            # fail on some Windows/local-network setups even when normal DNS works.
            connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def close(self):
        """Close the aiohttp session."""
        if self.session:
            await self.session.close()
            self.session = None
        for temp_dir in self._temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        self._temp_dirs.clear()

    async def search_skills(self, query: str = "", category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search for skills in ClawHub."""
        self.last_request_error = None
        self.last_search_error = None
        try:
            normalized_query = query.strip()
            if normalized_query:
                payload = await self._get_json(
                    "/api/v1/search",
                    params={"q": normalized_query, "limit": "20"},
                )
                if payload is None:
                    self.last_search_error = self.last_request_error or "ClawHub search returned no data"
                    return []
                results = (payload or {}).get("results", [])
                listings = [
                    listing
                    for listing in (
                        self._normalize_search_result(item)
                        for item in results if isinstance(item, dict)
                    )
                    if listing is not None and self._matches_category(listing, category)
                ]
                return await self._enrich_search_results(listings)

            payload = await self._get_json(
                "/api/v1/skills",
                params={"limit": "50"},
            )
            if payload is None:
                self.last_search_error = self.last_request_error or "ClawHub search returned no data"
                return []
            items = (payload or {}).get("items", [])
            return [
                listing
                for listing in (
                    self._normalize_list_item(item)
                    for item in items if isinstance(item, dict)
                )
                if listing is not None and self._matches_category(listing, category)
            ]
        except Exception as e:
            self.last_search_error = str(e)
            logger.error(f"Error searching skills: {e}")
            return []

    async def _enrich_search_results(self, listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fill marketplace stats for search hits using the detail endpoint when needed."""

        if not listings:
            return listings

        pending_indexes = [
            index for index, listing in enumerate(listings)
            if self._listing_missing_marketplace_stats(listing)
        ]
        if not pending_indexes:
            return listings

        detail_results = await asyncio.gather(
            *(self.get_skill_detail(str(listings[index].get("id") or "")) for index in pending_indexes),
            return_exceptions=True,
        )
        for index, detail in zip(pending_indexes, detail_results):
            if isinstance(detail, Exception) or not isinstance(detail, dict):
                continue
            self._merge_marketplace_stats(listings[index], detail)
            if listings[index].get("author") in ("", "ClawHub"):
                listings[index]["author"] = detail.get("author") or listings[index].get("author")
        return listings

    async def get_skill_detail(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a skill."""
        try:
            payload = await self._get_json(f"/api/v1/skills/{skill_id}")
            if not isinstance(payload, dict):
                return None
            return self._normalize_skill_detail(payload)
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
            detail = await self.get_skill_detail(skill_id)
            if detail is None:
                return None

            version = str(detail.get("version") or "").strip()
            archive_bytes = await self._download_skill_archive(skill_id, version or None)
            if archive_bytes is None:
                return None

            extracted_root = self._extract_skill_archive(skill_id, archive_bytes)
            loader = SkillLoader()
            skill_md_path = self._find_skill_markdown_path(extracted_root)
            if skill_md_path is None:
                raise ValueError(f"Downloaded ClawHub archive for {skill_id} is missing SKILL.md")
            raw_data = loader._load_skill_data(skill_md_path)
            prepared = loader._prepare_skill_data(raw_data, skill_md_path)
            bundle = dict(prepared)
            metadata = dict(bundle.get("metadata", {}) or {})
            metadata.setdefault("source_format", "skill_markdown")
            metadata.setdefault("source_path", str(skill_md_path))
            clawhub_meta = dict(metadata.get("clawhub", {}) or {})
            clawhub_meta.update(
                {
                    "slug": skill_id,
                    "registry": self.base_url,
                    "version": version,
                    "source": "remote",
                }
            )
            metadata["clawhub"] = clawhub_meta
            bundle["metadata"] = metadata
            bundle["author"] = bundle.get("author") or detail.get("author") or detail.get("owner") or ""
            bundle["homepage"] = bundle.get("homepage") or detail.get("homepage") or ""
            bundle["repository"] = bundle.get("repository") or detail.get("repository") or ""
            bundle["files"] = self._snapshot_skill_files(extracted_root)
            bundle["_localclaw_source_dir"] = str(extracted_root)
            return bundle
        except Exception as e:
            logger.error(f"Error downloading skill: {e}")
            return None

    def save_skill_bundle(self, skill_id: str, skill_data: Dict[str, Any], target_dir: Path) -> bool:
        """Save a fetched skill bundle into the local skills directory."""
        try:
            skill_dir = target_dir / skill_id
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
            skill_dir.mkdir(parents=True, exist_ok=True)

            persisted_skill_data = {
                key: value
                for key, value in skill_data.items()
                if not str(key).startswith("_localclaw_")
            }
            source_dir = Path(str(skill_data.get("_localclaw_source_dir", "") or "")).resolve() if skill_data.get("_localclaw_source_dir") else None
            source_copied = bool(source_dir and source_dir.exists() and source_dir.is_dir())
            render_as_markdown = self._should_save_as_skill_markdown(persisted_skill_data)
            generated_files: set[str] = set()

            if source_copied and source_dir is not None:
                for item in source_dir.iterdir():
                    destination = skill_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, destination, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, destination)

            if render_as_markdown:
                (skill_dir / self.SKILL_MARKDOWN_NAME).write_text(
                    self._render_skill_markdown(persisted_skill_data),
                    encoding="utf-8",
                )
                generated_files.add(self.SKILL_MARKDOWN_NAME)
                self._canonicalize_skill_markdown_file(skill_dir)
                stale_json = skill_dir / f"{skill_id}.json"
                if stale_json.exists():
                    stale_json.unlink()
            else:
                bundle_path = skill_dir / f"{skill_id}.json"
                with open(bundle_path, "w", encoding="utf-8") as f:
                    json.dump(persisted_skill_data, f, indent=2, ensure_ascii=False)
                generated_files.add(bundle_path.name)

            files = persisted_skill_data.get("files", {})
            if isinstance(files, dict) and not source_copied:
                for file_name, file_content in files.items():
                    normalized_name = Path(str(file_name)).as_posix()
                    if normalized_name in generated_files:
                        continue
                    if render_as_markdown and Path(normalized_name).name.lower() in {
                        variant.lower() for variant in self.SKILL_MARKDOWN_VARIANTS
                    }:
                        continue
                    file_path = skill_dir / file_name
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(str(file_content))

            return True
        except Exception as e:
            logger.error(f"Error saving skill bundle: {e}")
            return False

    def _should_save_as_skill_markdown(self, skill_data: Dict[str, Any]) -> bool:
        """Preserve OpenClaw-style SKILL.md bundles when available."""

        metadata = skill_data.get("metadata", {}) or {}
        source_format = str(metadata.get("source_format", "")).strip().lower()
        source_path = str(metadata.get("source_path", "")).strip()
        return source_format == "skill_markdown" or source_path.upper().endswith("SKILL.MD")

    def _render_skill_markdown(self, skill_data: Dict[str, Any]) -> str:
        """Serialize a bundle back into directory-style SKILL.md format."""

        front_matter = {
            key: value
            for key, value in skill_data.items()
            if key != "files" and not str(key).startswith("_localclaw_")
        }

        metadata = dict(front_matter.get("metadata", {}) or {})
        documentation = str(metadata.pop("documentation", "") or "").strip()
        front_matter["metadata"] = metadata

        yaml_text = yaml.safe_dump(
            front_matter,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        if not documentation:
            documentation = f"# {front_matter.get('name', 'Skill')}"

        return f"---\n{yaml_text}\n---\n\n{documentation.rstrip()}\n"

    async def get_categories(self) -> List[str]:
        """Get available skill categories."""
        try:
            skills = await self.search_skills("")
            categories = {
                str(skill.get("category") or "").strip()
                for skill in skills
                if str(skill.get("category") or "").strip()
            }
            return sorted(categories)
        except Exception as e:
            logger.error(f"Error getting categories: {e}")
            return []

    async def _get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        """Fetch a JSON payload from the ClawHub API."""

        self.last_request_error = None
        session = await self._ensure_session()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        async with session.get(f"{self.base_url}{path}", params=params, headers=headers) as response:
            if response.status != 200:
                body = await response.text()
                message = body.strip() or f"HTTP {response.status}"
                self.last_request_error = f"{path} returned {response.status}: {message}"
                logger.error("ClawHub request failed %s (%s): %s", path, response.status, body)
                return None
            return await response.json()

    async def _download_skill_archive(self, slug: str, version: Optional[str]) -> Optional[bytes]:
        """Download a ClawHub skill archive."""

        session = await self._ensure_session()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        params = {"slug": slug}
        if version:
            params["version"] = version
        async with session.get(f"{self.base_url}/api/v1/download", params=params, headers=headers) as response:
            if response.status != 200:
                body = await response.text()
                logger.error("Failed to download ClawHub skill %s (%s): %s", slug, response.status, body)
                return None
            return await response.read()

    def _extract_skill_archive(self, skill_id: str, archive_bytes: bytes) -> Path:
        """Extract a downloaded ClawHub archive and return the skill root directory."""

        temp_dir = Path(tempfile.mkdtemp(prefix=f"localclaw-clawhub-{skill_id.replace('.', '-')}-"))
        self._temp_dirs.append(temp_dir)
        archive_path = temp_dir / f"{skill_id}.zip"
        archive_path.write_bytes(archive_bytes)

        extract_dir = temp_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path) as zip_file:
            zip_file.extractall(extract_dir)

        skill_root = self._locate_skill_root(extract_dir)
        if skill_root is None:
            raise ValueError(f"Downloaded ClawHub archive for {skill_id} is missing SKILL.md")
        return skill_root

    def _locate_skill_root(self, extract_dir: Path) -> Optional[Path]:
        """Locate the extracted skill root containing SKILL.md."""
        skill_markdown = self._find_skill_markdown_path(extract_dir)
        if skill_markdown is not None:
            return skill_markdown.parent
        return None

    def _find_skill_markdown_path(self, directory: Path) -> Optional[Path]:
        """Locate the primary skill markdown file inside a directory tree."""

        for variant in self.SKILL_MARKDOWN_VARIANTS:
            candidate = directory / variant
            if candidate.exists() and candidate.is_file():
                return candidate

        for candidate in sorted(directory.rglob("*")):
            if candidate.is_file() and candidate.name.lower() in {
                variant.lower() for variant in self.SKILL_MARKDOWN_VARIANTS
            }:
                return candidate
        return None

    def _canonicalize_skill_markdown_file(self, skill_dir: Path) -> None:
        """Keep only the canonical top-level SKILL.md filename after installation."""

        candidates = [
            candidate
            for candidate in skill_dir.iterdir()
            if candidate.is_file() and candidate.name.lower() in {
                variant.lower() for variant in self.SKILL_MARKDOWN_VARIANTS
            }
        ]
        if not candidates:
            return

        canonical_path = skill_dir / self.SKILL_MARKDOWN_NAME
        primary = next((candidate for candidate in candidates if candidate.name == self.SKILL_MARKDOWN_NAME), candidates[0])

        if primary.name != self.SKILL_MARKDOWN_NAME:
            temp_path = skill_dir / "__localclaw_skill_md__.tmp"
            counter = 0
            while temp_path.exists():
                counter += 1
                temp_path = skill_dir / f"__localclaw_skill_md__.{counter}.tmp"
            primary.rename(temp_path)
            temp_path.rename(canonical_path)

        for candidate in list(skill_dir.iterdir()):
            if not candidate.is_file():
                continue
            if candidate.name == self.SKILL_MARKDOWN_NAME:
                continue
            if candidate.name.lower() in {variant.lower() for variant in self.SKILL_MARKDOWN_VARIANTS}:
                candidate.unlink()

    def _snapshot_skill_files(self, skill_root: Path) -> Dict[str, str]:
        """Capture text files from a skill directory for review heuristics."""

        files: Dict[str, str] = {}
        for file_path in skill_root.rglob("*"):
            if not file_path.is_file():
                continue
            try:
                if file_path.stat().st_size > self.MAX_TEXT_FILE_BYTES:
                    continue
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = file_path.relative_to(skill_root).as_posix()
            files[relative] = content
        return files

    def _normalize_search_result(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize `/api/v1/search` skill search records."""

        slug = str(item.get("slug") or "").strip()
        if not slug:
            return None
        listing = {
            "id": slug,
            "name": str(item.get("displayName") or slug),
            "version": str(item.get("version") or ""),
            "description": str(item.get("summary") or ""),
            "author": "ClawHub",
            "homepage": f"{self.base_url}/skills/{slug}",
            "repository": "",
            "category": "remote",
            "tags": ["clawhub", "remote"],
            "source": "remote",
            "source_label": "ClawHub",
            "updated_at": item.get("updatedAt"),
            "score": item.get("score"),
        }
        self._merge_marketplace_stats(listing, item)
        return listing

    def _normalize_list_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize `/api/v1/skills` list records."""

        slug = str(item.get("slug") or "").strip()
        if not slug:
            return None
        latest_version = item.get("latestVersion") or {}
        metadata = item.get("metadata") or {}
        tags = item.get("tags") or {}
        tag_list = list(tags.keys()) if isinstance(tags, dict) else []
        system_list = metadata.get("systems") if isinstance(metadata, dict) else None
        category = (
            system_list[0]
            if isinstance(system_list, list) and system_list and str(system_list[0]).strip()
            else "remote"
        )
        listing = {
            "id": slug,
            "name": str(item.get("displayName") or slug),
            "version": str((latest_version or {}).get("version") or ""),
            "description": str(item.get("summary") or ""),
            "author": "ClawHub",
            "homepage": f"{self.base_url}/skills/{slug}",
            "repository": "",
            "category": str(category),
            "tags": [*tag_list, "clawhub", "remote"],
            "source": "remote",
            "source_label": "ClawHub",
            "updated_at": item.get("updatedAt"),
        }
        self._merge_marketplace_stats(listing, item)
        return listing

    def _normalize_skill_detail(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize `/api/v1/skills/{slug}` detail records."""

        skill = payload.get("skill") or {}
        if not isinstance(skill, dict):
            return None

        slug = str(skill.get("slug") or "").strip()
        if not slug:
            return None

        latest_version = payload.get("latestVersion") or {}
        owner = payload.get("owner") or {}
        metadata = payload.get("metadata") or {}
        tags = skill.get("tags") or {}
        author = str(owner.get("displayName") or owner.get("handle") or "ClawHub")
        detail = {
            "id": slug,
            "slug": slug,
            "name": str(skill.get("displayName") or slug),
            "version": str((latest_version or {}).get("version") or ""),
            "description": str(skill.get("summary") or ""),
            "author": author,
            "owner": author,
            "homepage": f"{self.base_url}/skills/{slug}",
            "repository": "",
            "category": "remote",
            "tags": list(tags.keys()) if isinstance(tags, dict) else [],
            "metadata": {
                "owner_handle": owner.get("handle"),
                "owner_display_name": owner.get("displayName"),
                "created_at": skill.get("createdAt"),
                "updated_at": skill.get("updatedAt"),
                "os": metadata.get("os") if isinstance(metadata, dict) else None,
                "systems": metadata.get("systems") if isinstance(metadata, dict) else None,
                "registry": self.base_url,
                "source": "clawhub",
            },
            "updated_at": skill.get("updatedAt"),
            "created_at": skill.get("createdAt"),
            "raw": payload,
        }
        self._merge_marketplace_stats(detail, payload)
        self._merge_marketplace_stats(detail, skill)
        return detail

    def _listing_missing_marketplace_stats(self, listing: Dict[str, Any]) -> bool:
        """Return True when no marketplace stat was normalized for a listing."""

        return not any(
            listing.get(field) is not None
            for field in ("stars", "downloads", "current_installs", "all_time_installs")
        )

    def _merge_marketplace_stats(self, listing: Dict[str, Any], payload: Dict[str, Any]) -> None:
        """Best-effort extraction for ClawHub popularity/install counters."""

        stats = payload.get("stats") if isinstance(payload, dict) else None
        sources = [stats, payload]
        field_aliases = {
            "stars": ("stars", "starCount", "star_count"),
            "downloads": ("downloads", "downloadCount", "download_count"),
            "current_installs": (
                "current_installs",
                "installsCurrent",
                "currentInstalls",
                "current_install_count",
                "currentInstallsCount",
            ),
            "all_time_installs": (
                "installsAllTime",
                "allTimeInstalls",
                "all_time_installs",
                "installCount",
                "install_count",
                "totalInstalls",
                "total_install_count",
            ),
        }

        for target_field, aliases in field_aliases.items():
            if listing.get(target_field) is not None:
                continue
            for source in sources:
                if not isinstance(source, dict):
                    continue
                for alias in aliases:
                    value = self._coerce_stat_number(source.get(alias))
                    if value is not None:
                        listing[target_field] = value
                        break
                if listing.get(target_field) is not None:
                    break

    def _coerce_stat_number(self, value: Any) -> Optional[int]:
        """Convert API stat payloads into compact integer counters."""

        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            normalized = value.strip().replace(",", "")
            if not normalized:
                return None
            try:
                return int(float(normalized))
            except ValueError:
                return None
        return None

    def _matches_category(self, listing: Dict[str, Any], category: Optional[str]) -> bool:
        """Apply best-effort local category filtering for ClawHub listings."""

        normalized_category = str(category or "").strip().lower()
        if not normalized_category:
            return True

        haystacks = [
            str(listing.get("category") or "").lower(),
            " ".join(str(tag).lower() for tag in listing.get("tags", []) or []),
        ]
        return any(normalized_category in haystack for haystack in haystacks)


class LocalSkillRegistry:
    """Local skill registry for managing downloaded skills."""

    def __init__(self):
        """Initialize local skill registry."""
        self.settings = get_settings()
        self.skills_dir = self.settings.managed_skills_dir
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


class BundledSkillCatalog:
    """Browse installable bundled skills shipped with the project."""

    def __init__(self):
        self.settings = get_settings()
        self.catalog_dir = self.settings.bundled_skill_catalog_dir
        self.catalog_dir.mkdir(parents=True, exist_ok=True)

    def search_skills(self, query: str = "", category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search bundled installable skills."""
        normalized_query = query.strip().lower()
        normalized_category = (category or "").strip().lower()
        results: List[Dict[str, Any]] = []

        for skill_path in self._iter_catalog_paths():
            bundle = self._load_bundle(skill_path)
            if bundle is None:
                continue
            skill_id = self._resolve_skill_id(skill_path, bundle)
            listing = self._build_listing(skill_id, bundle)
            haystacks = [
                skill_id.lower(),
                str(listing.get("name", "")).lower(),
                str(listing.get("description", "")).lower(),
                " ".join(str(tag).lower() for tag in listing.get("tags", [])),
                str(listing.get("category", "")).lower(),
            ]
            if normalized_query and not any(normalized_query in haystack for haystack in haystacks):
                continue
            if normalized_category and normalized_category != str(listing.get("category", "")).lower():
                continue
            results.append(listing)

        results.sort(key=lambda item: (str(item.get("source", "")), str(item.get("name", ""))))
        return results

    def get_skill_detail(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Return detail for a bundled installable skill."""
        bundle = self.fetch_skill_bundle(skill_id)
        if bundle is None:
            return None
        detail = self._build_listing(skill_id, bundle)
        metadata = bundle.get("metadata", {}) or {}
        detail.update(
            {
                "repository": metadata.get("repository"),
                "homepage": metadata.get("homepage"),
                "metadata": metadata,
            }
        )
        return detail

    def fetch_skill_bundle(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Return the raw bundle for an installable bundled skill."""
        normalized_skill_id = skill_id.strip().lower()
        if not normalized_skill_id:
            return None

        for skill_path in self._iter_catalog_paths():
            bundle = self._load_bundle(skill_path)
            if bundle is None:
                continue
            resolved_id = self._resolve_skill_id(skill_path, bundle)
            if resolved_id.lower() == normalized_skill_id:
                return bundle
        return None

    def _iter_catalog_paths(self) -> List[Path]:
        """Return candidate bundled skill definition files."""
        from localclaw.skills.loader import SkillLoader

        loader = SkillLoader()
        return list(loader._iter_skill_paths(self.catalog_dir, recursive=True))

    def _load_bundle(self, skill_path: Path) -> Optional[Dict[str, Any]]:
        """Load a bundled skill into a bundle dict suitable for installation."""
        from localclaw.skills.loader import SkillLoader

        loader = SkillLoader()
        try:
            raw_data = loader._load_skill_data(skill_path)
            if not isinstance(raw_data, dict):
                return None
            prepared = loader._prepare_skill_data(raw_data, skill_path)
            bundle = dict(prepared)
            metadata = dict(bundle.get("metadata", {}))
            metadata.setdefault("catalog_source", "bundled")
            metadata.setdefault("catalog_path", str(skill_path))
            bundle["metadata"] = metadata
            return bundle
        except Exception as exc:
            logger.error("Error loading bundled catalog skill %s: %s", skill_path, exc)
            return None

    def _resolve_skill_id(self, skill_path: Path, bundle: Dict[str, Any]) -> str:
        """Resolve the marketplace-visible ID for a bundled skill."""
        metadata = bundle.get("metadata", {}) or {}
        openclaw = metadata.get("openclaw", {}) or {}
        candidates = [
            metadata.get("catalog_id"),
            metadata.get("skill_key"),
            openclaw.get("skillKey"),
            bundle.get("name"),
        ]
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        if skill_path.name.upper() == "SKILL.MD":
            return skill_path.parent.name
        return skill_path.stem

    def _build_listing(self, skill_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
        """Build marketplace metadata for a bundled skill."""
        metadata = bundle.get("metadata", {}) or {}
        tags = metadata.get("tags") or metadata.get("keywords") or []
        if not isinstance(tags, list):
            tags = [tags]
        display_name = (
            str(metadata.get("display_name") or metadata.get("title") or bundle.get("name", skill_id)).strip()
            or skill_id
        )
        return {
            "id": skill_id,
            "name": display_name,
            "version": bundle.get("version", "1.0.0"),
            "description": bundle.get("description", ""),
            "author": metadata.get("author", "LocalClaw"),
            "homepage": metadata.get("homepage"),
            "repository": metadata.get("repository"),
            "category": metadata.get("category", "bundled"),
            "tags": [str(tag) for tag in tags if str(tag).strip()],
            "source": "bundled",
            "source_label": "Bundled",
        }


def resolve_bundle_skill_id(bundle: Dict[str, Any], fallback: str = "uploaded-skill") -> str:
    """Resolve the canonical install ID for a bundle."""

    metadata = bundle.get("metadata", {}) or {}
    openclaw = metadata.get("openclaw", {}) or {}
    candidates = [
        metadata.get("catalog_id"),
        metadata.get("skill_key"),
        openclaw.get("skillKey"),
        bundle.get("name"),
        fallback,
    ]
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return fallback


def build_skill_detail_from_bundle(
    skill_id: str,
    bundle: Dict[str, Any],
    source: str = "upload",
) -> Dict[str, Any]:
    """Build a detail payload for reviews from a local bundle."""

    metadata = bundle.get("metadata", {}) or {}
    tags = metadata.get("tags") or metadata.get("keywords") or []
    if not isinstance(tags, list):
        tags = [tags]
    return {
        "id": skill_id,
        "slug": skill_id,
        "name": str(bundle.get("name") or skill_id),
        "version": str(bundle.get("version") or "1.0.0"),
        "description": str(bundle.get("description") or ""),
        "author": str(bundle.get("author") or metadata.get("author") or "Local upload"),
        "owner": str(bundle.get("author") or metadata.get("author") or "Local upload"),
        "homepage": bundle.get("homepage") or metadata.get("homepage") or "",
        "repository": bundle.get("repository") or metadata.get("repository") or "",
        "category": str(metadata.get("category") or source),
        "tags": [str(tag) for tag in tags if str(tag).strip()],
        "metadata": {
            "registry": "local-upload",
            "source": source,
            "source_format": metadata.get("source_format"),
        },
    }


def _extract_uploaded_archive(archive_path: Path, extract_dir: Path) -> None:
    """Extract a local uploaded archive into a directory."""

    lowered_name = archive_path.name.lower()
    if lowered_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zip_file:
            zip_file.extractall(extract_dir)
        return

    if lowered_name.endswith((".tar", ".tgz", ".tar.gz")):
        with tarfile.open(archive_path, "r:*") as tar_file:
            tar_file.extractall(extract_dir)
        return

    raise ValueError(f"Unsupported archive format: {archive_path.name}")


def _find_primary_skill_path(base_dir: Path, loader: SkillLoader) -> Optional[Path]:
    """Locate the primary skill definition path inside an extracted upload."""

    own_skill_md = base_dir / loader.SKILL_MARKDOWN_NAME
    if own_skill_md.exists():
        return own_skill_md

    candidates = list(loader._iter_skill_paths(base_dir, recursive=True))
    return candidates[0] if candidates else None


def build_uploaded_skill_bundle(upload_name: str, payload: bytes) -> Tuple[str, Dict[str, Any], Path]:
    """Turn an uploaded local skill artifact into a bundle suitable for review/install."""

    filename = Path(str(upload_name or "").strip()).name or "uploaded-skill"
    lowered_name = filename.lower()
    if not any(
        lowered_name.endswith(suffix)
        for suffix in [*UPLOAD_ARCHIVE_SUFFIXES, *UPLOAD_FILE_SUFFIXES]
    ):
        raise ValueError("Supported uploads are .zip, .tar, .tgz, .tar.gz, .json, .yaml, .yml, and .md")

    temp_dir = Path(tempfile.mkdtemp(prefix="localclaw-upload-"))
    upload_path = temp_dir / filename
    upload_path.write_bytes(payload)
    loader = SkillLoader()

    bundle: Dict[str, Any]
    source_root: Optional[Path] = None

    if any(lowered_name.endswith(suffix) for suffix in UPLOAD_ARCHIVE_SUFFIXES):
        extract_dir = temp_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _extract_uploaded_archive(upload_path, extract_dir)
        skill_path = _find_primary_skill_path(extract_dir, loader)
        if skill_path is None:
            raise ValueError("Uploaded archive does not contain a recognizable skill definition or SKILL.md")
        raw_data = loader._load_skill_data(skill_path)
        bundle = dict(loader._prepare_skill_data(raw_data, skill_path))
        source_root = skill_path.parent
        bundle["files"] = ClawHubClient()._snapshot_skill_files(source_root)
    else:
        source_path = upload_path
        if lowered_name.endswith(".md") and upload_path.name.upper() != ClawHubClient.SKILL_MARKDOWN_NAME:
            source_path = temp_dir / ClawHubClient.SKILL_MARKDOWN_NAME
            upload_path.replace(source_path)
        raw_data = loader._load_skill_data(source_path)
        bundle = dict(loader._prepare_skill_data(raw_data, source_path))
        try:
            bundle["files"] = {source_path.name: source_path.read_text(encoding="utf-8")}
        except UnicodeDecodeError:
            bundle["files"] = {}

    metadata = dict(bundle.get("metadata", {}) or {})
    metadata["upload_name"] = filename
    metadata["upload_source"] = "local_file"
    bundle["metadata"] = metadata

    if source_root is not None:
        bundle["_localclaw_source_dir"] = str(source_root)

    skill_id = resolve_bundle_skill_id(bundle, fallback=Path(filename).stem or "uploaded-skill")
    return skill_id, bundle, temp_dir


def _sanitize_uploaded_relative_path(filename: str) -> PurePosixPath:
    """Validate and normalize relative upload paths from directory uploads."""

    normalized = PurePosixPath(str(filename or "").replace("\\", "/"))
    parts = [part for part in normalized.parts if part not in ("", ".")]
    if not parts:
        raise ValueError("Uploaded directory entry is missing a valid relative path.")
    if normalized.is_absolute() or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe upload path: {filename}")
    return PurePosixPath(*parts)


def build_uploaded_skill_bundle_from_files(
    uploaded_files: List[Tuple[str, bytes]],
) -> Tuple[str, Dict[str, Any], Path]:
    """Turn a set of uploaded files into a bundle suitable for review/install."""

    if not uploaded_files:
        raise ValueError("Uploaded directory is empty.")

    temp_dir = Path(tempfile.mkdtemp(prefix="localclaw-upload-"))
    upload_root = temp_dir / "upload"
    upload_root.mkdir(parents=True, exist_ok=True)

    for filename, payload in uploaded_files:
        relative_path = _sanitize_uploaded_relative_path(filename)
        target_path = upload_root.joinpath(*relative_path.parts)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)

    loader = SkillLoader()
    skill_path = _find_primary_skill_path(upload_root, loader)
    if skill_path is None:
        raise ValueError("Uploaded directory does not contain a recognizable skill definition or SKILL.md")

    raw_data = loader._load_skill_data(skill_path)
    bundle = dict(loader._prepare_skill_data(raw_data, skill_path))
    bundle["files"] = ClawHubClient()._snapshot_skill_files(skill_path.parent)

    metadata = dict(bundle.get("metadata", {}) or {})
    top_level_dir = _sanitize_uploaded_relative_path(uploaded_files[0][0]).parts[0]
    metadata["upload_name"] = f"{top_level_dir}/"
    metadata["upload_source"] = "local_directory"
    bundle["metadata"] = metadata
    bundle["_localclaw_source_dir"] = str(skill_path.parent)

    skill_id = resolve_bundle_skill_id(bundle, fallback=top_level_dir or "uploaded-skill")
    return skill_id, bundle, temp_dir


def cleanup_uploaded_skill_bundle(temp_dir: Optional[Path]) -> None:
    """Delete temporary files created while staging a local upload."""

    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_clawhub_client() -> ClawHubClient:
    """Get the ClawHub client instance."""
    return ClawHubClient()


def get_local_registry() -> LocalSkillRegistry:
    """Get the local skill registry instance."""
    return LocalSkillRegistry()


def get_bundled_skill_catalog() -> BundledSkillCatalog:
    """Get the bundled installable skill catalog."""
    return BundledSkillCatalog()

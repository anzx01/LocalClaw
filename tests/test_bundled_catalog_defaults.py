"""Tests for the default bundled installable skill catalog."""

from localclaw.skills.registry.clawhub import BundledSkillCatalog


def test_default_bundled_catalog_includes_requested_openclaw_style_skills():
    """The repo-shipped catalog should expose the requested default installable skills."""

    catalog = BundledSkillCatalog()
    skills = catalog.search_skills()
    skill_ids = {skill["id"] for skill in skills}

    assert {
        "skill-vetter",
        "agent-browser",
        "tavily-web-search",
        "find-skills",
        "weather",
        "self-improving-agent",
        "summarize",
        "humanizer",
    }.issubset(skill_ids)

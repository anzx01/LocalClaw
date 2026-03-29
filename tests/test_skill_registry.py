"""Tests for skill registry persistence and approval policy helpers."""

from localclaw.skills.base import create_skill_from_dict
from localclaw.skills.registry import SkillRegistry


def test_skill_registry_persists_approval_policy(tmp_path):
    policy_path = tmp_path / "skill_policies.json"

    registry = SkillRegistry(policy_store_path=policy_path)
    registry.register(
        create_skill_from_dict(
            {
                "name": "repo.fs",
                "version": "1.0.0",
                "description": "Workspace file skill",
                "type": "workflow",
                "metadata": {"skill_key": "repo.fs"},
            }
        )
    )
    assert registry.set_skill_approval_required("repo.fs", True) is True

    reloaded = SkillRegistry(policy_store_path=policy_path)
    reloaded.register(
        create_skill_from_dict(
            {
                "name": "repo.fs",
                "version": "1.0.0",
                "description": "Workspace file skill",
                "type": "workflow",
                "metadata": {"skill_key": "repo.fs"},
            }
        )
    )

    assert reloaded.get_skill_approval_required("repo.fs") is True
    assert reloaded.get_skill_info("repo.fs")["require_approval"] is True

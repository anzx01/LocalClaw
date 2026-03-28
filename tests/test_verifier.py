"""Tests for verifier risk classification and approval behavior."""

import asyncio

from localclaw.config.settings import Settings
from localclaw.core.models import Context, RiskLevel, Step, StepType
from localclaw.core.verifier import PermissionVerifier, VerificationDecision


def _browser_step(request: str) -> Step:
    return Step(
        type=StepType.TOOL_CALL,
        name="run_web_access",
        tool_name="browser_cdp",
        input={"request": request, "url": ""},
    )


def test_browser_cdp_readonly_date_query_is_low_risk():
    verifier = PermissionVerifier(settings=Settings(_env_file=None))
    step = _browser_step("What day is today?")

    assert verifier.get_risk_level(step) == RiskLevel.LOW


def test_browser_cdp_mutating_instruction_stays_high_risk():
    verifier = PermissionVerifier(settings=Settings(_env_file=None))
    step = _browser_step("Open bank website and click transfer submit")

    assert verifier.get_risk_level(step) == RiskLevel.HIGH


def test_verify_step_auto_approves_low_risk_browser_query():
    verifier = PermissionVerifier(settings=Settings(_env_file=None))
    step = _browser_step("明天北京天气怎么样？")

    result = asyncio.run(verifier.verify_step(step, Context()))

    assert result.decision == VerificationDecision.PASS
    assert "Auto-approved" in result.message


def test_browser_cdp_auto_approve_can_be_disabled():
    settings = Settings(_env_file=None, browser_cdp_auto_approve_readonly=False)
    verifier = PermissionVerifier(settings=settings)
    step = _browser_step("What day is today?")

    assert verifier.get_risk_level(step) == RiskLevel.HIGH

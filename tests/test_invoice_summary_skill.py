"""Tests for PDF extraction and invoice summary skills."""

from pathlib import Path

import pytest

from localclaw.config.settings import Settings
from localclaw.core.engine import ExecutionEngine
from localclaw.core.models import Intent, Message, TaskState
from localclaw.core.planner import create_default_planner
from localclaw.core.verifier import create_default_verifier
from localclaw.llm.provider import LLMConfig, LLMProvider, LLMProviderType, set_llm_provider
from localclaw.skills.loader import SkillLoader
from localclaw.skills.registry import SkillRegistry
from localclaw.tools.base import ToolRegistry
from localclaw.tools.file_tool import PdfExtractTool
from localclaw.tools.local_model_tool import LocalModelPromptTool


class _InvoiceSummaryParser:
    """Parser stub that routes directly to the invoice-summary skill."""

    def __init__(self, dir1: str, dir2: str) -> None:
        self._dir1 = dir1
        self._dir2 = dir2

    async def parse(self, message):
        return Intent(
            intent="skill.invoice-summary",
            params={"dir1": self._dir1, "dir2": self._dir2},
            raw_message=message.content,
        )


class _FakeProvider(LLMProvider):
    """Small local-model stub for skill prompt tests."""

    def __init__(self) -> None:
        super().__init__(LLMConfig(provider_type=LLMProviderType.MOCK, model="fake-local"))

    async def generate(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
        from localclaw.llm.provider import LLMResponse

        assert "PDF内容（目录1）" in prompt
        assert "beijing/hotel_001.pdf" in prompt
        assert "changsha/hotel_001.pdf" in prompt
        return LLMResponse(content="发票汇总完成", model="fake-local", provider="mock")

    async def chat(self, messages, max_tokens=None, temperature=None):
        from localclaw.llm.provider import LLMResponse

        return LLMResponse(content="unused", model="fake-local", provider="mock")

    async def is_available(self):
        return True


@pytest.mark.asyncio
async def test_pdf_extract_tool_reads_directory_of_pdfs():
    repo_root = Path(__file__).resolve().parents[1]
    invoices_dir = repo_root / "test_invoices" / "beijing"
    tool = PdfExtractTool(base_dir=repo_root)

    result = await tool.execute(path=str(invoices_dir))

    assert result.status == "success"
    assert result.data["count"] == 2
    assert {item["name"] for item in result.data["files"]} == {"hotel_001.pdf", "taxi_002.pdf"}
    assert "### beijing/hotel_001.pdf" in result.data["content"]
    assert "### beijing/taxi_002.pdf" in result.data["content"]


@pytest.mark.asyncio
async def test_invoice_summary_skill_runs_without_shell_approval():
    repo_root = Path(__file__).resolve().parents[1]
    beijing_dir = repo_root / "test_invoices" / "beijing"
    changsha_dir = repo_root / "test_invoices" / "changsha"

    set_llm_provider(_FakeProvider())

    registry = SkillRegistry()
    loader = SkillLoader(registry)
    skill = loader.load_from_file(repo_root / "bundled_skills" / "invoice-summary" / "SKILL.md")
    assert skill is not None
    registry.register(skill, enable=True)

    tool_registry = ToolRegistry()
    tool_registry.register(PdfExtractTool(base_dir=repo_root))
    tool_registry.register(LocalModelPromptTool())

    engine = ExecutionEngine(
        settings=Settings(_env_file=None, llm_enabled=True, llm_parse_only=False),
        parser=_InvoiceSummaryParser(str(beijing_dir), str(changsha_dir)),
        planner=create_default_planner(),
        verifier=create_default_verifier(settings=Settings(_env_file=None), skill_registry=registry),
        tool_registry=tool_registry,
        skill_registry=registry,
    )

    task = await engine.process_message(Message(content="汇总发票", user_id="u1", channel="test"))

    assert task.state == TaskState.COMPLETED
    assert [step.tool_name for step in task.plan.steps] == ["pdf_extract", "pdf_extract", "_local_model_prompt"]
    assert task.result.data[task.plan.steps[-1].id]["content"] == "发票汇总完成"

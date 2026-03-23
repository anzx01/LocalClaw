"""Integration tests for Phase 3 components."""

import asyncio
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from localclaw.agents.config import AgentConfig, AgentLoader, create_default_agent
from localclaw.agents.manager import AgentManager, get_agent_manager
from localclaw.events.scheduler import EventScheduler, get_scheduler
from localclaw.events.triggers import TriggerManager, TriggerConfig, TriggerType
from localclaw.security.sandbox import SandboxExecutor, SandboxConfig
from localclaw.security.hitl import HITLManager, ApprovalStatus
from localclaw.llm.provider import LLMConfig, LLMProviderType, MockLLMProvider
from localclaw.llm.ollama import OllamaClient, OllamaConfig


class TestAgentConfig:
    """Tests for AgentConfig."""
    
    def test_create_agent_config(self):
        config = AgentConfig(
            name="test_agent",
            description="Test agent",
            skills=["skill1", "skill2"],
            tools=["tool1"],
        )
        
        assert config.name == "test_agent"
        assert config.description == "Test agent"
        assert config.skills == ["skill1", "skill2"]
        assert config.tools == ["tool1"]
        assert config.enabled is True
        assert config.is_default is False
    
    def test_can_use_skill(self):
        config = AgentConfig(
            name="test",
            skills=["allowed_skill"],
        )
        
        assert config.can_use_skill("allowed_skill") is True
        assert config.can_use_skill("forbidden_skill") is False
    
    def test_can_use_skill_empty_means_all(self):
        config = AgentConfig(name="test", skills=[])
        
        assert config.can_use_skill("any_skill") is True
    
    def test_get_max_risk_level_value(self):
        low = AgentConfig(name="low", max_risk_level="low")
        medium = AgentConfig(name="medium", max_risk_level="medium")
        high = AgentConfig(name="high", max_risk_level="high")
        critical = AgentConfig(name="critical", max_risk_level="critical")
        
        assert low.get_max_risk_level_value() == 1
        assert medium.get_max_risk_level_value() == 2
        assert high.get_max_risk_level_value() == 3
        assert critical.get_max_risk_level_value() == 4
    
    def test_to_dict_and_from_dict(self):
        original = AgentConfig(
            name="test",
            description="Test",
            skills=["s1"],
            tools=["t1"],
            max_risk_level="high",
        )
        
        data = original.to_dict()
        restored = AgentConfig.from_dict(data)
        
        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.skills == original.skills
        assert restored.tools == original.tools
        assert restored.max_risk_level == original.max_risk_level
    
    def test_create_default_agent(self):
        default = create_default_agent()
        
        assert default.name == "default"
        assert default.is_default is True
        assert default.enabled is True


class TestAgentManager:
    """Tests for AgentManager."""
    
    def test_manager_has_default_agent(self):
        manager = AgentManager()
        
        assert "default" in manager.list_agents()
        assert manager.get_agent("default") is not None
    
    def test_register_agent(self):
        manager = AgentManager()
        config = AgentConfig(name="custom", skills=["skill1"])
        
        manager.register_agent(config)
        
        assert "custom" in manager.list_agents()
        assert manager.get_agent("custom") == config
    
    def test_unregister_agent(self):
        manager = AgentManager()
        config = AgentConfig(name="removable")
        manager.register_agent(config)
        
        result = manager.unregister_agent("removable")
        
        assert result is True
        assert "removable" not in manager.list_agents()
    
    def test_cannot_unregister_default(self):
        manager = AgentManager()
        
        result = manager.unregister_agent("default")
        
        assert result is False
        assert "default" in manager.list_agents()
    
    def test_enable_disable_agent(self):
        manager = AgentManager()
        config = AgentConfig(name="toggleable")
        manager.register_agent(config)
        
        manager.disable_agent("toggleable")
        assert manager.get_agent("toggleable").enabled is False
        
        manager.enable_agent("toggleable")
        assert manager.get_agent("toggleable").enabled is True
    
    def test_cannot_disable_default(self):
        manager = AgentManager()
        
        result = manager.disable_agent("default")
        
        assert result is False


class TestEventScheduler:
    """Tests for EventScheduler."""
    
    @pytest.mark.asyncio
    async def test_scheduler_start_stop(self):
        scheduler = EventScheduler()
        
        await scheduler.start()
        assert scheduler._running is True
        
        await scheduler.stop()
        assert scheduler._running is False
    
    @pytest.mark.asyncio
    async def test_add_interval_task(self):
        scheduler = EventScheduler()
        call_count = 0
        
        def handler():
            nonlocal call_count
            call_count += 1
        
        task = scheduler.add_interval_task(
            task_id="test_interval",
            handler=handler,
            seconds=1,
        )
        
        assert task.task_id == "test_interval"
        assert task.trigger_type == "interval"
        assert "test_interval" in scheduler.list_tasks()
    
    @pytest.mark.asyncio
    async def test_add_cron_task(self):
        scheduler = EventScheduler()
        
        task = scheduler.add_cron_task(
            task_id="test_cron",
            handler=lambda: None,
            cron_expression="0 * * * *",
        )
        
        assert task.task_id == "test_cron"
        assert task.trigger_type == "cron"
    
    @pytest.mark.asyncio
    async def test_remove_task(self):
        scheduler = EventScheduler()
        scheduler.add_interval_task(
            task_id="removable",
            handler=lambda: None,
            seconds=60,
        )
        
        result = scheduler.remove_task("removable")
        
        assert result is True
        assert "removable" not in scheduler.list_tasks()


class TestTriggerManager:
    """Tests for TriggerManager."""
    
    @pytest.mark.asyncio
    async def test_register_trigger(self):
        manager = TriggerManager()
        config = TriggerConfig(
            trigger_id="test_trigger",
            trigger_type=TriggerType.CONDITION,
            condition="value > 10",
        )
        
        instance = manager.register_trigger(config, lambda ctx: None)
        
        assert "test_trigger" in manager.list_triggers()
    
    @pytest.mark.asyncio
    async def test_condition_trigger(self):
        manager = TriggerManager()
        triggered = []
        
        config = TriggerConfig(
            trigger_id="cond_trigger",
            trigger_type=TriggerType.CONDITION,
            condition="value > 10",
        )
        
        manager.register_trigger(config, lambda ctx: triggered.append(ctx))
        
        await manager.check_condition_triggers({"value": 5})
        assert len(triggered) == 0
        
        await manager.check_condition_triggers({"value": 15})
        assert len(triggered) == 1
    
    @pytest.mark.asyncio
    async def test_event_trigger(self):
        manager = TriggerManager()
        events = []
        
        config = TriggerConfig(
            trigger_id="event_trigger",
            trigger_type=TriggerType.EVENT,
            event_pattern=r"user\..*",
        )
        
        manager.register_trigger(config, lambda ctx: events.append(ctx))
        
        count = await manager.emit_event("user.login", {"user_id": "123"})
        assert count == 1
        assert len(events) == 1
        
        count = await manager.emit_event("system.start", {})
        assert count == 0
    
    @pytest.mark.asyncio
    async def test_enable_disable_trigger(self):
        manager = TriggerManager()
        config = TriggerConfig(
            trigger_id="toggle",
            trigger_type=TriggerType.CONDITION,
            condition="True",
        )
        manager.register_trigger(config, lambda ctx: None)
        
        manager.disable_trigger("toggle")
        assert manager.get_trigger("toggle").config.enabled is False
        
        manager.enable_trigger("toggle")
        assert manager.get_trigger("toggle").config.enabled is True


class TestSandboxExecutor:
    """Tests for SandboxExecutor."""
    
    @pytest.mark.asyncio
    async def test_validate_code_allowed_import(self):
        executor = SandboxExecutor()
        
        valid, error = executor.validate_code("import json")
        assert valid is True
        
        valid, error = executor.validate_code("import os")
        assert valid is False
        assert "os" in error
    
    @pytest.mark.asyncio
    async def test_validate_code_forbidden_function(self):
        executor = SandboxExecutor()
        
        valid, error = executor.validate_code("eval('1+1')")
        assert valid is False
        assert "eval" in error
    
    @pytest.mark.asyncio
    async def test_execute_direct(self):
        config = SandboxConfig(enabled=False)
        executor = SandboxExecutor(config)
        
        result = await executor.execute_python("result = {'value': 1 + 1}")
        
        assert result.status == "success"
    
    @pytest.mark.asyncio
    async def test_execute_with_context(self):
        config = SandboxConfig(enabled=False)
        executor = SandboxExecutor(config)
        
        result = await executor.execute_python(
            "result = {'value': x * 2}",
            context={"x": 5},
        )
        
        assert result.status == "success"


class TestHITLManager:
    """Tests for HITLManager."""
    
    def test_requires_approval_high_risk(self):
        from localclaw.core.models import Step, StepType, RiskLevel
        
        manager = HITLManager()
        step = Step(type=StepType.TOOL_CALL, tool_name="shell")
        
        assert manager.requires_approval(step, RiskLevel.HIGH) is True
        assert manager.requires_approval(step, RiskLevel.CRITICAL) is True
    
    def test_requires_approval_low_risk_with_auto_approve(self):
        from localclaw.core.models import Step, StepType, RiskLevel
        
        manager = HITLManager()
        manager.set_auto_approve_low_risk(True)
        step = Step(type=StepType.TOOL_CALL, tool_name="file_read")
        
        assert manager.requires_approval(step, RiskLevel.LOW) is False
    
    @pytest.mark.asyncio
    async def test_request_approval(self):
        from localclaw.core.models import Step, StepType, RiskLevel
        
        manager = HITLManager()
        step = Step(type=StepType.TOOL_CALL, tool_name="shell")
        
        request = await manager.request_approval(
            step=step,
            risk_level=RiskLevel.HIGH,
            reason="Shell command execution",
        )
        
        assert request.status == ApprovalStatus.PENDING
        assert request.request_id in manager._requests
    
    def test_approve_request(self):
        from localclaw.core.models import Step, StepType, RiskLevel
        
        manager = HITLManager()
        step = Step(type=StepType.TOOL_CALL, tool_name="shell")
        
        async def create_and_approve():
            request = await manager.request_approval(
                step=step,
                risk_level=RiskLevel.HIGH,
                reason="Test",
            )
            result = manager.approve(request.request_id, approver="admin")
            return request, result
        
        request, result = asyncio.run(create_and_approve())
        
        assert result is True
        assert request.status == ApprovalStatus.APPROVED
    
    def test_reject_request(self):
        from localclaw.core.models import Step, StepType, RiskLevel
        
        manager = HITLManager()
        step = Step(type=StepType.TOOL_CALL, tool_name="shell")
        
        async def create_and_reject():
            request = await manager.request_approval(
                step=step,
                risk_level=RiskLevel.HIGH,
                reason="Test",
            )
            result = manager.reject(request.request_id, reason="Not allowed")
            return request, result
        
        request, result = asyncio.run(create_and_reject())
        
        assert result is True
        assert request.status == ApprovalStatus.REJECTED
    
    def test_get_stats(self):
        from localclaw.core.models import Step, StepType, RiskLevel
        
        manager = HITLManager()
        
        stats = manager.get_stats()
        
        assert "total_requests" in stats
        assert "pending" in stats
        assert "approved" in stats
        assert "rejected" in stats


class TestLLMProvider:
    """Tests for LLM providers."""
    
    @pytest.mark.asyncio
    async def test_mock_provider_generate(self):
        config = LLMConfig(
            provider_type=LLMProviderType.MOCK,
            model="test-model",
        )
        provider = MockLLMProvider(config)
        
        response = await provider.generate("Hello, world!")
        
        assert response.provider == "mock"
        assert response.model == "test-model"
        assert "Mock response" in response.content
    
    @pytest.mark.asyncio
    async def test_mock_provider_chat(self):
        provider = MockLLMProvider(LLMConfig())
        
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        
        response = await provider.chat(messages)
        
        assert response.provider == "mock"
        assert "Mock chat response" in response.content
    
    @pytest.mark.asyncio
    async def test_mock_provider_available(self):
        provider = MockLLMProvider(LLMConfig())
        
        available = await provider.is_available()
        
        assert available is True


class TestOllamaClient:
    """Tests for Ollama client."""
    
    def test_ollama_config_defaults(self):
        config = OllamaConfig()
        
        assert config.base_url == "http://localhost:11434"
        assert config.model == "llama2"
        assert config.timeout == 120.0
    
    def test_ollama_client_set_model(self):
        client = OllamaClient()
        
        client.set_model("mistral")
        
        assert client._ollama_config.model == "mistral"
    
    @pytest.mark.asyncio
    async def test_ollama_is_available_mock(self):
        client = OllamaClient()
        
        available = await client.is_available()
        
        assert available is False


class TestOpenClawCompatibility:
    """Tests for OpenClaw skill compatibility."""
    
    def test_convert_openclaw_skill(self):
        from localclaw.skills.loader import SkillLoader
        
        loader = SkillLoader()
        
        openclaw_data = {
            "name": "test_skill",
            "version": "1.0.0",
            "description": "Test skill",
            "command": "echo",
            "args": {"text": "hello"},
        }
        
        converted = loader._convert_openclaw_to_localclaw(openclaw_data)
        
        assert converted["name"] == "test_skill"
        assert len(converted["actions"]) > 0
        assert converted["metadata"]["source"] == "openclaw"
    
    def test_convert_openclaw_steps(self):
        from localclaw.skills.loader import SkillLoader
        
        loader = SkillLoader()
        
        openclaw_data = {
            "name": "multi_step",
            "steps": [
                {"type": "tool_call", "tool": "file_read", "params": {"path": "/tmp/test"}},
                {"type": "transform", "template": "Result: {{data}}"},
            ],
        }
        
        converted = loader._convert_openclaw_to_localclaw(openclaw_data)
        
        assert len(converted["actions"]) == 2
        assert converted["actions"][0]["type"] == "tool_call"
        assert converted["actions"][1]["type"] == "transform"

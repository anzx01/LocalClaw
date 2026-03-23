"""Tests for gateway modules."""

import asyncio
import pytest
from datetime import datetime, timedelta

from localclaw.core.models import Message
from localclaw.gateway.gateway import Gateway, Session, SessionManager, MessageQueue
from localclaw.gateway.router import AgentRouter, AgentConfig, RoutingRule


class TestSession:
    """Tests for Session."""
    
    def test_session_creation(self):
        """Test session creation."""
        session = Session(
            session_id="test123",
            user_id="user1",
            channel="web",
        )
        
        assert session.session_id == "test123"
        assert session.user_id == "user1"
        assert session.channel == "web"
        assert isinstance(session.created_at, datetime)
    
    def test_session_touch(self):
        """Test session touch."""
        session = Session(
            session_id="test123",
            user_id="user1",
            channel="web",
        )
        
        old_time = session.last_activity
        import time
        time.sleep(0.01)
        session.touch()
        
        assert session.last_activity > old_time


class TestMessageQueue:
    """Tests for MessageQueue."""
    
    @pytest.mark.asyncio
    async def test_put_and_get(self):
        """Test basic put and get operations."""
        queue = MessageQueue()
        
        message = Message(content="test", user_id="user1")
        await queue.put(message)
        
        result = await queue.get()
        assert result.content == "test"
    
    @pytest.mark.asyncio
    async def test_queue_size(self):
        """Test queue size."""
        queue = MessageQueue()
        
        assert queue.size == 0
        
        await queue.put(Message(content="test1", user_id="user1"))
        await queue.put(Message(content="test2", user_id="user1"))
        
        assert queue.size == 2
    
    @pytest.mark.asyncio
    async def test_queue_full(self):
        """Test queue full behavior."""
        queue = MessageQueue(max_size=2)
        
        assert await queue.put(Message(content="test1", user_id="user1")) is True
        assert await queue.put(Message(content="test2", user_id="user1")) is True
        assert await queue.put(Message(content="test3", user_id="user1")) is False
    
    @pytest.mark.asyncio
    async def test_queue_processing(self):
        """Test queue processing with handlers."""
        queue = MessageQueue()
        processed = []
        
        async def handler(message):
            processed.append(message.content)
        
        queue.add_handler(handler)
        await queue.start()
        
        await queue.put(Message(content="test1", user_id="user1"))
        await queue.put(Message(content="test2", user_id="user1"))
        
        await asyncio.sleep(0.1)
        await queue.stop()
        
        assert "test1" in processed
        assert "test2" in processed


class TestSessionManager:
    """Tests for SessionManager."""
    
    def test_create_session(self):
        """Test session creation."""
        manager = SessionManager()
        
        session = manager.create_session("user1", "web")
        
        assert session.user_id == "user1"
        assert session.channel == "web"
        assert session.session_id in manager._sessions
    
    def test_get_session(self):
        """Test getting session."""
        manager = SessionManager()
        
        created = manager.create_session("user1", "web")
        retrieved = manager.get_session(created.session_id)
        
        assert retrieved is created
    
    def test_get_user_sessions(self):
        """Test getting user sessions."""
        manager = SessionManager()
        
        manager.create_session("user1", "web")
        manager.create_session("user1", "cli")
        
        sessions = manager.get_user_sessions("user1")
        assert len(sessions) == 2
    
    def test_end_session(self):
        """Test ending session."""
        manager = SessionManager()
        
        session = manager.create_session("user1", "web")
        result = manager.end_session(session.session_id)
        
        assert result is True
        assert manager.get_session(session.session_id) is None
    
    def test_cleanup_expired(self):
        """Test cleanup of expired sessions."""
        manager = SessionManager(session_timeout=0)
        
        manager.create_session("user1", "web")
        
        import time
        time.sleep(0.1)
        
        count = manager.cleanup_expired()
        assert count == 1


class TestGateway:
    """Tests for Gateway."""
    
    @pytest.mark.asyncio
    async def test_submit_message(self):
        """Test submitting a message."""
        gateway = Gateway()
        
        message = Message(content="test", user_id="user1")
        result = await gateway.submit_message(message)
        
        assert result is True
        assert gateway.queue.size == 1
    
    @pytest.mark.asyncio
    async def test_process_message_direct(self):
        """Test direct message processing."""
        gateway = Gateway()
        results = []
        
        async def handler(message):
            results.append(message.content)
            return "processed"
        
        gateway.add_message_handler(handler)
        
        message = Message(content="test", user_id="user1")
        result = await gateway.process_message_direct(message)
        
        assert result == "processed"
        assert "test" in results


class TestRoutingRule:
    """Tests for RoutingRule."""
    
    def test_pattern_match(self):
        """Test pattern matching."""
        rule = RoutingRule(
            name="test_rule",
            pattern=r"hello\s+\w+",
            agent_name="greeting_agent",
        )
        
        assert rule.matches("hello world", "user1") is True
        assert rule.matches("hi there", "user1") is False
    
    def test_keyword_match(self):
        """Test keyword matching."""
        rule = RoutingRule(
            name="test_rule",
            keywords=["weather", "temperature"],
            agent_name="weather_agent",
        )
        
        assert rule.matches("What's the weather?", "user1") is True
        assert rule.matches("What's the temperature?", "user1") is True
        assert rule.matches("Hello world", "user1") is False
    
    def test_user_id_match(self):
        """Test user ID matching with keywords."""
        rule = RoutingRule(
            name="test_rule",
            user_ids=["admin", "root"],
            keywords=["admin", "system"],
            agent_name="admin_agent",
        )
        
        assert rule.matches("admin command", "admin") is True
        assert rule.matches("system check", "root") is True
        assert rule.matches("any message", "user1") is False
        assert rule.matches("admin command", "user1") is False
    
    def test_combined_match(self):
        """Test combined matching."""
        rule = RoutingRule(
            name="test_rule",
            keywords=["weather"],
            user_ids=["user1"],
            agent_name="weather_agent",
        )
        
        assert rule.matches("What's the weather?", "user1") is True
        assert rule.matches("What's the weather?", "user2") is False
        assert rule.matches("Hello", "user1") is False


class TestAgentRouter:
    """Tests for AgentRouter."""
    
    def test_register_agent(self):
        """Test agent registration."""
        router = AgentRouter()
        
        config = AgentConfig(name="test_agent", description="Test agent")
        router.register_agent(config)
        
        assert "test_agent" in router.list_agents()
    
    def test_unregister_agent(self):
        """Test agent unregistration."""
        router = AgentRouter()
        
        config = AgentConfig(name="test_agent", description="Test agent")
        router.register_agent(config)
        router.unregister_agent("test_agent")
        
        assert "test_agent" not in router.list_agents()
    
    def test_add_rule(self):
        """Test adding routing rules."""
        router = AgentRouter()
        
        router.register_agent(AgentConfig(name="agent1", description="Agent 1"))
        router.add_rule(RoutingRule(
            name="rule1",
            keywords=["weather"],
            agent_name="agent1",
        ))
        
        agent = router.route("What's the weather?", "user1")
        assert agent == "agent1"
    
    def test_rule_priority(self):
        """Test rule priority ordering."""
        router = AgentRouter()
        
        router.register_agent(AgentConfig(name="agent1", description="Agent 1"))
        router.register_agent(AgentConfig(name="agent2", description="Agent 2"))
        
        router.add_rule(RoutingRule(
            name="low_priority",
            keywords=["test"],
            agent_name="agent1",
            priority=1,
        ))
        router.add_rule(RoutingRule(
            name="high_priority",
            keywords=["test"],
            agent_name="agent2",
            priority=10,
        ))
        
        agent = router.route("test message", "user1")
        assert agent == "agent2"
    
    def test_user_binding(self):
        """Test user binding."""
        router = AgentRouter()
        
        router.register_agent(AgentConfig(name="agent1", description="Agent 1"))
        router.register_agent(AgentConfig(name="agent2", description="Agent 2"))
        
        router.bind_user("user1", "agent2")
        
        agent = router.route("any message", "user1")
        assert agent == "agent2"
    
    def test_default_agent(self):
        """Test default agent fallback."""
        router = AgentRouter()
        
        router.register_agent(AgentConfig(name="default", description="Default agent"))
        router.register_agent(AgentConfig(name="special", description="Special agent"))
        
        router.set_default_agent("default")
        
        agent = router.route("unmatched message", "user1")
        assert agent == "default"
    
    def test_disabled_agent(self):
        """Test disabled agent handling."""
        router = AgentRouter()
        
        router.register_agent(AgentConfig(
            name="agent1",
            description="Agent 1",
            enabled=False,
        ))
        router.register_agent(AgentConfig(name="agent2", description="Agent 2"))
        
        router.add_rule(RoutingRule(
            name="rule1",
            keywords=["test"],
            agent_name="agent1",
        ))
        
        agent = router.route("test message", "user1")
        assert agent == "agent2"
    
    def test_get_agent_skills(self):
        """Test getting agent skills."""
        router = AgentRouter()
        
        router.register_agent(AgentConfig(
            name="agent1",
            description="Agent 1",
            skills=["skill1", "skill2"],
        ))
        
        skills = router.get_agent_skills("agent1")
        assert skills == ["skill1", "skill2"]
    
    def test_can_use_skill(self):
        """Test skill permission check."""
        router = AgentRouter()
        
        router.register_agent(AgentConfig(
            name="agent1",
            description="Agent 1",
            skills=["skill1", "skill2"],
        ))
        
        assert router.can_use_skill("agent1", "skill1") is True
        assert router.can_use_skill("agent1", "skill3") is False
        
        router.register_agent(AgentConfig(
            name="agent2",
            description="Agent 2",
            skills=[],
        ))
        
        assert router.can_use_skill("agent2", "any_skill") is True

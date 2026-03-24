"""Web channel for LocalClaw using FastAPI."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from localclaw.config.settings import get_settings
from localclaw.core.engine import ExecutionEngine, get_engine
from localclaw.core.models import Message, Task, TaskState, ExecutionResult
from localclaw.security.audit import configure_audit_logger
from localclaw.skills.loader import load_skills_from_dir, register_builtin_skills
from localclaw.tools.base import Tool, get_tool_registry
from localclaw.tools.file_tool import register_file_tools
from localclaw.tools.http_tool import register_http_tools
from localclaw.tools.shell_tool import register_shell_tools
from localclaw.tools.clawhub_tool import register_clawhub_tools


logger = logging.getLogger(__name__)


class MessageRequest(BaseModel):
    """Request model for sending a message."""
    content: str
    user_id: str = "default"
    channel: str = "web"


class MessageResponse(BaseModel):
    """Response model for message processing."""
    task_id: str
    status: str
    message: str
    data: Dict[str, Any] = {}
    error: Optional[str] = None


class TaskResponse(BaseModel):
    """Response model for task status."""
    id: str
    state: str
    created_at: datetime
    completed_at: Optional[datetime]
    result: Dict[str, Any] = {}
    error: Optional[str] = None


class SkillResponse(BaseModel):
    """Response model for skill info."""
    name: str
    version: str
    description: str
    type: str
    state: str


class ConnectionManager:
    """Manages WebSocket connections."""
    
    def __init__(self) -> None:
        self._connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new connection."""
        await websocket.accept()
        self._connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a connection."""
        if websocket in self._connections:
            self._connections.remove(websocket)
    
    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcast a message to all connections."""
        for connection in self._connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


_manager = ConnectionManager()


class SystemStatusTool(Tool):
    """Tool for getting system status."""
    
    name = "system_status"
    description = "Get system status information"
    inputs = {}
    outputs = {"status": "string", "version": "string"}
    
    async def execute(self, **kwargs) -> ExecutionResult:
        from localclaw import __version__
        return ExecutionResult.success(
            message="System status",
            data={
                "status": "running",
                "version": __version__,
                "mode": get_settings().mode.value,
            },
        )


class ListSkillsTool(Tool):
    """Tool for listing skills."""
    
    name = "list_skills"
    description = "List all available skills"
    inputs = {}
    outputs = {"skills": "list"}
    
    async def execute(self, **kwargs) -> ExecutionResult:
        from localclaw.skills.registry import get_skill_registry
        skills = get_skill_registry().list_skills()
        
        # Generate a more natural response
        response = "我是一个智能助手，可以帮助你做以下事情：\n\n"
        response += "1. 提供信息：\n"
        response += "   - 查询日期和时间（今天几号？明天星期几？）\n"
        response += "   - 查询天气信息（今天天气如何？）\n"
        response += "2. 系统功能：\n"
        response += "   - 查看系统状态\n"
        response += "   - 列出所有可用技能\n"
        response += "3. 其他功能：\n"
        response += "   - 执行各种工具操作\n"
        response += "   - 处理用户的各种查询\n"
        
        return ExecutionResult.success(
            message="系统功能介绍",
            data={"skills": skills, "result": response},
        )


def initialize_system() -> ExecutionEngine:
    """Initialize the LocalClaw system."""
    settings = get_settings()
    settings.ensure_directories()
    
    configure_audit_logger(settings.audit_log)
    
    # Initialize LLM provider if enabled
    if settings.llm_enabled:
        logger.info("LLM enabled, initializing Ollama provider")
        try:
            from localclaw.llm.ollama import initialize_ollama, OllamaConfig
            ollama_config = OllamaConfig(
                base_url=settings.ollama_base_url or "http://localhost:11434",
                model="gemma3:4b",
            )
            logger.info(f"Initializing Ollama with model: {ollama_config.model}")
            initialize_ollama(ollama_config)
            logger.info("Ollama LLM provider initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Ollama LLM provider: {e}")
    else:
        logger.info("LLM is disabled")
    
    tool_registry = get_tool_registry()
    tool_registry.register(SystemStatusTool())
    tool_registry.register(ListSkillsTool())
    register_file_tools()
    register_http_tools()
    register_shell_tools()
    register_clawhub_tools()
    
    register_builtin_skills()
    
    if settings.skills_dir.exists():
        load_skills_from_dir(settings.skills_dir)
    
    # Create engine with LLM-enabled parser
    from localclaw.core.parser import create_default_parser
    parser = create_default_parser(
        llm_enabled=settings.llm_enabled,
    )
    
    from localclaw.core.verifier import create_default_verifier
    verifier = create_default_verifier()
    verifier.set_auto_approve_low(True)
    verifier.set_require_confirmation_high(False)

    engine = ExecutionEngine(
        settings=settings,
        parser=parser,
        verifier=verifier,
    )
    
    def on_step_start(step, task):
        asyncio.create_task(_manager.broadcast({
            "type": "step_start",
            "task_id": task.id,
            "step_id": step.id,
            "step_name": step.name,
        }))
    
    def on_step_complete(step, task):
        asyncio.create_task(_manager.broadcast({
            "type": "step_complete",
            "task_id": task.id,
            "step_id": step.id,
            "status": step.status.value,
        }))
    
    def on_task_complete(task):
        asyncio.create_task(_manager.broadcast({
            "type": "task_complete",
            "task_id": task.id,
            "state": task.state.value,
        }))
    
    engine.set_callbacks(
        on_step_start=on_step_start,
        on_step_complete=on_step_complete,
        on_task_complete=on_task_complete,
    )
    
    # Set the global engine instance
    from localclaw.core.engine import _engine
    import localclaw.core.engine as engine_module
    engine_module._engine = engine
    
    return engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Check if system is already initialized
    from localclaw.core.engine import _engine
    if _engine is None:
        initialize_system()
    logger.info("LocalClaw web server started")
    yield
    logger.info("LocalClaw web server stopped")


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="LocalClaw",
        description="Local-first Agent Runtime System",
        version="0.1.0",
        lifespan=lifespan,
    )
    
    # Mount static files directory
    import os
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    
    @app.get("/", response_class=HTMLResponse)
    async def root():
        """Serve the web UI."""
        import os
        static_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "static", "index.html")
        if os.path.exists(static_file):
            with open(static_file, 'r', encoding='utf-8') as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content=get_web_ui_html())
    
    @app.post("/api/message", response_model=MessageResponse)
    async def send_message(request: MessageRequest):
        """Process a message and return the result."""
        engine = get_engine()
        
        message = Message(
            content=request.content,
            user_id=request.user_id,
            channel=request.channel,
        )
        
        task = await engine.process_message(message)
        
        return MessageResponse(
            task_id=task.id,
            status=task.state.value,
            message=task.result.message if task.result else "",
            data=task.result.data if task.result else {},
            error=task.error,
        )
    
    @app.get("/api/tasks", response_model=List[TaskResponse])
    async def list_tasks(limit: int = 10):
        """List recent tasks."""
        engine = get_engine()
        tasks = engine.get_task_history(limit)
        
        return [
            TaskResponse(
                id=t.id,
                state=t.state.value,
                created_at=t.created_at,
                completed_at=t.completed_at,
                result=t.result.data if t.result else {},
                error=t.error,
            )
            for t in tasks
        ]
    
    @app.get("/api/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str):
        """Get a specific task."""
        engine = get_engine()
        task = engine.get_task(task_id)
        
        if not task:
            tasks = engine.get_task_history(100)
            task = next((t for t in tasks if t.id == task_id), None)
        
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        
        return TaskResponse(
            id=task.id,
            state=task.state.value,
            created_at=task.created_at,
            completed_at=task.completed_at,
            result=task.result.data if task.result else {},
            error=task.error,
        )
    
    @app.get("/api/skills", response_model=List[SkillResponse])
    async def list_skills():
        """List all registered skills."""
        from localclaw.skills.registry import get_skill_registry
        
        registry = get_skill_registry()
        skills = registry.get_all_info()
        
        return [
            SkillResponse(
                name=s["name"],
                version=s["version"],
                description=s["description"],
                type=s["type"],
                state=s["state"],
            )
            for s in skills
        ]
    
    @app.get("/api/tools")
    async def list_tools():
        """List all registered tools."""
        from localclaw.tools.base import get_tool_registry
        
        registry = get_tool_registry()
        return registry.get_all_info()
    
    @app.get("/api/clawhub/search")
    async def clawhub_search(query: str = ""):
        registry = get_tool_registry()
        result = await registry.execute("clawhub_search", query=query)
        if result.status == "success":
            return {"skills": result.data.get("skills", [])}
        return {"skills": [], "error": result.message}

    @app.post("/api/clawhub/install")
    async def clawhub_install(skill_id: str):
        registry = get_tool_registry()
        result = await registry.execute("clawhub_install", skill_id=skill_id)
        if result.status == "success":
            return {"installed": True, "skill_path": result.data.get("skill_path", "")}
        return {"installed": False, "error": result.message}

    @app.post("/api/clawhub/remove")
    async def clawhub_remove(skill_id: str):
        registry = get_tool_registry()
        result = await registry.execute("clawhub_remove", skill_id=skill_id)
        if result.status == "success":
            return {"removed": True}
        return {"removed": False, "error": result.message}
    
    @app.get("/api/config")
    async def get_config():
        """Get current configuration."""
        settings = get_settings()
        return {
            "mode": settings.mode.value,
            "llm_enabled": settings.llm_enabled,
            "skills_dir": str(settings.skills_dir),
            "data_dir": str(settings.data_dir),
        }
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}
    
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time updates."""
        await _manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_json()
                
                if data.get("type") == "message":
                    engine = get_engine()
                    message = Message(
                        content=data.get("content", ""),
                        user_id=data.get("user_id", "ws"),
                        channel="websocket",
                    )
                    
                    task = await engine.process_message(message)
                    
                    await websocket.send_json({
                        "type": "result",
                        "task_id": task.id,
                        "status": task.state.value,
                        "message": task.result.message if task.result else "",
                        "data": task.result.data if task.result else {},
                    })
        except WebSocketDisconnect:
            _manager.disconnect(websocket)
    
    return app


def get_web_ui_html() -> str:
    """Get the web UI HTML."""
    html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LocalClaw</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23007bff' d='M8 0C3.58 0 0 3.58 0 8s3.58 8 8 8 8-3.58 8-8-3.58-8-8-8zm0 14.5c-3.59 0-6.5-2.91-6.5-6.5S4.41 1.5 8 1.5 14.5 4.41 14.5 8 11.59 14.5 8 14.5zm-1.5-8c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5-.67-1.5-1.5-1.5-1.5.67-1.5 1.5zm0 5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5-.67-1.5-1.5-1.5-1.5.67-1.5 1.5z'/%3E%3C/svg%3E" type="image/svg+xml">
    <script src="https://unpkg.com/axios/dist/axios.min.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .header { text-align: center; margin-bottom: 20px; }
        .header h1 { color: #333; }
        .tabs { display: flex; margin-bottom: 20px; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .tab { flex: 1; padding: 15px; text-align: center; cursor: pointer; border-bottom: 3px solid transparent; }
        .tab.active { border-bottom-color: #007bff; color: #007bff; font-weight: bold; }
        .tab-content { display: none; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 20px; }
        .tab-content.active { display: block; }
        .chat-container { background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .messages { height: 400px; overflow-y: auto; padding: 20px; border-bottom: 1px solid #eee; }
        .message { margin-bottom: 15px; padding: 10px 15px; border-radius: 8px; }
        .message.user { background: #007bff; color: white; margin-left: 20%; }
        .message.system { background: #f0f0f0; margin-right: 20%; }
        .message.error { background: #f8d7da; color: #721c24; }
        .input-area { padding: 15px; display: flex; gap: 10px; }
        .input-area input { flex: 1; padding: 10px 15px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        .input-area button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
        .input-area button:hover { background: #0056b3; }
        .sidebar { margin-top: 20px; background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .sidebar h3 { margin-bottom: 10px; color: #333; }
        .skill-list { list-style: none; }
        .skill-list li { padding: 5px 0; border-bottom: 1px solid #eee; }
        .status { font-size: 12px; color: #666; margin-top: 10px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .task { border: 1px solid #eee; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
        .task h4 { margin-bottom: 10px; color: #333; }
        .task p { margin-bottom: 5px; font-size: 14px; }
        .task-status { display: inline-block; padding: 3px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; }
        .task-status.completed { background-color: #d4edda; color: #155724; }
        .task-status.failed { background-color: #f8d7da; color: #721c24; }
        .task-status.running { background-color: #d1ecf1; color: #0c5460; }
        .skill-card { border: 1px solid #eee; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
        .skill-card h4 { margin-bottom: 10px; color: #333; }
        .skill-card p { margin-bottom: 5px; font-size: 14px; }
        .skill-actions { margin-top: 10px; display: flex; gap: 10px; }
        .skill-actions button { padding: 5px 10px; font-size: 12px; border: none; border-radius: 4px; cursor: pointer; }
        .skill-actions button.install { background: #28a745; color: white; }
        .skill-actions button.uninstall { background: #dc3545; color: white; }
        .skill-actions button:hover { opacity: 0.8; }
    </style>
</head>
<body>
    <div class="container" x-data="app()" x-init="init()">
        <div class="header">
            <h1>🦀 LocalClaw</h1>
            <p class="status">Mode: <span x-text="config.mode || 'loading...'"></span></p>
        </div>
        
        <div class="tabs">
            <div class="tab" :class="{active: activeTab === 'chat'}" @click="activeTab = 'chat'">Chat</div>
            <div class="tab" :class="{active: activeTab === 'tasks'}" @click="activeTab = 'tasks'">Tasks</div>
            <div class="tab" :class="{active: activeTab === 'skills'}" @click="activeTab = 'skills'">Skills</div>
        </div>
        
        <div class="tab-content" x-show="activeTab === 'chat'">
            <div class="chat-container">
                <div class="messages" x-ref="messages">
                    <template x-for="msg in messages" :key="msg.id">
                        <div class="message" :class="msg.type" x-text="msg.content"></div>
                    </template>
                </div>
                
                <div class="input-area">
                    <input type="text" x-model="input" @keyup.enter="sendMessage()" placeholder="Type a message or /skill_name params...">
                    <button @click="sendMessage()" :disabled="loading">Send</button>
                </div>
            </div>
        </div>
        
        <div class="tab-content" x-show="activeTab === 'tasks'">
            <h2>Task History</h2>
            <div class="task-list">
                <template x-for="task in tasks" :key="task.id">
                    <div class="task">
                        <h4>Task {{ task.id }}</h4>
                        <p><strong>Status:</strong> <span class="task-status" :class="task.state.toLowerCase()">{{ task.state }}</span></p>
                        <p><strong>Created:</strong> <span x-text="new Date(task.created_at).toLocaleString()"></span></p>
                        <p><strong>Completed:</strong> <span x-text="task.completed_at ? new Date(task.completed_at).toLocaleString() : 'N/A'"></span></p>
                        <p><strong>Result:</strong> <span x-text="JSON.stringify(task.result)"></span></p>
                        <p><strong>Error:</strong> <span x-text="task.error || 'N/A'"></span></p>
                    </div>
                </template>
            </div>
        </div>
        
        <div class="tab-content" x-show="activeTab === 'skills'">
            <h2>Skill Management</h2>
            <div class="grid">
                <div>
                    <h3>Installed Skills</h3>
                    <div class="skill-list">
                        <template x-for="skill in skills" :key="skill.name">
                            <div class="skill-card">
                                <h4>{{ skill.name }} v{{ skill.version }}</h4>
                                <p><strong>Description:</strong> {{ skill.description }}</p>
                                <p><strong>Type:</strong> {{ skill.type }}</p>
                                <p><strong>State:</strong> {{ skill.state }}</p>
                                <div class="skill-actions">
                                    <button class="uninstall" @click="uninstallSkill(skill.name)">Uninstall</button>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
                <div>
                    <h3>ClawHub Skills</h3>
                    <div class="search-box" style="margin-bottom: 15px;">
                        <input type="text" x-model="searchQuery" placeholder="Search skills..." style="width: 70%; padding: 8px;">
                        <button @click="searchClawHub(searchQuery)" style="padding: 8px 15px; margin-left: 5px;">Search</button>
                    </div>
                    <div class="skill-list">
                        <template x-for="skill in availableSkills" :key="skill.id">
                            <div class="skill-card">
                                <h4>{{ skill.name }} v{{ skill.version }}</h4>
                                <p><strong>Description:</strong> {{ skill.description }}</p>
                                <p><strong>Author:</strong> {{ skill.author || 'Unknown' }}</p>
                                <div class="skill-actions">
                                    <template x-if="isSkillInstalled(skill.id)">
                                        <button class="uninstall" @click="uninstallSkill(skill.id)">Uninstall</button>
                                    </template>
                                    <template x-if="!isSkillInstalled(skill.id)">
                                        <button class="install" @click="installSkill(skill.id)" :disabled="installing === skill.id">
                                            <span x-text="installing === skill.id ? 'Installing...' : 'Install'"></span>
                                        </button>
                                    </template>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
        const _FALLBACK_SKILLS = [
            { id: 'weather', name: 'Weather', version: '2.0.0', description: 'Get weather information from wttr.in', author: 'LocalClaw Team' },
            { id: 'web_search', name: 'Web Search', version: '1.0.0', description: 'Search the web using DuckDuckGo', author: 'LocalClaw Team' },
            { id: 'fs', name: 'File System', version: '2.0.0', description: 'File system operations', author: 'LocalClaw Team' }
        ];

        window.app = function() {
            return {
                activeTab: 'chat',
                messages: [],
                input: '',
                loading: false,
                skills: [],
                availableSkills: [],
                tasks: [],
                config: {},
                searchQuery: '',
                installing: null,
                
                async init() {
                    await this.loadConfig();
                    await Promise.all([this.loadSkills(), this.loadTasks(), this.searchClawHub('')]);

                    setInterval(() => {
                        Promise.all([this.loadSkills(), this.loadTasks()]);
                    }, 5000);
                },
                
                async loadConfig() {
                    try {
                        const response = await axios.get('/api/config');
                        this.config = response.data;
                    } catch (e) {
                        console.error('Failed to load config:', e);
                    }
                },
                
                async loadSkills() {
                    try {
                        const response = await axios.get('/api/skills');
                        this.skills = response.data;
                    } catch (e) {
                        console.error('Failed to load skills:', e);
                    }
                },
                
                async loadTasks() {
                    try {
                        const response = await axios.get('/api/tasks');
                        this.tasks = response.data;
                    } catch (e) {
                        console.error('Failed to load tasks:', e);
                    }
                },
                
                async searchClawHub(query) {
                    try {
                        const response = await axios.get('/api/clawhub/search', { params: { query: query || '' } });
                        this.availableSkills = response.data.skills || [];
                        if (this.availableSkills.length === 0) {
                            this.availableSkills = _FALLBACK_SKILLS;
                        }
                    } catch (e) {
                        console.error('Failed to search ClawHub:', e);
                        this.availableSkills = _FALLBACK_SKILLS;
                    }
                },
                
                isSkillInstalled(skillId) {
                    return this.skills.some(s => s.name.toLowerCase() === skillId.toLowerCase());
                },
                
                async sendMessage() {
                    if (!this.input.trim() || this.loading) return;
                    
                    const content = this.input.trim();
                    this.input = '';
                    
                    this.messages.push({
                        id: Date.now(),
                        type: 'user',
                        content: content
                    });
                    
                    this.loading = true;
                    
                    try {
                        const response = await axios.post('/api/message', {
                            content: content,
                            user_id: 'web',
                            channel: 'web'
                        });
                        
                        const data = response.data;
                        
                        let responseText = data.message || 'Done';
                        if (data.data) {
                            const stepIds = Object.keys(data.data);
                            if (stepIds.length > 0) {
                                const firstStepId = stepIds[0];
                                const stepResult = data.data[firstStepId];
                                let weatherData = stepResult.body || stepResult.result;
                                if (weatherData && typeof weatherData === 'object' && weatherData.current_condition) {
                                    const weather = weatherData;
                                    const current = weather.current_condition[0];
                                    const location = weather.nearest_area[0];
                                    responseText = '🌍 位置: ' + (location.areaName[0].value || '未知') + ', ' + (location.country[0].value || '') + '\\n';
                                    responseText += '🌡️ 温度: ' + current.temp_C + '°C (体感 ' + current.FeelsLikeC + '°C)\\n';
                                    responseText += '☁️ 天气: ' + current.weatherDesc[0].value + '\\n';
                                    responseText += '💨 风速: ' + current.windspeedKmph + ' km/h\\n';
                                    responseText += '💧 湿度: ' + current.humidity + '%';
                                    if (weather.weather && weather.weather.length > 1) {
                                        const tomorrow = weather.weather[1];
                                        responseText += '\\n\\n📅 明天预报:\\n';
                                        responseText += '🌡️ 温度: ' + tomorrow.mintempC + '°C ~ ' + tomorrow.maxtempC + '°C\\n';
                                        responseText += '☁️ 天气: ' + tomorrow.hourly[4].weatherDesc[0].value;
                                    }
                                } else if (stepResult && stepResult.result) {
                                    responseText = stepResult.result;
                                } else if (stepResult && stepResult.message) {
                                    responseText = stepResult.message;
                                } else if (stepResult && stepResult.files && stepResult.directories) {
                                    let fileList = '📁 目录: ' + stepResult.path + '\\n\\n';
                                    if (stepResult.directories && stepResult.directories.length > 0) {
                                        fileList += '📂 目录:\\n';
                                        stepResult.directories.forEach(dir => {
                                            fileList += '  📁 ' + dir + '\\n';
                                        });
                                        fileList += '\\n';
                                    }
                                    if (stepResult.files && stepResult.files.length > 0) {
                                        fileList += '📄 文件:\\n';
                                        stepResult.files.forEach(file => {
                                            fileList += '  📄 ' + file + '\\n';
                                        });
                                    }
                                    responseText = fileList;
                                }
                            } else if (data.data.result) {
                                responseText = data.data.result;
                            } else if (data.data.message) {
                                responseText = data.data.message;
                            }
                        }
                        
                        this.messages.push({
                            id: Date.now() + 1,
                            type: data.status === 'failed' ? 'error' : 'system',
                            content: responseText
                        });
                    } catch (e) {
                        this.messages.push({
                            id: Date.now() + 1,
                            type: 'error',
                            content: 'Error: ' + (e.response?.data?.detail || e.message)
                        });
                    } finally {
                        this.loading = false;
                        setTimeout(() => {
                            const messagesElement = document.querySelector('.messages');
                            if (messagesElement) {
                                messagesElement.scrollTop = messagesElement.scrollHeight;
                            }
                        }, 100);
                    }
                },
                
                async installSkill(skillId) {
                    if (this.installing === skillId) return;
                    this.installing = skillId;
                    try {
                        const response = await axios.post('/api/clawhub/install?skill_id=' + encodeURIComponent(skillId));
                        if (response.data.installed) {
                            alert('Skill ' + skillId + ' installed successfully!');
                            await this.loadSkills();
                        } else {
                            alert('Failed to install skill: ' + (response.data.error || 'Unknown error'));
                        }
                    } catch (e) {
                        console.error('Failed to install skill:', e);
                        alert('Failed to install skill: ' + (e.response?.data?.error || e.message || 'Unknown error'));
                    } finally {
                        this.installing = null;
                    }
                },
                
                async uninstallSkill(skillName) {
                    try {
                        const response = await axios.post('/api/clawhub/remove?skill_id=' + encodeURIComponent(skillName));
                        if (response.data.removed) {
                            alert('Skill ' + skillName + ' uninstalled successfully!');
                            await this.loadSkills();
                        } else {
                            alert('Failed to uninstall skill: ' + (response.data.error || 'Unknown error'));
                        }
                    } catch (e) {
                        console.error('Failed to uninstall skill:', e);
                        alert('Failed to uninstall skill: ' + (e.response?.data?.error || e.message || 'Unknown error'));
                    }
                }
            };
        };
    </script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
</body>
</html>
'''
    return html

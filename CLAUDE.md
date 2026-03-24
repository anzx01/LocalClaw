# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run CLI query
python main.py run "查询天气"

# Start Web server (http://127.0.0.1:8000)
python main.py serve

# Run all tests
pytest

# Run a single test file
pytest tests/test_engine.py -v

# Run tests with coverage
pytest --cov=localclaw tests/

# Format code
black localclaw/ tests/
isort localclaw/ tests/

# Lint
ruff check localclaw/

# Type-check
mypy localclaw/
```

## Environment Setup

Create a `.env` file to enable local LLM:

```env
LLM_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:4b
```

Without `.env`, the system runs in `mode=zero` (rule-based, no LLM). Settings use the `LOCALCLAW_` prefix for env vars (e.g., `LOCALCLAW_MODE=local`).

Ollama must be running before starting in local/hybrid mode: `ollama pull gemma3:4b`.

## Architecture

LocalClaw is a local-first Agent Runtime System. The core goal is to run completely on a local LLM (Ollama) — no cloud dependency required. It is designed to be compatible with [OpenClaw](https://github.com/openclaw) conventions.

### Request Pipeline

```
User Input
  → Parser (DSL → LLM → Rules → Default)
  → Intent
  → Planner (Intent → Plan with Steps)
  → Verifier (risk check per Step)
  → ExecutionEngine (state machine: INIT→PARSED→PLANNED→RUNNING→VERIFYING→COMPLETED/FAILED)
  → Tool / Skill execution
  → Response
```

### Key Modules

**`localclaw/core/`** — Pipeline core
- `models.py`: All shared data types (`Message`, `Intent`, `Plan`, `Step`, `Task`, `Context`, `ExecutionResult`, `TaskState`, `RiskLevel`)
- `parser.py`: Multi-backend parser. Chain: DSL (`/skill arg`) → LLM (Ollama) → Rule (`ParseRule` regex) → Default fallback
- `planner.py`: Maps `Intent` to `Plan` (list of `Step`s). Weather uses inline HTTP steps; other skills resolved via `_plan_from_skill()`
- `engine.py`: State-machine executor. Holds `_task_history: deque(maxlen=1000)`. Use `get_engine()` singleton
- `verifier.py`: Pre-execution risk check. Use `create_default_verifier()` — never access `engine._verifier` directly

**`localclaw/llm/`** — LLM abstraction
- `provider.py`: `LLMProvider` ABC with `generate()` / `chat()` / `is_available()`. `get_llm_provider()` / `set_llm_provider()` singleton
- `ollama.py`: Ollama implementation (HTTP to `localhost:11434`)
- Modes: `zero` (MockLLMProvider), `local` (Ollama), `hybrid` (Ollama + cloud fallback)

**`localclaw/tools/`** — Atomic capabilities
- `base.py`: `Tool` ABC (declare `name`, `description`, `risk_level`, `inputs`, `outputs` as class vars; implement `async execute(**kwargs) → ExecutionResult`). `get_tool_registry()` singleton
- Call tools via: `await get_tool_registry().execute("tool_name", **params)` — never instantiate directly
- Built-in tools: `http_get`, `http_post`, `file_read`, `file_write`, `shell_exec`, `date_info`, `clawhub_search`, `clawhub_install`, `clawhub_remove`, `clawhub_list`
- Register tool sets at startup: `register_http_tools()`, `register_file_tools()`, `register_shell_tools()`, `register_clawhub_tools()`

**`localclaw/skills/`** — Composable capabilities
- Skills are JSON/YAML definitions in `skills/` directory. A skill declares `triggers`, `actions`, `permissions`, and required tools
- `skills/registry/registry.py`: `SkillRegistry` — `get_skill_registry()` singleton
- `skills/registry/clawhub.py`: ClawHub marketplace integration (search/install remote skills)
- `skills/loader.py`: `register_from_directory(path, recursive)` loads and registers all skills in one call; `register_builtin_skills()` for built-in Python skills
- Skill lifecycle: `installed → enabled → running → stopped`

**`localclaw/channels/`** — User interfaces
- `cli.py`: Click-based CLI (`python main.py run "..."`)
- `web.py`: FastAPI app with inlined Alpine.js frontend. Registers all tools/skills at startup. Key endpoints: `POST /api/message`, `GET /api/tasks`, `GET /api/skills`, `WebSocket /ws`, `GET /api/clawhub/search`, `POST /api/clawhub/install`

**`localclaw/security/`**
- `hitl.py`: Human-In-The-Loop — high-risk steps pause and await user confirmation via callback
- `sandbox.py`: Subprocess isolation for skill execution
- `permissions.py`: Risk-level checking
- `audit.py`: JSON Lines audit log at `./data/audit.jsonl`

**`localclaw/memory/`** — Session and persistent memory
- `short_term.py`: In-memory dict scoped to a Task
- `long_term.py`: SQLite (`aiosqlite`) key-value with vector retrieval hook
- `cache.py`: Deduplication cache for Tool results

**`localclaw/events/`** — Proactive triggers
- `scheduler.py`: APScheduler integration for cron/interval tasks
- `triggers.py`: Condition-based event trigger evaluation

**`localclaw/gateway/`** — Multi-channel routing
- `gateway.py`: Unified message receive/dispatch, session management, asyncio Queue
- `router.py`: User-to-agent binding, keyword routing, fallback agent

**`localclaw/agents/`** — Multi-agent orchestration
- `manager.py`: Agent lifecycle management
- `config.py`: Per-agent skill/permissions/strategy config; agent definitions stored in `agents/*.json`

### Unified Output Format

All tools, skills, and the engine return:
```python
ExecutionResult(status="success|error", message="...", data={})
```

Use `ExecutionResult.success(message, data)` and `ExecutionResult.error(message, error_type)` factory methods.

### Skill Definition Format

```json
{
  "name": "my_skill",
  "version": "1.0.0",
  "type": "atomic",
  "inputs": { "param": "string" },
  "outputs": { "result": "string" },
  "actions": [{ "type": "transform", "template": "Hello {{param}}" }],
  "permissions": { "risk_level": "low" },
  "triggers": [{ "type": "intent", "pattern": "my_intent" }]
}
```

Note: Skill JSON templates use `{{param}}` syntax (Jinja2). The `${param}` syntax in older skill files is **not** supported by `_resolve_params`.

### Adding a New Tool

1. Subclass `Tool` in `localclaw/tools/your_tool.py`
2. Declare `name`, `description`, `risk_level`, `inputs`, `outputs` as class variables
3. Implement `async execute(self, **kwargs) -> ExecutionResult`
4. Write a `register_your_tools()` function that calls `get_tool_registry().register(YourTool())`
5. Call `register_your_tools()` in `channels/cli.py` and `channels/web.py` startup

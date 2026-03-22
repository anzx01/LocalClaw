# LocalClaw 开发计划

## Context

LocalClaw 是一个兼容 OpenClaw 的**本地优先 Agent Runtime 系统**，核心目标是构建一个无需依赖 LLM 也能完整运行的 Agent 操作系统。

- **问题**：现有 Agent 系统强依赖 LLM，成本高、不可预测、无法离线运行
- **目标**：Zero Token 默认模式 + 规则/模板驱动 + Tool 唯一执行入口 + 全行为可审计
- **技术栈**：Python（生态丰富，LLM 库支持最佳，开发效率高）
- **首选渠道**：Web UI（可视化验证，演示友好）
- **交付节奏**：3 阶段渐进式

---

## 项目结构

```
G:\myaist\LocalClaw\
├── localclaw/
│   ├── __init__.py
│   ├── core/
│   │   ├── models.py          # 核心数据模型 (Message, Intent, Step, Context, Task)
│   │   ├── parser.py          # Parser 模块（规则/DSL/可选LLM）
│   │   ├── planner.py         # Planner 模块（模板规划）
│   │   ├── engine.py          # Execution Engine（状态机核心）
│   │   └── verifier.py        # Verifier 校验模块
│   ├── channels/
│   │   ├── base.py            # Channel 抽象基类
│   │   ├── cli.py             # CLI 渠道
│   │   └── web.py             # Web UI 渠道（FastAPI）
│   ├── gateway/
│   │   ├── gateway.py         # Communication Gateway（消息收发/会话/队列）
│   │   └── router.py          # Agent Router（用户绑定/关键词路由/fallback）
│   ├── skills/
│   │   ├── base.py            # Skill 抽象基类
│   │   ├── loader.py          # Skill 加载器（含 OpenClaw 兼容导入）
│   │   └── registry.py        # Skill 注册表
│   ├── tools/
│   │   ├── base.py            # Tool 抽象基类（输入/输出声明 + 日志 + 权限）
│   │   ├── file_tool.py       # 文件操作 Tool
│   │   ├── shell_tool.py      # Shell 命令 Tool
│   │   └── http_tool.py       # HTTP 请求 Tool
│   ├── memory/
│   │   ├── short_term.py      # 短期记忆（会话内）
│   │   ├── long_term.py       # 长期记忆（持久化，SQLite）
│   │   └── cache.py           # 缓存记忆
│   ├── security/
│   │   ├── permissions.py     # 权限控制 + 风险拦截（HITL）
│   │   └── audit.py           # 审计日志
│   └── config/
│       └── settings.py        # 全局配置（mode: zero/local/hybrid）
├── skills/                    # 内置/用户 Skill 目录（JSON/YAML 定义）
├── tests/
│   ├── test_parser.py
│   ├── test_engine.py
│   ├── test_tools.py
│   └── test_skills.py
├── main.py                    # 入口文件
├── requirements.txt
└── pyproject.toml
```

---

## 核心数据模型（localclaw/core/models.py）

```python
# 统一消息格式
Message: { user_id, channel, message, timestamp }

# Parser 输出
Intent: { intent: str, params: dict }

# Planner 输出
Plan: { steps: List[Step] }

# 执行步骤
Step: { id, type, status, input, output }
# type: tool_call | skill_call | agent_call | condition | loop | transform
# status: pending | running | completed | failed

# 执行上下文
Context: { inputs, memory, step_outputs }

# 任务状态机
TaskState: INIT → PARSED → PLANNED → RUNNING → VERIFYING → COMPLETED / FAILED
```

---

## Phase 1：核心引擎 MVP（约 2 周）

**目标**：无 LLM 可完整执行任务链，CLI 可交互验证

### 1.1 基础设施
- [ ] `pyproject.toml` + `requirements.txt` 初始化（fastapi, uvicorn, pydantic, sqlite3, click）
- [ ] `localclaw/config/settings.py`：全局配置加载，支持 `mode: zero/local/hybrid`，`llm_enabled: false`

### 1.2 核心模型
- [ ] `localclaw/core/models.py`：定义 Message、Intent、Step、Context、TaskState 数据类（Pydantic BaseModel）

### 1.3 Parser 模块
- [ ] `localclaw/core/parser.py`
  - 规则解析（正则关键词匹配 → Intent）
  - DSL 解析（`/skill_name param1 param2` 格式）
  - 可选：本地 LLM 接口（仅 mode=local/hybrid 启用）

### 1.4 Planner 模块
- [ ] `localclaw/core/planner.py`
  - 模板规划：Intent → Plan（从 Skill 的 `actions` 字段生成 steps 列表）
  - Workflow Skill 直接映射为多步 Plan

### 1.5 Execution Engine（核心）
- [ ] `localclaw/core/engine.py`
  - 状态机实现：INIT → PARSED → PLANNED → RUNNING → VERIFYING → COMPLETED/FAILED
  - FOR each step：权限检查 → 执行 → 获取结果 → 更新 Context → 决策下一步
  - 支持 condition / loop step 类型
  - 错误处理：retry → fallback → abort
  - 中断/恢复：状态持久化（JSON 文件或 SQLite）
  - 执行日志：每步记录 `{ step, status, input, output }`

### 1.6 Verifier 模块
- [ ] `localclaw/core/verifier.py`
  - 风险检查（对比权限声明）
  - 数据校验（输出格式匹配 Skill 的 outputs schema）
  - 执行决策：pass / reject / ask_human

### 1.7 Tool 系统
- [ ] `localclaw/tools/base.py`：Tool 基类，必须声明 `inputs`/`outputs`，自动记录日志，权限控制
- [ ] `localclaw/tools/file_tool.py`：读写文件
- [ ] `localclaw/tools/shell_tool.py`：Shell 命令执行（高风险，需 HITL 确认）
- [ ] `localclaw/tools/http_tool.py`：HTTP GET/POST

### 1.8 Skill 系统
- [ ] `localclaw/skills/base.py`：Skill 基类，标准结构：`{ name, version, description, type, inputs, outputs, actions, tools, permissions, triggers, metadata }`
- [ ] `localclaw/skills/registry.py`：Skill 注册表（内存 + 磁盘扫描）
- [ ] `localclaw/skills/loader.py`：从 `skills/` 目录加载 JSON/YAML Skill 定义
- [ ] Skill 生命周期：installed → enabled → running → stopped
- [ ] Skill 执行规则：不直接执行系统操作，必须通过 Tool，必须可审计

### 1.9 CLI 渠道
- [ ] `localclaw/channels/cli.py`：使用 `click` 实现交互式命令行
- [ ] `main.py`：CLI 入口，支持 `python main.py run "查询天气"`

### 1.10 基础安全层
- [ ] `localclaw/security/permissions.py`：权限级别定义 + 执行前检查
- [ ] `localclaw/security/audit.py`：审计日志写入（JSON Lines 格式）

---

## Phase 2：Web UI 渠道 + Memory + Gateway（约 2 周）

**目标**：浏览器可访问，支持多轮会话，Gateway 统一消息分发

### 2.1 Web UI 渠道
- [ ] `localclaw/channels/web.py`：FastAPI 应用
  - `POST /api/message`：接收用户消息，返回执行结果
  - `GET /api/tasks`：查询任务列表与状态
  - `GET /api/skills`：列出已注册 Skill
  - WebSocket `/ws`：实时推送执行过程
- [ ] 前端：简洁聊天界面（HTML + Alpine.js，内嵌于 FastAPI static）

### 2.2 Memory 系统
- [ ] `localclaw/memory/short_term.py`：会话内内存（dict，Task 生命周期内有效）
- [ ] `localclaw/memory/long_term.py`：SQLite 持久化，支持 key-value + 向量检索预留接口
- [ ] `localclaw/memory/cache.py`：结果缓存（减少重复 Tool 调用）

### 2.3 Communication Gateway
- [ ] `localclaw/gateway/gateway.py`：消息接收分发、会话管理、异步队列（asyncio Queue）
- [ ] `localclaw/gateway/router.py`：用户绑定 Agent、关键词路由规则、默认 fallback Agent

### 2.4 Telegram 渠道（选做）
- [ ] `localclaw/channels/telegram.py`：使用 `python-telegram-bot` 库接入

---

## Phase 3：安全加固 + 多 Agent + 生态（约 2 周）

**���标**：生产级安全，多 Agent 协同，OpenClaw 兼容，可选 LLM

### 3.1 多 Agent 支持
- [ ] Agent 配置文件（`agents/` 目录，每个 Agent 独立 skills/权限/策略）
- [ ] agent_call step 类型实现：主 Agent → 子 Agent → 返回结果

### 3.2 事件系统
- [ ] 定时任务（APScheduler 集成）
- [ ] 条件触发（基于 Memory/外部事件）
- [ ] 自动执行链

### 3.3 OpenClaw 兼容层
- [ ] `localclaw/skills/loader.py` 扩展：识别并导入 OpenClaw Skill 格式
- [ ] 自动字段映射规则
- [ ] 不兼容字段 fallback 处理

### 3.4 安全加固
- [ ] Skill 沙箱执行（subprocess isolation）
- [ ] HITL（Human-In-The-Loop）：高风险操作暂停等待用户确认
- [ ] 审计日志完善：可查询、可导出

### 3.5 Local LLM 接入
- [ ] Ollama 接口集成（`mode=local` 时启用）
- [ ] 云 LLM 接口（OpenAI compatible API，`mode=hybrid` 时启用）

### 3.6 WhatsApp / 企业微信渠道（选做）

---

## 关键配置文件

### `localclaw/config/settings.py`
```python
{
  "mode": "zero",          # zero | local | hybrid
  "llm_enabled": False,
  "skills_dir": "./skills",
  "memory_db": "./data/memory.db",
  "audit_log": "./data/audit.jsonl"
}
```

### Skill 定义示例（`skills/hello.json`）
```json
{
  "name": "hello",
  "version": "1.0.0",
  "type": "atomic",
  "inputs": { "name": "string" },
  "outputs": { "message": "string" },
  "actions": [{ "type": "transform", "template": "Hello, {{name}}!" }],
  "permissions": { "risk_level": "low" }
}
```

---

## 输出规范

所有 Tool / Skill / Engine 统一输出格式：
```json
{ "status": "success|error", "message": "", "data": {} }
```

---

## 验收标准

| 阶段 | 验收条件 |
|------|---------|
| Phase 1 | `python main.py run "hello world"` → CLI 执行完整任务链，无 LLM，日志可查 |
| Phase 2 | 浏览器访问 `http://localhost:8000`，多轮对话，任务状态实时展示 |
| Phase 3 | 多 Agent 协同执行，OpenClaw Skill 可导入，高风险操作触发 HITL 确认 |

---

## 依赖清单（requirements.txt）

```
pydantic>=2.0
fastapi>=0.110
uvicorn>=0.29
click>=8.1
python-dotenv>=1.0
aiosqlite>=0.20
apscheduler>=3.10
httpx>=0.27
pyyaml>=6.0
# Phase 2+ 可选
python-telegram-bot>=21.0
# Phase 3 可选
ollama>=0.2
openai>=1.0
```

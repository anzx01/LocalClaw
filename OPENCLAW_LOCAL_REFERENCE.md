# OpenClaw 参考方案

本文记录 LocalClaw 在架构设计阶段参考 OpenClaw 文档所形成的设计决策与边界判断。

目标约束：

- 以 OpenClaw 为架构参考
- 但 LocalClaw 只走**本地大模型**
- 优先使用 **Ollama / LM Studio / vLLM / OpenAI-compatible 本地端点**
- 不以云模型为默认，不为多供应商复杂度买单
- 保持 Python 实现、Web/CLI 优先、小而稳

## 总结

OpenClaw 最值得借鉴的不是“支持很多模型提供商”，而是这四件事：

1. 清晰的控制面与执行面分层
2. Skills 作为独立能力包，而不是把所有逻辑硬编码进 core
3. 高风险执行必须有审批、白名单和默认拒绝
4. 模型接入统一抽象为 OpenAI-compatible provider，方便替换成本地推理服务

LocalClaw 不应该照搬的部分：

1. 多消息渠道大一统网关
2. 插件生态和远程节点体系
3. 多供应商回退链
4. 巨量配置面和全平台配套能力

结论：

**LocalClaw 应该做成 “OpenClaw 的本地单机精简版”**，重点保留：

- 本地 Gateway / Engine
- 本地模型适配层
- Skills/Tools/审批体系
- Web UI + CLI

而不是去复制 OpenClaw 的全渠道平台化能力。

## 推荐借鉴点

### 1. Gateway / Runtime 分层

OpenClaw 参考（<https://docs.openclaw.ai/architecture>）：

可借鉴点：

- 一个长期运行的核心运行时负责状态、会话、工具、事件
- Web UI / CLI 都只是这个运行时的入口
- 执行权限和审批不写死在前端，而是在运行时统一判断

对 LocalClaw 的建议：

- 保留 `ExecutionEngine` 作为单一执行核心
- `channels/web.py` 和 `channels/cli.py` 只做输入输出，不直接改变安全策略
- 后续如果增加 websocket 或任务流式更新，也挂在 engine 之上

### 2. 本地模型优先的 Provider 抽象

OpenClaw 参考（<https://docs.openclaw.ai/gateway/local-models>、<https://docs.openclaw.ai/gateway/configuration-reference>）：

OpenClaw 的一个关键思路是：

- 模型统一通过 provider catalog 管理
- 本地模型也走 OpenAI-compatible 接口
- 成本字段允许为 `0`

对 LocalClaw 的建议：

- 不做多供应商平台，先只支持以下四类：
  - `ollama`
  - `lmstudio`
  - `vllm`
  - `openai_compat_local`
- 配置结构向 OpenClaw 靠拢，但精简：

```json
{
  "model": {
    "provider": "ollama",
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "qwen2.5-coder:7b",
    "api": "openai-compatible",
    "context_window": 32768,
    "cost": { "input": 0, "output": 0 }
  }
}
```

- 默认模式应从“zero”转向“local”
- 可以保留 `hybrid` 作为将来选项，但**不是默认路径**

### 3. Skills 改成目录 + `SKILL.md`，而不是只靠 JSON

OpenClaw 参考（<https://docs.openclaw.ai/tools/skills>）：

OpenClaw 的 skills 设计比当前 LocalClaw 的 JSON 技能更稳，优势在于：

- 每个 skill 是独立目录
- `SKILL.md` 同时包含元数据和使用说明
- 能做环境门控、依赖检测、可见性控制
- 天然适合被模型阅读

对 LocalClaw 的建议：

- 短期支持双格式：
  - 继续兼容 `skills/*.json`
  - 新增 `skills/<name>/SKILL.md`
- 中期以 `SKILL.md` 为主，JSON 退化为兼容层
- Skill 元数据至少支持：
  - `name`
  - `description`
  - `tools`
  - `requires.bins`
  - `requires.env`
  - `user-invocable`

建议目录形态：

```text
skills/
  weather/
    SKILL.md
    scripts/
  web_search/
    SKILL.md
```

### 4. Skill 可见性和环境门控

OpenClaw 参考（<https://docs.openclaw.ai/tools/skills>）：

当前 LocalClaw 的问题是：

- skill 只要文件存在就加载
- 不检查依赖是否可用
- 不区分“安装了”和“当前可执行”

对 LocalClaw 的建议：

- skill loader 在加载时做 eligibility check：
  - 所需命令是否存在
  - 所需环境变量是否存在
  - 所需配置是否启用
- 只把“当前可执行”的 skills 注入 prompt / 暴露给 planner
- Web UI 中区分：
  - installed
  - available
  - blocked

### 5. 执行审批与白名单必须保留

OpenClaw 参考（<https://docs.openclaw.ai/tools/exec-approvals>）：

OpenClaw 的核心经验：

- prompt 不是安全边界
- 真正的安全边界是工具策略、审批、allowlist、sandbox

对 LocalClaw 的建议：

- `shell`、`file_delete`、`http_post`、`clawhub_install/remove` 默认都不应自动放行
- Web UI 不能为了省事直接关闭高风险确认
- 审批策略建议分三级：
  - `deny`
  - `allowlist`
  - `ask`

最小实现建议：

- 低风险：自动执行
- 中风险：弹确认框
- 高风险：必须审批 + 可选白名单

### 6. Web Search 不要写死成单一 API 调用

OpenClaw 参考（OpenClaw web search runtime 设计思路）：

OpenClaw 的做法是 provider-based web search runtime，而不是把搜索写死到一个 URL。

对 LocalClaw 的建议：

- 先保留一个默认 provider，但抽象出接口：
  - `duckduckgo`
  - `searxng`
  - `tavily` 可选
- 这样以后仍然可以保持“低成本优先”，但不会把业务逻辑写死

## 不建议照搬的部分

### 1. 多渠道接入

OpenClaw 的 WhatsApp / Telegram / Discord / iMessage / 节点体系非常强，但对 LocalClaw 当前阶段不划算。

LocalClaw 当前只应聚焦：

- CLI
- Web UI

### 2. 远程节点 / 移动端节点

OpenClaw 的 `system.run` / `node.invoke` / 移动端能力适合成熟平台，不适合当前阶段直接引入。

LocalClaw 先把本机工具运行、安全审批、技能系统做好。

### 3. 插件大生态

OpenClaw 的插件边界做得很深，但 LocalClaw 现在连 skill 边界还没稳。

建议顺序：

1. 先把 tools 稳定
2. 再把 skills 稳定
3. 最后再考虑 plugin

## 对 LocalClaw 的具体落地方向

### A. 配置层

建议改造：

- `localclaw/config/settings.py`

目标：

- 默认 `mode = local`
- 默认 `llm_enabled = true`
- 新增本地 provider 配置
- 支持 OpenAI-compatible base URL

### B. LLM 适配层

建议改造：

- `localclaw/llm/`

目标：

- 不再只绑定 Ollama
- 抽象统一 provider 接口
- 至少支持：
  - Ollama
  - LM Studio
  - 通用 OpenAI-compatible 本地端点

### C. Skill 系统

建议改造：

- `localclaw/skills/loader.py`
- `localclaw/skills/base.py`
- `localclaw/skills/registry/`

目标：

- 支持 `SKILL.md`
- 支持目录型技能
- 支持门控与状态
- 支持 workspace 覆盖

### D. Parser / Planner

建议改造：

- `localclaw/core/parser.py`
- `localclaw/core/planner.py`

目标：

- parser 只负责意图理解和参数抽取
- planner 负责把意图映射到：
  - skill
  - tool
  - workflow
- 避免把业务逻辑硬编码在 parser prompt 里

### E. 安全层

建议改造：

- `localclaw/core/verifier.py`
- `localclaw/tools/shell_tool.py`
- `localclaw/channels/web.py`

目标：

- 高风险操作默认不自动通过
- 保留审批记录
- allowlist 与审批策略从 UI 解耦

## 推荐的三阶段路线

### Phase 1：先对齐骨架

- 引入本地 provider 抽象
- 默认切到 local-only
- 修正审批逻辑
- 让 parser / planner / skill 能稳定闭环

### Phase 2：把 skill 做成 OpenClaw 风格

- 支持 `SKILL.md`
- 支持目录型 skill
- 支持依赖门控
- 支持 workspace 覆盖与热加载

### Phase 3：再加高级能力

- Web search provider 抽象
- 更好的上下文压缩
- 审计日志与运行可视化
- 必要时再考虑 plugin，而不是现在

## 当前建议

从现在开始，LocalClaw 的产品方向建议固定为：

**参考 OpenClaw 的架构和技能机制，但不复制它的平台规模；以本地模型和低成本运行为第一原则。**

一句话版本：

**借 OpenClaw 的结构，不借 OpenClaw 的体量。**

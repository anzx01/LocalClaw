# LocalClaw

LocalClaw 是一个本地优先的 Agent Runtime，目标是尽量零成本、尽量本地化地完成这些事：

- 通过 Web UI 操作本机任务
- 通过个人微信或 WhatsApp 给电脑发指令
- 指挥 IDE 辅助编程
- 自动化执行命令
- 生成日报、周报、报表、摘要和文件总结
- 用 OpenClaw 风格的 `skills` 插件扩展能力

当前产品方向已经明确为：

> 所有输入默认先交给本地大模型解析，再进入 Planner / Engine / Tool / Skill 链路。

这意味着 LocalClaw 不再把“规则解析器优先”作为主产品路线，而是以“本地大模型统一理解 + skills 插件扩展 + 多渠道控制”为核心。

## 当前定位

LocalClaw 现在更接近一个“本地控制台 + 多渠道入口 + skills 插件系统”：

- Web UI 是主控制台
- CLI 是调试和快速操作入口
- 个人微信是实验性的个人控制入口
- WhatsApp 是标准化的外部消息入口
- Tool 是唯一执行入口
- Skill 是能力扩展层
- 高风险操作统一进入审批或保护策略

## 已实现能力

### 1. 本地大模型统一解析输入

默认配置方向：

```env
LOCALCLAW_MODE=local
LOCALCLAW_LLM_ENABLED=true
LOCALCLAW_LLM_PARSE_ONLY=true
```

当前行为：

- 所有自然语言输入优先交给本地模型理解
- `/cmd`、`/shell` 等命令式输入也纳入统一解析链路
- 模型会结合当前可用的 tools 和 skills 来决定下一步动作
- 不再依赖传统规则解析器作为主入口
- 如果关闭本地模型（`LOCALCLAW_LLM_ENABLED=false`），系统不会回退旧 parser 兼容链，而是直接提示你先安装并启用本地大模型

### 2. Web UI 控制台

当前 Web UI 已有这些核心页面：

- `Chat`：发送任务、提问、执行命令
- `Tasks`：查看任务历史和执行结果
- `Approvals`：审批高风险步骤
- `Skills`：查看本地技能与 ClawHub 安装入口
- `Channels`：查看和测试微信 / WhatsApp 渠道

### 3. 自动化执行命令

已支持两类命令入口：

常规命令：

```text
/cmd git status
/cmd pytest
执行命令 git status
执行命令 python run_server.py
```

高风险原始 shell：

```text
/shell git pull
/shell del /s /q temp
```

当前策略：

- 常规命令默认走 `safe_shell`
- 原始 shell 走 `shell`
- 原始 shell 默认进入审批中心，等待人工确认

### 4. OpenClaw 风格 skills 插件

当前已经支持：

- `skills/<name>/SKILL.md`
- `skills/*.json`
- `skills/*.yaml`
- requirements / metadata / availability / `user-invocable`
- 模型基于 skill 描述直接返回 `skill.<name>` 意图

这意味着技能体系已经不是临时脚本集合，而是正式的可扩展能力层。

### 5. 第三方 Skill 安全机制

这是当前版本非常重要的一部分。

#### 安装前必须先做安全体检

每次从 ClawHub 安装第三方 skill 前，都会先扫描，再由用户明确决定是否继续安装，不再允许“点一下直接装”。

安装前体检重点覆盖 8 类风险：

- 密钥收割型
- 挖矿注入型
- 动态拉取型
- 越权访问型
- 仿冒官方型
- 无作者溯源型
- 第三方内容抓取型
- 无评分无维护型

同时还会单独扫描这些高危能力型 skill：

- 要你输入 API Key / Token / Secret / 私钥
- 能执行命令或做系统管理
- 能控制浏览器或做网页自动化
- 能读取文件或访问文件系统
- 能发起网络请求
- 能设置定时任务、后台运行或自动执行

为什么要重点盯这几类：

- 要输入密钥的 skill，可能把你的密钥发到任何地方
- 能执行命令的 skill，相当于把终端操作权交给自动化流程
- 能控制浏览器的 skill，可能接触你的登录态、Cookies 和已登录账号
- 能读取文件的 skill，可能看到 SSH 私钥、配置文件和敏感目录
- 能发起网络请求的 skill，可能访问外网、内网或把数据带出去
- 能设置定时任务的 skill，可能在你不知情时持续后台运行

#### 安装后默认继续保护，而不是装完就放开

现在第三方 skill 安装成功后，还会自动写入一层“安装后保护策略”，默认不是完全放行。

可配置项：

```env
LOCALCLAW_SKILL_INSTALL_PROTECTION_MODE=disable_high_risk
LOCALCLAW_SKILL_ISOLATION_REQUIRE_APPROVAL=true
LOCALCLAW_SKILL_ISOLATION_BLOCK_CRITICAL=true
```

支持三种模式：

- `off`
  - 不追加安装后保护策略
- `disable_high_risk`
  - 默认模式
  - 直接禁用高危工具权限
  - 自动禁用定时触发器和后台触发
- `isolate`
  - 以隔离模式安装 skill
  - 受保护工具默认要求人工审批后才能运行
  - 可继续阻断 `shell` 这类关键危险工具
  - 自动禁用触发器，避免静默后台执行

当前被纳入安装后保护范围的能力主要包括：

- `shell` / `safe_shell`
- 浏览器控制工具
- 文件读取、写入、删除、遍历
- 网络请求工具
- 定时触发器

也就是说，现在第三方 skill 的安全策略是两层：

1. 安装前先体检，给建议和选择。
2. 安装后继续默认收紧高危权限，或者隔离运行。

### 6. 个人微信接入

当前接入的是“个人微信桥接方案”，不是官方个人微信 API。

已实现接口：

- `POST /api/channels/wechat-personal/webhook`
- `GET /api/channels/wechat-personal/status`
- `POST /api/channels/wechat-personal/test`

当前定位：

- 适合“我给自己的电脑发消息”
- 依赖你自己的桥接器或第三方桥接器转发消息到 LocalClaw
- 可选把执行结果通过代理地址回推

### 7. WhatsApp 接入

当前实现的是 WhatsApp Cloud API 风格接入。

已实现接口：

- `GET /api/channels/whatsapp/webhook`
- `POST /api/channels/whatsapp/webhook`
- `GET /api/channels/whatsapp/status`
- `POST /api/channels/whatsapp/test`

当前定位：

- 更标准化
- 更适合稳定外部接入
- 如果出站配置完整，可以直接发送真实回复

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 准备本地模型

推荐 Ollama：

```bash
ollama pull qwen3:4b
ollama serve
```

如果你更看重中文理解，优先推荐：

- `qwen3:4b`
- `qwen2.5:7b`

不建议把 `LOCALCLAW_LLM_ENABLED` 设成 `false` 作为日常运行方式。当前产品路线要求先装好本地大模型，再启动 LocalClaw。

### 3. 配置 `.env`

最小本地配置示例：

```env
LOCALCLAW_MODE=local
LOCALCLAW_LLM_ENABLED=true
LOCALCLAW_LLM_PARSE_ONLY=true
LOCALCLAW_MODEL_PROVIDER=ollama
LOCALCLAW_MODEL_NAME=qwen3:4b
LOCALCLAW_SERVER_HOST=127.0.0.1
LOCALCLAW_SERVER_PORT=8000
LOCALCLAW_SKILL_INSTALL_PROTECTION_MODE=disable_high_risk
```

如果你希望第三方 skill 隔离运行：

```env
LOCALCLAW_SKILL_INSTALL_PROTECTION_MODE=isolate
LOCALCLAW_SKILL_ISOLATION_REQUIRE_APPROVAL=true
LOCALCLAW_SKILL_ISOLATION_BLOCK_CRITICAL=true
```

### 4. 启动服务

方式一：

```bash
python run_server.py
```

如果启动后提示“请先安装并启用本地大模型”，说明当前没有按默认路线配置好本地模型。先确认：

- `ollama serve` 已经启动
- 已拉取可用模型，例如 `ollama pull qwen3:4b`
- `.env` 中保持 `LOCALCLAW_LLM_ENABLED=true`

当前 `run_server.py` 默认监听：

```text
http://127.0.0.1:8016
```

方式二：

```bash
uvicorn localclaw.channels.web:create_app --factory --host 127.0.0.1 --port 8000
```

### 5. 打开 Web UI

- 如果走 `python run_server.py`：`http://127.0.0.1:8016`
- 如果走 `uvicorn`：`http://127.0.0.1:8000`

## 推荐使用方式

### 1. 直接自然语言输入

例如：

- `帮我总结这个目录`
- `生成今天的项目日报`
- `查看最近失败的任务`
- `执行命令 git status`
- `帮我写一个读取日志并生成报表的 skill`

### 2. 命令式输入做自动化

例如：

- `/cmd pytest tests/test_parser.py`
- `/cmd python run_server.py`
- `/shell git pull`

### 3. 用 skills 做长期沉淀

推荐把这些需求沉淀成 skill：

- 文件总结
- 周报 / 日报 / 项目汇总
- IDE 辅助编程流程
- 测试执行与结果整理
- 报表生成

## Skill 目录建议

推荐结构：

```text
skills/
  my_skill/
    SKILL.md
    scripts/
    assets/
```

最小 `SKILL.md` 示例：

```md
---
name: my_report_skill
version: 1.0.0
description: 汇总目录中的文件并生成 markdown 报告
type: workflow
inputs:
  path: string
tools:
  - file_list
actions:
  - type: tool_call
    tool: file_list
    params:
      path: $path
user-invocable: true
---
# My Report Skill

用于列出目录内容，并为后续总结流程提供输入。
```

## 当前边界

为了避免把“目标”写成“现状”，这里明确一下当前还没完全做完的部分：

- 还不是完整的桌面 GUI 自动化系统
  - 例如鼠标点击、窗口切换、屏幕识别、桌面录制等还没有做成完整产品能力
- 个人微信目前是桥接接入，不是官方个人 API
- 报表、IDE、文件整理等更强的工作流 skill 还需要继续补
- 第三方 skill 的保护策略已经落地，但更细粒度的沙箱和权限画像还可以继续增强

## 一句话理解

你可以把当前 LocalClaw 理解成：

> 一个以 Web UI 为主控制台、以本地大模型为统一解析器、以 OpenClaw 风格 skills 为插件层、以个人微信和 WhatsApp 为消息入口，并且对第三方 skill 先体检再持续保护的本地自动化 Agent Runtime。

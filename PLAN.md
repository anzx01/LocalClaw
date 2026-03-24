# LocalClaw 当前计划

## 1. 产品目标

LocalClaw 当前的目标不是继续做“规则解析优先”的实验框架，而是做成一个真正可本地落地使用的 Agent 控制台：

- 尽量零成本、尽量本地优先
- 通过 Web UI、个人微信、WhatsApp 给本机发任务
- 用自然语言指挥 IDE 辅助编程、执行命令、整理文件
- 生成日报、周报、项目总结、文件摘要和报表
- 用 OpenClaw 风格的 skills 扩展能力
- 对高风险动作保留审批、隔离或禁用策略

## 2. 已经确定的产品决策

### 2.1 统一解析策略

当前路线已经明确：

- 默认启用本地大模型
- 默认启用 `LOCALCLAW_LLM_PARSE_ONLY=true`
- 所有输入优先交给本地大模型解析
- `/cmd`、`/shell` 也纳入统一解析链路
- 不再把规则解析器当作主产品路线
- 如果 `LOCALCLAW_LLM_ENABLED=false`，默认产品链路直接报错并提示安装本地大模型，不再自动回退旧 parser 兼容链

后续优化重点应该放在：

- 提升本地模型提示词和参数抽取质量
- 提升 tools / skills 暴露给模型的可理解性
- 补失败回退和错误提示

而不是继续堆更多 parser 规则。

### 2.2 主入口和执行原则

当前入口分工：

- Web UI 是主控制台
- CLI 是调试入口
- 个人微信是实验性个人入口
- WhatsApp 是标准化外部入口

当前执行原则：

- Tool 是唯一执行入口
- 常规命令走 `safe_shell`
- 原始命令走 `shell`
- 高风险步骤进入审批中心
- 第三方 skill 先体检，再安装，再继续受保护

### 2.3 Skill 路线

当前已经明确采用 OpenClaw 风格的 skills 路线：

- 支持 `SKILL.md`
- 支持 `.json` / `.yaml`
- 支持 metadata / requirements / availability
- 支持模型直接返回 `skill.<name>`
- 支持多目录加载和能力暴露过滤

Skill 不是临时脚本集合，而是正式能力扩展层。

## 3. 当前已经落地的能力

### 3.1 本地模型统一解析

已落地：

- `localclaw/config/settings.py`
  - 默认 `mode=local`
  - 默认 `llm_enabled=true`
  - 默认 `llm_parse_only=true`
- `localclaw/core/engine.py`
  - 默认消息入口优先走本地模型理解链路
  - 当本地模型关闭时，直接提示用户先安装并启用本地大模型
  - 不再自动掉回旧 parser 兼容链
- `localclaw/core/planner.py`
  - 本地模型直接把原始输入理解成 `Intent`
  - prompt 会动态注入当前可用 skills
- `localclaw/channels/cli.py` / `localclaw/channels/web.py`
  - 默认初始化时不再显式注入兼容 parser
  - 保证运行时行为与“本地模型必需”路线一致

### 3.2 Web UI 和基础 API

已落地：

- `Chat`
- `Tasks`
- `Approvals`
- `Skills`
- `Channels`

已落地接口：

- `POST /api/message`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/approvals`
- `POST /api/tasks/{task_id}/approve/{step_id}`
- `GET /api/skills`
- `GET /api/channels`
- `GET /api/config`

### 3.3 自动化执行命令

已落地：

- `/cmd <command>` -> `safe_shell`
- 自然语言 `执行命令 xxx` -> `safe_shell`
- `/shell <command>` -> `shell`
- 高风险 shell 进入审批中心等待批准

这条链路已经满足“可以自动化执行命令”的核心要求。

### 3.4 OpenClaw 风格 skills

已落地：

- 本地 skill 加载与注册
- `SKILL.md` / JSON / YAML 支持
- metadata / requirements / availability 检查
- 对模型可调用 skill 的过滤暴露
- 将 skill workflow 转成 step 执行链路

### 3.5 第三方 skill 安装前安全体检

已落地：

- ClawHub 安装前必须先 scan
- 用户必须显式选择 `proceed` 才允许继续安装
- Web UI 改为“体检后安装”

当前安装前体检覆盖：

- 8 类恶意 / 可疑插件风险
  - 密钥收割型
  - 挖矿注入型
  - 动态拉取型
  - 越权访问型
  - 仿冒官方型
  - 无作者溯源型
  - 第三方内容抓取型
  - 无评分无维护型
- 6 类高危能力型 skill
  - 要输入密钥
  - 能执行命令
  - 能控制浏览器
  - 能读取文件
  - 能发起网络请求
  - 能设置定时任务 / 后台运行

### 3.6 第三方 skill 安装后保护策略

这一项现在也已经落地，不只是计划。

已落地：

- 安装时自动根据 skill 声明的工具能力写入 `metadata.localclaw_guard`
- 运行时 verifier 会基于 `source_skill_name` 读取 guard
- 被禁用的高危工具会直接拦截
- 隔离模式下的受保护工具会先进入审批
- 触发器可在安装后自动禁用，避免后台静默运行

当前支持的模式：

- `off`
  - 不做安装后保护
- `disable_high_risk`
  - 默认模式
  - 直接禁用高危工具
  - 同时禁用 trigger
- `isolate`
  - 隔离运行
  - 受保护工具默认审批
  - 可继续阻断关键危险工具，如 `shell`
  - 同时禁用 trigger

相关配置：

```env
LOCALCLAW_SKILL_INSTALL_PROTECTION_MODE=disable_high_risk
LOCALCLAW_SKILL_ISOLATION_REQUIRE_APPROVAL=true
LOCALCLAW_SKILL_ISOLATION_BLOCK_CRITICAL=true
```

这满足了“安装后默认禁用高危权限”或“隔离运行”，并且“可设置”的要求。

### 3.7 个人微信接入

已落地接口：

- `POST /api/channels/wechat-personal/webhook`
- `GET /api/channels/wechat-personal/status`
- `POST /api/channels/wechat-personal/test`

当前定位：

- 面向“自己给自己的电脑发消息”
- 依赖桥接器转发到 LocalClaw
- 支持代理回推结果

### 3.8 WhatsApp 接入

已落地接口：

- `GET /api/channels/whatsapp/webhook`
- `POST /api/channels/whatsapp/webhook`
- `GET /api/channels/whatsapp/status`
- `POST /api/channels/whatsapp/test`

当前定位：

- 更标准化
- 更适合稳定外部接入
- 支持 Cloud API 风格回复

## 4. 当前能力边界

为了防止文档和现实脱节，这里明确当前还没完全做完的部分：

- 还没有完整的桌面 GUI 自动化
  - 例如鼠标点击、窗口切换、图像识别、桌面录制等
- 还没有完整的 IDE 编程产品化工作流
  - 例如“自动读项目 -> 改代码 -> 跑测试 -> 汇总 patch”的成熟多步技能包
- 报表模板中心还不完整
- 个人微信桥接器本体仍需要你自己部署或接第三方方案
- 第三方 skill 的权限保护已落地，但更细粒度沙箱仍值得继续补

## 5. 近期优先级

### P0：先把“稳定可用”继续补齐

- 把个人微信桥接接入文档写完整
- 增加文件总结、目录汇总、日报、周报、项目总结类 skills
- 把 Web UI 里的技能安装说明继续补清楚
- 把审批中心提示文案做得更直观
- 把安装后保护模式在 UI 中显示得更清楚

### P1：把“指挥 IDE 编程”做得更像产品

- 增加代码库分析 skill
- 增加变更总结 skill
- 增加测试执行与结果汇总 skill
- 增加面向 IDE 场景的多步 workflow skill
- 增加更明确的命令白名单和权限模板

### P2：把“操作电脑”从半自动推进到完整自动化

- 增加桌面 GUI 控制能力
- 增加窗口 / 应用感知
- 增加定时任务和目录监听
- 增加更强的多 Agent / 子任务能力
- 增加报表模板中心和输出格式模板

## 6. 暂不继续作为主线推进的旧方向

以下方向不是完全放弃，但不再作为当前主线叙事：

- 不再以 Zero Mode 作为默认产品故事
- 不再以规则解析优先作为主流程
- 不再把“无 LLM 也能完整使用”当作第一卖点
- 不再把“关闭本地模型后自动回退 parser”当作默认兼容能力
- 不再把企业微信优先级放在个人微信前面

当前主线已经变成：

> 用本地大模型 + Web UI + 个人微信 / WhatsApp + OpenClaw 风格 skills，先把真正可用的本地自动化体验做出来。

## 7. 文档同步要求

后续只要下面这些代码方向发生变化，文档就要同步更新：

- `localclaw/config/settings.py`
- `localclaw/core/engine.py`
- `localclaw/core/parser.py`
- `localclaw/core/planner.py`
- `localclaw/core/verifier.py`
- `localclaw/tools/clawhub_tool.py`
- `localclaw/skills/security_review.py`
- `localclaw/channels/web.py`
- `.env.example`
- `README.md`
- `PLAN.md`

避免再次出现“代码已经变了，README 和 PLAN 还是旧路线”的情况。

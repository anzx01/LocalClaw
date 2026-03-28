# LocalClaw 开发进度（2026-03-27）

## 已完成
- Weixin 通道打通并稳定可回包。
- 增加 Weixin 轮询收消息（`getupdates`）能力，避免必须配置微信侧 webhook 管理入口。
- 对齐 Weixin API 请求头（`AuthorizationType`、`X-WECHAT-UIN`），修复 `errcode=-14` 的一类问题。
- Weixin 回复格式改为优先使用通用聊天格式化器，避免返回 `Task completed successfully` 这种默认文案。
- 新增运行时配置开关：`LOCALCLAW_RUNTIME_REQUEST_GUARDRAILS_ENABLED`。
  - 作用：控制是否启用 runtime 的规则兜底（天气/新闻/文件等 guardrail）。
  - 当前 `.env` 已设置为 `false`（走“大模型解析优先”）。
- Skills 页面结构调整：
  - 把 `Installed Skills` / `Available Skills` 主列表提前到首屏。
  - 保留 `Security Workflow` / `Skill Storage Layout`，但移到主列表下方。
  - 当 `Available = 0` 且 bundled catalog 已全部安装时，明确提示“不是空白，是都装过了”。
- Skills 安装入口增强：
  - 支持从 Web UI 上传本地 skill 文件安装，不再只依赖 Bundled / ClawHub。
  - 当前支持 `zip`、`tar/tgz`、单个 `SKILL.md`、以及 `json/yaml` skill 定义。
  - 上传后仍然先做安全体检，再确认安装，最后注册到 `managed_skills_dir`。
- Windows 自动启动链路调整：
  - Settings 中的安装逻辑改为优先注册 `Task Scheduler` 开机自启动任务。
  - 旧版通过 `sc.exe create` 注册的 SCM service 会被识别为 `legacy`，并给出迁移提示。
  - `sc start ... -> 1053` 现在会明确解释为旧方案和 Windows Service 控制协议不匹配，而不是 winsock 问题。
- 本轮回归通过：`pytest -q tests/test_windows_service.py tests/test_channels_api.py`（`14 passed`）。
- 此前全量记录：`pytest -q` 曾通过（`235 passed`），但本轮改动后还没重新完整跑一遍全量。

## 当前状态
- 服务运行端口：`127.0.0.1:8016`。
- `.env` 关键配置：
  - `LOCALCLAW_LLM_ENABLED=true`
  - `LOCALCLAW_LLM_PARSE_ONLY=true`
  - `LOCALCLAW_RUNTIME_REFINE_SKILL_DECISION=false`
  - `LOCALCLAW_RUNTIME_REQUEST_GUARDRAILS_ENABLED=false`
- Skills 页现状：
  - `Installed = 12`
  - `Available = 0`
  - 这不是接口没返回，而是 bundled catalog 里的技能当前都已经安装到 `managed_skills_dir` 里了。
- 自动启动现状：
  - 当前机器上仍能检测到旧版 `LocalClaw` SCM service。
  - 状态查询会把它标成 legacy，并提示重新安装以迁移到 Task Scheduler。
- 上传安装现状：
  - API 已提供 `/api/skills/upload/scan` 和 `/api/skills/upload/install`。
  - 相关 UI 已接到 Skills 页面里的 `Upload Skill File` 按钮。

## 仍待处理问题
- 纯“大模型解析”路径下，`今天热吗？` 仍可能走到 `unknown`：
  - 观察到 runtime 决策超时（30s）后回退到 deterministic parser。
  - 日志特征：`OpenClaw runtime decision timed out after 30.0s; using deterministic parser fallback`。
- 这意味着当前瓶颈更偏向 **LLM 调用稳定性/时延**，而不是 Weixin 通道本身。
- Task Scheduler 新链路已经进代码，但当前还没有做一次完整的“真实安装 -> 重启后自动拉起 -> 页面回报 RUNNING”的端到端回归。
- legacy service 是否要在迁移时自动删除，当前实现是“安装新自动启动任务时尝试清理”；还需要再观察不同机器上的权限反馈。

## 下次继续建议
1. 先排查 LLM 可用性与时延（Ollama 推理耗时、模型加载、超时参数）。
2. 如果确认模型可用但慢，调大 `LOCALCLAW_DEFAULT_TIMEOUT`（例如 60~90）再复测。
3. 继续优化 runtime/planner prompt 的天气请求指令，让模型更稳定返回 weather skill 或 `check_weather`。
4. 在 Settings 页面点一次 Install，确认 legacy service 成功迁移为 Task Scheduler 自动启动任务。
5. 如果还要继续清 UI，可以把 Skills 页里的“已安装 marketplace 技能”做成更明显的 badge 或分组。

## 关键文件
- `localclaw/channels/weixin.py`
- `localclaw/channels/web.py`
- `localclaw/core/openclaw_runtime.py`
- `localclaw/core/engine.py`
- `localclaw/core/parser.py`
- `localclaw/core/planner.py`
- `localclaw/config/settings.py`
- `localclaw/skills/registry/clawhub.py`
- `static/index.html`
- `.env`
- `.env.example`

## 常用命令（下次接着用）
```powershell
# 启动服务
python -m uvicorn localclaw.channels.web:create_app --host 127.0.0.1 --port 8016 --factory

# 查看实时日志
Get-Content .\lc8016.log -Tail 120 -Wait

# 跑测试
pytest -q
```

```powershell
# 查看自动启动状态（当前会识别 Task Scheduler / legacy service）
@'
from localclaw.system.windows_service import get_background_service_status
print(get_background_service_status())
'@ | python -
```

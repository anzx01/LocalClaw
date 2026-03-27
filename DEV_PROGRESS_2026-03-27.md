# LocalClaw 开发进度（2026-03-27）

## 已完成
- Weixin 通道打通并稳定可回包。
- 增加 Weixin 轮询收消息（`getupdates`）能力，避免必须配置微信侧 webhook 管理入口。
- 对齐 Weixin API 请求头（`AuthorizationType`、`X-WECHAT-UIN`），修复 `errcode=-14` 的一类问题。
- Weixin 回复格式改为优先使用通用聊天格式化器，避免返回 `Task completed successfully` 这种默认文案。
- 新增运行时配置开关：`LOCALCLAW_RUNTIME_REQUEST_GUARDRAILS_ENABLED`。
  - 作用：控制是否启用 runtime 的规则兜底（天气/新闻/文件等 guardrail）。
  - 当前 `.env` 已设置为 `false`（走“大模型解析优先”）。
- 测试通过：`pytest -q` 全量通过（`235 passed`）。

## 当前状态
- 服务运行端口：`127.0.0.1:8016`。
- `.env` 关键配置：
  - `LOCALCLAW_LLM_ENABLED=true`
  - `LOCALCLAW_LLM_PARSE_ONLY=true`
  - `LOCALCLAW_RUNTIME_REFINE_SKILL_DECISION=false`
  - `LOCALCLAW_RUNTIME_REQUEST_GUARDRAILS_ENABLED=false`

## 仍待处理问题
- 纯“大模型解析”路径下，`今天热吗？` 仍可能走到 `unknown`：
  - 观察到 runtime 决策超时（30s）后回退到 deterministic parser。
  - 日志特征：`OpenClaw runtime decision timed out after 30.0s; using deterministic parser fallback`。
- 这意味着当前瓶颈更偏向 **LLM 调用稳定性/时延**，而不是 Weixin 通道本身。

## 下次继续建议
1. 先排查 LLM 可用性与时延（Ollama 推理耗时、模型加载、超时参数）。
2. 如果确认模型可用但慢，调大 `LOCALCLAW_DEFAULT_TIMEOUT`（例如 60~90）再复测。
3. 继续优化 runtime/planner prompt 的天气请求指令，让模型更稳定返回 weather skill 或 `check_weather`。

## 关键文件
- `localclaw/channels/weixin.py`
- `localclaw/channels/web.py`
- `localclaw/core/openclaw_runtime.py`
- `localclaw/core/engine.py`
- `localclaw/core/parser.py`
- `localclaw/core/planner.py`
- `localclaw/config/settings.py`
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

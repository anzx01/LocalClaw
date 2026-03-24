# Code Review Findings

审查时间：2026-03-24

审查范围：
- `localclaw/channels/web.py`
- `localclaw/core/engine.py`
- `localclaw/core/parser.py`
- `localclaw/core/planner.py`
- `skills/weather.json`
- `skills/web_search.json`

## 发现

### 1. 高危：Web 通道关闭了高风险操作确认

- 位置：`localclaw/channels/web.py:185-188`
- 问题：Web 初始化时调用了 `verifier.set_require_confirmation_high(False)`，会跳过高风险/关键风险操作的人机确认。
- 影响：`shell` 仍然是 `CRITICAL` 风险工具，按原设计应进入 HITL，但现在会直接执行。`http_post`、安装/删除类操作的风险兜底也被削弱。
- 依据：
  - `localclaw/tools/shell_tool.py:15-17`
  - `localclaw/core/verifier.py:98-100`
- 建议：保留高风险确认，除非有单独的鉴权、审批或受限执行环境作为替代。

### 2. 中高：`deque` 替换 `list` 后仍使用切片，历史查询会报错

- 位置：
  - `localclaw/core/engine.py:67`
  - `localclaw/core/engine.py:438-446`
- 问题：`_task_history` 已改成 `deque(maxlen=1000)`，但 `get_task_history()` 和 `save_state()` 仍使用 `self._task_history[-limit:]` 与 `self._task_history[-100:]`。
- 影响：运行时会抛出 `TypeError: sequence index must be integer, not 'slice'`。
- 已复现：
  - `engine.get_task_history(10)` 直接报错
  - `/api/tasks` 和按历史回退查任务的路径会受影响
- 建议：在切片前转成 `list(self._task_history)`，或统一改成显式迭代/`itertools.islice`。

### 3. 中危：新增 `web_search` intent 没有 planner 路径，开启 LLM 后会走到 unknown

- 位置：
  - `localclaw/core/parser.py:187-190`
  - `localclaw/core/planner.py`
- 问题：LLM 解析器会把搜索请求映射为 `web_search` intent，但 planner 没有 `if intent.intent == "web_search"` 的分支。
- 影响：开启 `llm_enabled` 后，联网搜索请求会落到 unknown，返回 `Unknown intent: web_search`。
- 已复现：直接调用 `planner.plan(Intent(intent="web_search", params={"query": "OpenAI"}))`，得到 unknown plan。
- 建议：补齐 `web_search` 的 planner 分支，或统一走 `skill.web_search` / `tool.http_get` 的现有路径。

### 4. 中危：两个新 skill 的参数模板格式与 planner 不兼容，变量不会替换

- 位置：
  - `skills/weather.json:23`
  - `skills/weather.json:30`
  - `skills/web_search.json:20`
  - `localclaw/core/planner.py:361-373`
  - `localclaw/core/engine.py:407-408`
- 问题：
  - skill 使用了 `${location}`、`${query}`、`${weather_result}` 这种占位符
  - planner 只支持 `$name` 或 Jinja `{{name}}`
  - 引擎暴露的上一步输出变量名是 `step_<id>`，不是 `weather_result`
- 影响：
  - `/web_search query=OpenAI` 实际请求的是字面量 `${query}`
  - `/weather location=Beijing` 的第二步会输出字面量 `${weather_result}`
- 已复现：
  - `/web_search query=OpenAI`
  - `/weather location=Beijing`
- 建议：
  - 统一模板语法
  - 明确步骤结果的命名和引用机制
  - 为 skill workflow 增加端到端测试

### 5. 中危：天气规则只识别意图，不提取地点，默认路径会误查北京

- 位置：
  - `localclaw/core/parser.py:337-348`
  - `localclaw/core/planner.py:73-81`
  - `localclaw/config/settings.py:28-29`
- 问题：
  - 规则只能把文本识别为 `check_weather`
  - 不会提取“上海”“北京”等地点参数
  - planner 在没有地点时固定回退到 `Beijing`
  - 默认 `llm_enabled` 为 `false`，所以默认用户路径无法依赖 LLM 补救
- 影响：
  - “今天上海天气怎么样”“上海天气”“北京天气”等请求，在默认配置下都会查北京
- 已复现：
  - `今天上海天气怎么样 => Beijing`
  - `上海天气 => Beijing`
  - `北京天气 => Beijing`
- 建议：为天气规则补充地点提取，或在未识别到地点时要求澄清，而不是静默回退到固定城市。

## 备注

- 本次没有运行项目正式测试套件。
- 结论基于 diff 阅读和最小化本地复现。

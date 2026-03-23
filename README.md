# LocalClaw 使用说明

LocalClaw 是一个本地优先的智能代理运行时系统，支持使用本地大模型处理自然语言查询。

## 系统依赖

- Python 3.10+
- Ollama (本地大模型服务)
- 依赖包：
  - pydantic>=2.0
  - fastapi>=0.110
  - uvicorn>=0.29
  - click>=8.1
  - python-dotenv>=1.0
  - aiosqlite>=0.20
  - apscheduler>=3.10
  - httpx>=0.27
  - pyyaml>=6.0
  - aiohttp>=3.13
  - requests>=2.31

## 安装步骤

1. **安装 Python 3.10+**
   从 [Python 官网](https://www.python.org/downloads/) 下载并安装 Python 3.10 或更高版本。

2. **安装 Ollama**
   从 [Ollama 官网](https://ollama.com/) 下载并安装 Ollama，然后启动 Ollama 服务。

3. **拉取模型**
   打开命令行，运行：
   ```bash
   ollama pull gemma3:4b
   ```

4. **克隆项目**
   ```bash
   git clone <项目地址>
   cd LocalClaw
   ```

5. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

6. **配置环境变量**
   创建 `.env` 文件，添加以下内容：
   ```env
   # LLM 配置
   LLM_ENABLED=true
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=gemma3:4b
   ```

## 启动方法

### 方法 1：使用 CLI 运行单次查询

```bash
python main.py run "今天星期几"
```

### 方法 2：启动 Web 服务

```bash
python main.py serve
```

服务将在 `http://127.0.0.1:8000` 启动。

## 使用示例

### 1. 命令行查询

```bash
# 查询今天星期几
python main.py run "今天星期几"

# 其他查询示例
python main.py run "你好"
python main.py run "列出所有技能"
```

### 2. Web 服务 API

```bash
# 使用 curl 测试 API
curl -X POST http://localhost:8000/api/message \
  -H "Content-Type: application/json" \
  -d '{"content": "今天星期几", "user_id": "test", "channel": "test"}'
```

### 3. Web 界面

打开浏览器访问 `http://127.0.0.1:8000`，在输入框中输入查询。

## 技能系统

LocalClaw 使用技能系统来处理不同类型的查询：

- **day_of_week**：获取当前星期几
- **hello**：打招呼
- **echo**：回显消息
- **system_status**：获取系统状态
- **list_skills**：列出所有可用技能

## 自定义技能

在 `skills` 目录中创建新的技能文件，例如 `my_skill.json`：

```json
{
  "name": "my_skill",
  "version": "1.0.0",
  "description": "我的自定义技能",
  "type": "atomic",
  "inputs": {},
  "outputs": {
    "result": "string"
  },
  "actions": [
    {
      "type": "transform",
      "template": "这是我的自定义技能",
      "params": {}
    }
  ],
  "permissions": {
    "risk_level": "low"
  },
  "triggers": [
    {
      "type": "intent",
      "pattern": "my_intent"
    }
  ]
}
```

## 常见问题

### 1. 无法连接到 Ollama

- 确保 Ollama 服务已启动
- 检查 `OLLAMA_BASE_URL` 配置是否正确
- 尝试运行 `ollama list` 确认模型已下载

### 2. 技能未找到

- 确保技能文件位于 `skills` 目录
- 检查技能文件格式是否正确
- 重启服务以加载新技能

### 3. LLM 解析错误

- 确保模型已正确下载
- 检查网络连接
- 尝试使用其他模型，如 `llama3:8b`

## 日志

系统日志位于 `logs` 目录，可用于排查问题。

## 安全注意事项

- 系统支持执行 shell 命令，请谨慎使用
- 高风险操作会触发人工确认
- 定期更新依赖包以确保安全性

# Decision Arena

DeepSeek 风格的个人决策对话工具。前端只负责展示和重连，模型 API key、对话生成任务、SQLite 记录都在后端，切换对话不会中断后端生成。

## 本地运行

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`。

如果 `.env` 里没有 `DEEPSEEK_API_KEY`，后端会使用本地演示流式回复，方便先看界面和会话持久化。

## Docker 部署

```bash
copy .env.example .env
docker compose up -d --build
```

默认端口是 `8000`，数据保存在 Docker volume `decision-arena-data`。

## 环境变量

- `DEEPSEEK_API_KEY`: DeepSeek API key，保存在后端环境中
- `MODEL_BASE_URL`: OpenAI 兼容接口地址，默认 `https://api.deepseek.com`
- `MODEL_NAME`: 模型名，默认 `deepseek-chat`
- `DATA_DIR`: SQLite 数据目录

## 架构

- `frontend/`: 纯静态聊天 UI，左侧会话栏，中间对话流，SSE 自动重连
- `backend/app/main.py`: FastAPI HTTP 和 SSE 路由
- `backend/app/chat_engine.py`: 后台对话生成任务
- `backend/app/store.py`: SQLite 会话、消息、事件存储
- `backend/app/providers.py`: DeepSeek/OpenAI 兼容模型调用和本地 demo provider

# Decision Arena

DeepSeek 风格的个人决策对话工具。前端只负责展示和重连，模型 API key、对话生成任务、SQLite 记录都在后端，切换对话不会中断后端生成。

`expert` 模式现在会走真正的多 agent 辩论编排，而不是单次 prompt 模拟。后端会依次执行问题澄清、多个 agent 独立分析、交叉质询、评分矩阵和裁判总结，并把阶段事件持久化到 SQLite。

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
- `backend/app/debate_engine.py`: 多 agent 辩论编排，驱动 `expert` 模式
- `backend/app/store.py`: SQLite 会话、消息、事件存储
- `backend/app/providers.py`: DeepSeek/OpenAI 兼容模型调用和本地 demo provider

## Expert 模式流程

1. 问题澄清：结构化目标、候选方案、约束和评价维度
2. 独立 Agent 分析：支持者、反对者、现实主义者、长期主义者、情绪观察者、技术评审
3. 交叉质询：汇总各视角互相同意、反驳和遗漏的地方
4. 评分矩阵：按长期收益、短期成本、机会成本、失败风险、可逆性等维度打分
5. 裁判总结：输出最终建议、置信度、最大风险、最小行动、止损条件和复盘问题

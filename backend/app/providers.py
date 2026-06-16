import asyncio
import json

from .settings import DEFAULT_BASE_URL, DEFAULT_MODEL, ModelSettings


SYSTEM_PROMPTS = {
    "quick": "你是 Decision Arena 的快速模式。给出清晰、短促、可执行的建议。",
    "expert": "你是 Decision Arena 的专家模式。用多视角辩论、评分矩阵、反方质疑和裁判总结来分析。",
    "vision": "你是 Decision Arena 的识图模式。如果消息里没有图片，提醒用户上传图片并先基于文字分析。",
    "debate.clarifier": "你是问题澄清 agent。只做结构化澄清，不急着下结论。",
    "agent.supporter": "你是支持者 agent。积极论证方案价值，同时避免空泛鼓励。",
    "agent.opponent": "你是反对者 agent。寻找风险、代价、盲点和失败条件。",
    "agent.realist": "你是现实主义者 agent。重点看资源、时间、执行难度和依赖。",
    "agent.long_termist": "你是长期主义者 agent。重点看长期收益、路径依赖和机会成本。",
    "agent.emotion_observer": "你是情绪观察者 agent。识别冲动、逃避、焦虑和自欺。",
    "agent.technical_reviewer": "你是技术评审 agent。评估工程路线、复杂度、部署和依赖风险。",
    "debate.cross_examination": "你是交叉质询主持人。让各视角互相指出盲点并承认自身可能错误。",
    "debate.scoring_matrix": "你是评分矩阵 agent。输出结构化权重、分数和理由。",
    "debate.judge": "你是裁判 agent。综合所有阶段，给出可执行建议和复盘条件。",
}


class DemoProvider:
    async def stream_reply(self, messages, mode):
        latest = messages[-1]["content"] if messages else ""
        text = (
            f"{mode} 本地演示回复。\n\n"
            f"输入摘要：{latest[:160]}\n\n"
            "后端没有检测到可用的模型 API key，所以这里用确定性 demo 文本模拟流式输出。"
        )
        for char in text:
            await asyncio.sleep(0.01)
            yield char


class OpenAICompatibleProvider:
    def __init__(self, settings=None):
        self.settings = settings

    async def stream_reply(self, messages, mode):
        settings = await self._settings()
        api_key = settings["api_key"]
        if not api_key:
            async for chunk in DemoProvider().stream_reply(messages, mode):
                yield chunk
            return

        import httpx

        payload = {
            "model": settings["model"],
            "stream": True,
            "messages": [{"role": "system", "content": SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["quick"])}]
            + [{"role": item["role"], "content": item["content"]} for item in messages],
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        url = settings["base_url"].rstrip("/") + "/v1/chat/completions"

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    delta = event["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content

    async def _settings(self):
        if isinstance(self.settings, ModelSettings):
            return await self.settings.read_private()
        return {
            "api_key": "",
            "base_url": DEFAULT_BASE_URL,
            "model": DEFAULT_MODEL,
        }

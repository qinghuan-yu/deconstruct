import asyncio
import json
import os


SYSTEM_PROMPTS = {
    "quick": "你是 Decision Arena 的快速模式。给出清晰、短促、可执行的建议。",
    "expert": "你是 Decision Arena 的专家模式。用多视角辩论、评分矩阵、反方质疑和裁判总结来分析。",
    "vision": "你是 Decision Arena 的识图模式。如果消息里没有图片，提醒用户上传图片并先基于文字分析。",
}


class DemoProvider:
    async def stream_reply(self, messages, mode):
        latest = messages[-1]["content"] if messages else ""
        text = (
            "这是本地演示回复。后端没有检测到可用的模型 API key，所以我先模拟流式输出。\n\n"
            f"你的问题是：{latest}\n\n"
            "建议先做 MVP：保留左侧会话栏、后台持续生成、SQLite 持久化和 Docker 部署。"
            "等 key 配好后，前端无需改动，后端会切到真实模型。"
        )
        for char in text:
            await asyncio.sleep(0.01)
            yield char


class OpenAICompatibleProvider:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("MODEL_BASE_URL", "https://api.deepseek.com")
        self.model = os.getenv("MODEL_NAME", "deepseek-chat")

    @property
    def configured(self):
        return bool(self.api_key)

    async def stream_reply(self, messages, mode):
        if not self.configured:
            async for chunk in DemoProvider().stream_reply(messages, mode):
                yield chunk
            return

        import httpx

        payload = {
            "model": self.model,
            "stream": True,
            "messages": [{"role": "system", "content": SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["quick"])}]
            + [{"role": item["role"], "content": item["content"]} for item in messages],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = self.base_url.rstrip("/") + "/v1/chat/completions"

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

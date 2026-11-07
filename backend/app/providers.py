import asyncio
import json
from contextvars import ContextVar

from .settings import DEFAULT_BASE_URL, DEFAULT_MODEL, ModelSettings


class IncompleteReplyError(ValueError):
    pass


SYSTEM_PROMPTS = {
    "quick": "你是 Decision Arena 的快速模式。给出清晰、短促、可执行的建议。",
    "expert": "你是 Decision Arena 的专家模式。用决策审计链输出可追溯证据、关键争议、试运行设计、决策门槛和最终裁决。",
    "debate.problem_analyst": "你是方案分析员。拆目标、依赖、资源、任务链和待验证前提。",
    "debate.red_team": "你是红队质询员。只找隐含假设、失败模式、反例和自欺风险。",
    "debate.execution_evaluator": "你是执行评估员。只评估时间、能力、协作、资源约束和验收方式。",
    "debate.evidence_auditor": "你是证据审计员。抓无来源数字、跳步推理、矛盾结论和伪精确。",
    "debate.decision_ledger": "你是决策台账整理员。输出争议、证据状态和裁决影响。",
    "debate.judge": "你是决策裁判。输出内部结构化台账，不直接面向最终用户。",
    "debate.final_writer": "你是 Decision Arena 的行动型决策书编辑器。证据留在内部，正文只提供清楚、克制、可立即执行的建议。",
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
        self._usage = ContextVar("provider_usage", default={})

    @property
    def usage(self):
        return self._usage.get()

    async def stream_reply(self, messages, mode):
        self._usage.set({})
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
            "stream_options": {"include_usage": True},
            "max_tokens": 2600 if mode == "debate.final_writer" else 2400 if mode == "debate.judge" else 1600,
            "messages": [{"role": "system", "content": SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["quick"])}]
            + [{"role": item["role"], "content": item["content"]} for item in messages],
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        base = settings["base_url"].rstrip("/")
        url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")

        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15, write=30, pool=15)) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                finished = False
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        finished = True
                        break
                    event = json.loads(data)
                    if event.get("usage"):
                        self._usage.set(event["usage"])
                    if not event.get("choices"):
                        continue
                    delta = event["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                    reason = event["choices"][0].get("finish_reason")
                    if reason == "length":
                        raise IncompleteReplyError("模型输出达到长度上限，本次生成未完成，请缩小问题后重试。")
                    if reason == "content_filter":
                        raise IncompleteReplyError("模型服务未完成本次回答，请调整问题后重试。")
                    finished = finished or reason == "stop"
                if not finished:
                    raise IncompleteReplyError("模型连接中断，回答尚未完成，请重试。")

    async def _settings(self):
        if isinstance(self.settings, ModelSettings):
            return await self.settings.read_private()
        return {
            "api_key": "",
            "base_url": DEFAULT_BASE_URL,
            "model": DEFAULT_MODEL,
        }

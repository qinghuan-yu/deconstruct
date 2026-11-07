import asyncio
import contextlib
import re

from .debate_engine import DebateEngine
from .providers import IncompleteReplyError, OpenAICompatibleProvider
from .context import prepare_history
from .streaming import MessageStream


DEFAULT_TITLES = {"New conversation", "新的决策对话", "新对话", "Untitled"}


class ChatJob:
    def __init__(self, task):
        self._task = task

    async def wait(self):
        return await self._task


class ChatEngine:
    def __init__(self, store, provider=None):
        self.store = store
        self.provider = provider or OpenAICompatibleProvider()
        self.active_jobs = {}
        self._message_ids = {}
        self._submission_lock = asyncio.Lock()
        self._cancel_status = {}
        self.job_timeout = 360

    async def submit_user_message(self, conversation_id, content, mode="quick", attachments=None):
        async with self._submission_lock:
            active = self.active_jobs.get(conversation_id)
            if active and not active.done():
                raise ValueError("本会话正在生成，请先停止或等待完成。")
            return await self._submit(conversation_id, content, mode, attachments)

    async def _submit(self, conversation_id, content, mode, attachments):
        if not await self.store.get_conversation(conversation_id):
            raise KeyError("Conversation not found")
        await self._rename_default_conversation(conversation_id, content)
        user_message = await self.store.add_message(conversation_id, "user", content, "complete", mode, attachments)
        await self.store.add_event(
            conversation_id,
            "user_message",
            {"message": {**user_message, "attachments": [
                {key: value for key, value in item.items() if key != "text"} for item in user_message["attachments"]
            ]}},
            message_id=user_message["id"],
        )

        assistant_message = await self.store.add_message(conversation_id, "assistant", "", "streaming", mode)
        await self.store.add_event(
            conversation_id,
            "assistant_message_started",
            {"message": assistant_message},
            message_id=assistant_message["id"],
        )

        task = asyncio.create_task(
            self._run(conversation_id=conversation_id, assistant_message_id=assistant_message["id"], mode=mode)
        )
        self.active_jobs[conversation_id] = task
        self._message_ids[conversation_id] = assistant_message["id"]
        task.add_done_callback(lambda finished: self._job_done(conversation_id, finished))
        return ChatJob(task)

    def _job_done(self, conversation_id, task):
        if self.active_jobs.get(conversation_id) is task:
            self.active_jobs.pop(conversation_id, None)
            self._message_ids.pop(conversation_id, None)
        if not task.cancelled():
            task.exception()

    async def cancel(self, conversation_id, status="cancelled"):
        async with self._submission_lock:
            return await self._cancel_locked(conversation_id, status)

    async def _cancel_locked(self, conversation_id, status):
        task = self.active_jobs.get(conversation_id)
        if not task or task.done():
            return False
        message_id = self._message_ids[conversation_id]
        self._cancel_status[conversation_id] = status
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # 任务尚未进入协程就被取消时，也必须写入终态。
        await self.store.finish_message(conversation_id, message_id, status)
        self._cancel_status.pop(conversation_id, None)
        self.active_jobs.pop(conversation_id, None)
        self._message_ids.pop(conversation_id, None)
        return True

    async def delete_conversation(self, conversation_id):
        async with self._submission_lock:
            await self._cancel_locked(conversation_id, "cancelled")
            return await self.store.delete_conversation(conversation_id)

    async def close(self):
        for conversation_id in list(self.active_jobs):
            await self.cancel(conversation_id, "interrupted")

    async def _run(self, conversation_id, assistant_message_id, mode):
        try:
            async with asyncio.timeout(self.job_timeout):
                history = await self.store.list_messages(conversation_id)
                prompt_history, documents = prepare_history([m for m in history if m["id"] != assistant_message_id])
                if mode == "expert":
                    await DebateEngine(self.store, self.provider).run(conversation_id, assistant_message_id, prompt_history, documents)
                else:
                    if documents:
                        prompt_history = [*prompt_history, {"role": "user", "content": documents}]
                    async with MessageStream(self.store, conversation_id, assistant_message_id) as stream:
                        received = False
                        async for chunk in self.provider.stream_reply(prompt_history, mode):
                            received = received or bool(chunk.strip())
                            await stream.write(chunk)
                        if not received:
                            raise ValueError("模型没有返回正文")
            await self.store.finish_message(conversation_id, assistant_message_id)
        except asyncio.CancelledError:
            status = self._cancel_status.pop(conversation_id, "interrupted")
            await self.store.finish_message(conversation_id, assistant_message_id, status)
        except Exception as exc:
            with contextlib.suppress(Exception):
                # 不把上游响应或凭据拼进面向用户的错误信息。
                import httpx
                while isinstance(exc, BaseExceptionGroup):
                    exc = exc.exceptions[0]
                if isinstance(exc, IncompleteReplyError):
                    error = str(exc)
                elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                    error = "生成超时，请稍后重试，或切换为快速问答。"
                elif isinstance(exc, httpx.HTTPStatusError):
                    error = f"模型服务返回 HTTP {exc.response.status_code}，请检查模型配置或稍后重试。"
                else:
                    error = "生成失败，请检查模型连接后重试。"
                await self.store.finish_message(conversation_id, assistant_message_id, "error", error)

    async def _rename_default_conversation(self, conversation_id, content):
        conversation = await self.store.get_conversation(conversation_id)
        if not conversation or conversation["title"] not in DEFAULT_TITLES:
            return
        title = self._title_from_content(content)
        if title:
            await self.store.update_conversation_title(conversation_id, title)
            await self.store.add_event(
                conversation_id,
                "conversation_renamed",
                {"title": title},
            )

    def _title_from_content(self, content):
        text = re.sub(r"\s+", " ", content).strip()
        text = re.split(r"[。！？!?；;\n]", text, maxsplit=1)[0].strip()
        text = text.strip(" ，,：:、-—")
        return text[:15].strip(" ，,：:、-—") or "新的决策对话"

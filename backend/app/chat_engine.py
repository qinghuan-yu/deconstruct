import asyncio
import contextlib
import re

from .debate_engine import DebateEngine
from .providers import OpenAICompatibleProvider


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

    async def submit_user_message(self, conversation_id, content, mode="quick"):
        await self._rename_default_conversation(conversation_id, content)
        user_message = await self.store.add_message(conversation_id, "user", content, "complete")
        await self.store.add_event(
            conversation_id,
            "user_message",
            {"message": user_message},
            message_id=user_message["id"],
        )

        assistant_message = await self.store.add_message(conversation_id, "assistant", "", "streaming")
        await self.store.add_event(
            conversation_id,
            "assistant_message_started",
            {"message": assistant_message},
            message_id=assistant_message["id"],
        )

        task = asyncio.create_task(
            self._run(conversation_id=conversation_id, assistant_message_id=assistant_message["id"], mode=mode)
        )
        return ChatJob(task)

    async def _run(self, conversation_id, assistant_message_id, mode):
        try:
            history = await self.store.list_messages(conversation_id)
            prompt_history = [
                {"role": item["role"], "content": item["content"]}
                for item in history
                if item["role"] in {"user", "assistant"} and item["id"] != assistant_message_id
            ]
            if mode == "expert":
                await DebateEngine(self.store, self.provider).run(
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_message_id,
                    history=prompt_history,
                )
                return

            async for chunk in self.provider.stream_reply(prompt_history, mode):
                await self.store.append_message(assistant_message_id, chunk)
                await self.store.add_event(
                    conversation_id,
                    "assistant_delta",
                    {"delta": chunk},
                    message_id=assistant_message_id,
                )
            await self.store.update_message_status(assistant_message_id, "complete")
            await self.store.add_event(
                conversation_id,
                "assistant_message_completed",
                {"message_id": assistant_message_id},
                message_id=assistant_message_id,
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self.store.update_message_status(assistant_message_id, "error")
                await self.store.add_event(
                    conversation_id,
                    "assistant_error",
                    {"error": str(exc)},
                    message_id=assistant_message_id,
                )
            raise

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

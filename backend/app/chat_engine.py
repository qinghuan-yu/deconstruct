import asyncio
import contextlib

from .providers import OpenAICompatibleProvider


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

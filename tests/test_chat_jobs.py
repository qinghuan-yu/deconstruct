import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from backend.app.chat_engine import ChatEngine
from backend.app.store import ConversationStore


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def stream_reply(self, messages, mode):
        self.calls.append({"messages": messages, "mode": mode})
        for chunk in ["alpha", " beta", " gamma"]:
            await asyncio.sleep(0)
            yield chunk


class ChatJobsTest(unittest.TestCase):
    def test_background_chat_job_persists_stream_after_client_disconnect(self):
        async def scenario():
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as directory:
                store = ConversationStore(Path(directory) / "chat.sqlite3")
                await store.init()
                engine = ChatEngine(store=store, provider=FakeProvider())

                conversation = await store.create_conversation("Decision Arena")
                job = await engine.submit_user_message(
                    conversation_id=conversation["id"],
                    content="Should I build this?",
                    mode="quick",
                )

                events_before_completion = await store.list_events(conversation["id"], after_seq=0)
                self.assertEqual(
                    [event["type"] for event in events_before_completion][:2],
                    ["user_message", "assistant_message_started"],
                )

                await job.wait()

                messages = await store.list_messages(conversation["id"])
                self.assertEqual(messages[-1]["role"], "assistant")
                self.assertEqual(messages[-1]["content"], "alpha beta gamma")
                self.assertEqual(messages[-1]["status"], "complete")

                resumed_events = await store.list_events(conversation["id"], after_seq=2)
                self.assertTrue(any(event["type"] == "assistant_delta" for event in resumed_events))
                self.assertEqual(resumed_events[-1]["type"], "assistant_message_completed")

        asyncio.run(scenario())

    def test_follow_up_uses_existing_conversation_context_in_quick_mode(self):
        async def scenario():
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as directory:
                store = ConversationStore(Path(directory) / "chat.sqlite3")
                await store.init()
                provider = FakeProvider()
                engine = ChatEngine(store=store, provider=provider)

                conversation = await store.create_conversation("Decision Arena")
                await store.add_message(conversation["id"], "user", "Original constraint: budget is 200.", "complete")
                await store.add_message(conversation["id"], "assistant", "Prior answer: choose the smaller pilot.", "complete")

                job = await engine.submit_user_message(
                    conversation_id=conversation["id"],
                    content="Now include a one-week deadline.",
                    mode="quick",
                )
                await job.wait()

                prompt_history = provider.calls[0]["messages"]
                self.assertEqual([item["role"] for item in prompt_history], ["user", "assistant", "user"])
                self.assertIn("Original constraint", prompt_history[0]["content"])
                self.assertIn("Prior answer", prompt_history[1]["content"])
                self.assertIn("one-week deadline", prompt_history[2]["content"])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

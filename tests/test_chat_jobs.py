import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from backend.app.chat_engine import ChatEngine
from backend.app.store import ConversationStore


class FakeProvider:
    async def stream_reply(self, messages, mode):
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


if __name__ == "__main__":
    unittest.main()

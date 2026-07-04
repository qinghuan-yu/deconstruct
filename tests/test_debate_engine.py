import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from backend.app.chat_engine import ChatEngine
from backend.app.store import ConversationStore


class RecordingProvider:
    def __init__(self):
        self.calls = []

    async def stream_reply(self, messages, mode):
        self.calls.append({"mode": mode, "messages": messages})
        text = f"{mode} output"
        for chunk in [text[: len(text) // 2], text[len(text) // 2 :]]:
            await asyncio.sleep(0)
            yield chunk


class DebateEngineTest(unittest.TestCase):
    def test_expert_mode_runs_multi_agent_debate_and_persists_steps(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                store = ConversationStore(Path(directory) / "chat.sqlite3")
                await store.init()
                provider = RecordingProvider()
                engine = ChatEngine(store=store, provider=provider)

                conversation = await store.create_conversation("Decision Arena")
                job = await engine.submit_user_message(
                    conversation_id=conversation["id"],
                    content="Should I build a multi-agent decision tool?",
                    mode="expert",
                )
                await job.wait()

                modes = [call["mode"] for call in provider.calls]
                self.assertEqual(
                    modes,
                    [
                        "debate.clarifier",
                        "agent.supporter",
                        "agent.opponent",
                        "agent.realist",
                        "agent.long_termist",
                        "agent.emotion_observer",
                        "agent.technical_reviewer",
                        "debate.cross_examination",
                        "debate.scoring_matrix",
                        "debate.judge",
                    ],
                )

                messages = await store.list_messages(conversation["id"])
                assistant = messages[-1]
                self.assertEqual(assistant["role"], "assistant")
                self.assertEqual(assistant["status"], "complete")
                self.assertIn("## 问题澄清", assistant["content"])
                self.assertIn("## 独立 Agent 分析", assistant["content"])
                self.assertIn("### 支持者", assistant["content"])
                self.assertIn("## 交叉质询", assistant["content"])
                self.assertIn("## 评分矩阵", assistant["content"])
                self.assertIn("## 裁判总结", assistant["content"])

                events = await store.list_events(conversation["id"], after_seq=0)
                event_types = [event["type"] for event in events]
                self.assertIn("debate_step_started", event_types)
                self.assertIn("debate_step_completed", event_types)
                self.assertIn("assistant_message_completed", event_types)

        asyncio.run(scenario())

    def test_expert_follow_up_includes_previous_conversation_context(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                store = ConversationStore(Path(directory) / "chat.sqlite3")
                await store.init()
                provider = RecordingProvider()
                engine = ChatEngine(store=store, provider=provider)

                conversation = await store.create_conversation("Decision Arena")
                await store.add_message(conversation["id"], "user", "Should I deploy the advisor?", "complete")
                await store.add_message(
                    conversation["id"],
                    "assistant",
                    "Initial recommendation: deploy only after budget and user constraints are clarified.",
                    "complete",
                )

                job = await engine.submit_user_message(
                    conversation_id=conversation["id"],
                    content="The budget is 200 dollars and I need it this week.",
                    mode="expert",
                )
                await job.wait()

                first_prompt = provider.calls[0]["messages"][0]["content"]
                self.assertIn("Initial recommendation", first_prompt)
                self.assertIn("budget is 200 dollars", first_prompt)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from backend.app.chat_engine import ChatEngine
from backend.app.providers import SYSTEM_PROMPTS
from backend.app.store import ConversationStore


class RecordingProvider:
    def __init__(self):
        self.calls = []

    async def stream_reply(self, messages, mode):
        self.calls.append({"mode": mode, "messages": messages})
        text = "# 决策书：测试\n\nfinal report" if mode == "debate.final_writer" else f"{mode} output"
        for chunk in [text[: len(text) // 2], text[len(text) // 2 :]]:
            await asyncio.sleep(0)
            yield chunk


EXPECTED_AUDIT_MODES = [
    "debate.problem_analyst",
    "debate.red_team",
    "debate.execution_evaluator",
    "debate.evidence_auditor",
    "debate.decision_ledger",
    "debate.judge",
    "debate.final_writer",
]


class DebateEngineTest(unittest.TestCase):
    def test_final_writer_role_is_action_oriented_instead_of_audit_oriented(self):
        prompt = SYSTEM_PROMPTS["debate.final_writer"]

        self.assertIn("行动型决策书", prompt)
        self.assertIn("证据留在内部", prompt)
        self.assertNotIn("可追溯", prompt)

    def test_expert_mode_publishes_audit_process_before_final_decision_document(self):
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

                self.assertEqual([call["mode"] for call in provider.calls], EXPECTED_AUDIT_MODES)

                messages = await store.list_messages(conversation["id"])
                assistant = messages[-1]
                self.assertEqual(assistant["role"], "assistant")
                self.assertEqual(assistant["status"], "complete")
                self.assertIn("<!-- FINAL_DECISION -->", assistant["content"])
                self.assertIn("# 决策书：测试", assistant["content"])
                self.assertLess(
                    assistant["content"].index("debate.problem_analyst output"),
                    assistant["content"].index("<!-- FINAL_DECISION -->"),
                )
                self.assertLess(
                    assistant["content"].index("<!-- FINAL_DECISION -->"),
                    assistant["content"].index("# 决策书：测试"),
                )
                self.assertIn("debate.red_team output", assistant["content"])
                self.assertIn("debate.evidence_auditor output", assistant["content"])
                self.assertIn("debate.judge output", assistant["content"])

                events = await store.list_events(conversation["id"], after_seq=0)
                event_types = [event["type"] for event in events]
                self.assertEqual(event_types.count("assistant_delta"), 2)
                self.assertIn("debate_step_started", event_types)
                self.assertIn("debate_step_completed", event_types)
                self.assertIn("assistant_message_completed", event_types)

        asyncio.run(scenario())

    def test_internal_audit_steps_remain_available_in_events(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                store = ConversationStore(Path(directory) / "chat.sqlite3")
                await store.init()
                provider = RecordingProvider()
                engine = ChatEngine(store=store, provider=provider)

                conversation = await store.create_conversation("Decision Arena")
                job = await engine.submit_user_message(
                    conversation_id=conversation["id"],
                    content="Evaluate a pilot.",
                    mode="expert",
                )
                await job.wait()

                events = await store.list_events(conversation["id"], after_seq=0)
                completed = [event for event in events if event["type"] == "debate_step_completed"]
                self.assertEqual([event["payload"]["mode"] for event in completed], EXPECTED_AUDIT_MODES)
                self.assertTrue(all(event["payload"]["content"] for event in completed))

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

    def test_final_writer_prompt_produces_action_first_document_without_audit_scaffolding(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                store = ConversationStore(Path(directory) / "chat.sqlite3")
                await store.init()
                provider = RecordingProvider()
                engine = ChatEngine(store=store, provider=provider)

                conversation = await store.create_conversation("Decision Arena")
                job = await engine.submit_user_message(
                    conversation_id=conversation["id"],
                    content="Evaluate whether to run a two-week pilot before a full course.",
                    mode="expert",
                )
                await job.wait()

                judge_prompt = provider.calls[-2]["messages"][0]["content"]
                final_prompt = provider.calls[-1]["messages"][0]["content"]
                self.assertIn("仅当证据台账中存在", judge_prompt)
                self.assertIn("禁止为了满足格式虚构反对意见、风险、保留理由或机制", judge_prompt)
                self.assertIn("你现在输出的内容将直接展示给决策者", final_prompt)
                self.assertIn("不展示 Agent 过程", final_prompt)
                self.assertIn("严格按以下结构输出", final_prompt)
                self.assertIn("## 结论", final_prompt)
                self.assertIn("## 方案调整", final_prompt)
                self.assertIn("## 近期行动", final_prompt)
                self.assertIn("## 复盘与调整", final_prompt)
                self.assertIn("## 待补信息", final_prompt)
                self.assertIn("证据只约束判断，不占据正文", final_prompt)
                self.assertIn("只有信息不足确实阻止下一步行动时才使用“暂缓”", final_prompt)
                self.assertIn("方案自动关闭", final_prompt)
                self.assertNotIn("## 7. 可追溯性", final_prompt)
                self.assertNotIn("输出前自检", final_prompt)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

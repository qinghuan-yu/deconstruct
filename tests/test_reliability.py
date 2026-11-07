import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.app.chat_engine import ChatEngine
from backend.app.store import ConversationStore
from backend.app.document_parser import DocumentParseError, extract_document_text


class PausingProvider:
    def __init__(self, pause_mode="quick"):
        self.pause_mode = pause_mode
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def stream_reply(self, messages, mode):
        self.calls.append((mode, messages))
        yield "# 决策书\n\n第一段" if mode == "debate.final_writer" else "阶段结果"
        if mode == self.pause_mode:
            self.started.set()
            await self.release.wait()
        yield "，完成。"


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.directory.name) / "test.sqlite3")
        await self.store.init()
        self.conversation = await self.store.create_conversation("Test")
        self.cid = self.conversation["id"]
        self.provider = PausingProvider()
        self.engine = ChatEngine(self.store, self.provider)
        self.jobs = []

    async def asyncTearDown(self):
        self.provider.release.set()
        for job in self.jobs:
            try:
                await job.wait()
            except (Exception, asyncio.CancelledError):
                pass
        self.directory.cleanup()

    async def submit(self, **kwargs):
        job = await self.engine.submit_user_message(self.cid, "预算 200 元，本周试运行。", **kwargs)
        self.jobs.append(job)
        return job

    async def test_expert_final_is_visible_before_provider_finishes(self):
        self.provider.pause_mode = "debate.final_writer"
        await self.submit(mode="expert")
        await asyncio.wait_for(self.provider.started.wait(), 5)
        await asyncio.sleep(0.25)
        messages = await self.store.list_messages(self.cid)
        self.assertIn("第一段", messages[-1]["content"])
        self.assertEqual(messages[-1]["status"], "streaming")

    async def test_audit_output_does_not_pollute_assistant_answer(self):
        self.provider.pause_mode = "never"
        await (await self.submit(mode="expert")).wait()
        message = (await self.store.list_messages(self.cid))[-1]
        self.assertNotIn("阶段结果", message["content"])
        self.assertEqual(len(message.get("steps", {})), 7)

    async def test_busy_conversation_rejects_second_turn_without_adding_messages(self):
        await self.submit()
        await self.provider.started.wait()
        with self.assertRaisesRegex(ValueError, "生成"):
            await self.submit()
        self.assertEqual(len(await self.store.list_messages(self.cid)), 2)

    async def test_cancel_preserves_partial_reply_and_finishes_job(self):
        await self.submit()
        await self.provider.started.wait()
        await self.engine.cancel(self.cid)
        message = (await self.store.list_messages(self.cid))[-1]
        self.assertEqual(message["status"], "cancelled")
        self.assertIn("阶段结果", message["content"])
        self.assertNotIn(self.cid, self.engine.active_jobs)

    async def test_cancel_immediately_after_submit_does_not_leave_streaming_message(self):
        await self.submit()
        await self.engine.cancel(self.cid)
        self.assertEqual((await self.store.list_messages(self.cid))[-1]["status"], "cancelled")

    async def test_empty_model_response_is_an_error(self):
        class EmptyProvider:
            async def stream_reply(self, messages, mode):
                if False:
                    yield ""
        self.engine.provider = EmptyProvider()
        await (await self.submit()).wait()
        self.assertEqual((await self.store.list_messages(self.cid))[-1]["status"], "error")

    async def test_context_is_bounded_and_document_is_only_sent_to_initial_analyst(self):
        self.provider.pause_mode = "never"
        job = await self.engine.submit_user_message(self.cid, "查预算限制", mode="expert", attachments=[
            {"filename": "长资料.txt", "text": "SOURCE_ONLY " * 10000, "extension": "txt"}
        ])
        self.jobs.append(job)
        await job.wait()
        for index, (_, messages) in enumerate(self.provider.calls):
            prompt = messages[0]["content"]
            self.assertLess(len(prompt), 50000)
            if index == 0:
                self.assertIn("SOURCE_ONLY", prompt)
            else:
                self.assertNotIn("SOURCE_ONLY", prompt)

    async def test_atomic_snapshot_cursor_matches_persisted_deltas(self):
        message = await self.store.add_message(self.cid, "assistant", "", "streaming")
        await self.store.append_delta(self.cid, message["id"], "正文")
        snapshot = await self.store.snapshot(self.cid)
        self.assertEqual(snapshot["messages"][-1]["content"], "正文")
        self.assertEqual(await self.store.list_events(self.cid, snapshot["cursor"]), [])

    async def test_notification_does_not_lose_event_written_before_wait(self):
        revision = self.store.revision
        await self.store.add_event(self.cid, "test", {})
        await asyncio.wait_for(self.store.wait_for_events(revision, timeout=5), .5)

    async def test_parallel_views_finish_before_evidence_audit(self):
        class ParallelProvider:
            def __init__(self):
                self.red_started = asyncio.Event()
                self.execution_started = asyncio.Event()
                self.evidence_prompt = ""

            async def stream_reply(self, messages, mode):
                if mode == "debate.red_team":
                    self.red_started.set()
                    await asyncio.wait_for(self.execution_started.wait(), 1)
                if mode == "debate.execution_evaluator":
                    self.execution_started.set()
                    await asyncio.wait_for(self.red_started.wait(), 1)
                if mode == "debate.evidence_auditor":
                    self.evidence_prompt = messages[0]["content"]
                yield mode + "_OUTPUT"
        provider = ParallelProvider()
        self.engine.provider = provider
        await (await self.submit(mode="expert")).wait()
        self.assertEqual((await self.store.list_messages(self.cid))[-1]["status"], "complete")
        self.assertIn("debate.red_team_OUTPUT", provider.evidence_prompt)
        self.assertIn("debate.execution_evaluator_OUTPUT", provider.evidence_prompt)

    def test_long_history_preserves_initial_constraint_and_original_document(self):
        from backend.app.context import prepare_history, HISTORY_BUDGET
        messages = [{"role": "user", "status": "complete", "content": "INITIAL_CONSTRAINT 200 元", "attachments": []}]
        messages += [{"role": "user", "status": "complete", "content": "附件", "attachments": [{"filename": "原始资料.txt", "text": "ORIGINAL_SOURCE"}]}]
        messages += [{"role": "assistant", "status": "complete", "content": "x" * 6000} for _ in range(20)]
        messages += [{"role": "user", "status": "complete", "content": "最新问题"}]
        history, sources = prepare_history(messages)
        self.assertLessEqual(sum(len(item["content"]) for item in history), HISTORY_BUDGET)
        self.assertIn("INITIAL_CONSTRAINT", str(history))
        self.assertIn("最新问题", str(history))
        self.assertIn("ORIGINAL_SOURCE", sources)

    async def test_job_deadline_turns_hang_into_visible_error(self):
        self.engine.job_timeout = 0.1
        job = await self.submit()
        await asyncio.wait_for(job.wait(), 2)
        message = (await self.store.list_messages(self.cid))[-1]
        self.assertEqual(message["status"], "error")
        self.assertIn("超时", message.get("error", ""))

    async def test_legacy_audit_is_removed_from_follow_up_context(self):
        await self.store.add_message(self.cid, "assistant", "SECRET_AUDIT\n<!-- FINAL_DECISION -->\n预算保留 200 元", "complete")
        self.provider.pause_mode = "never"
        await (await self.submit()).wait()
        context = str(self.provider.calls[0][1])
        self.assertNotIn("SECRET_AUDIT", context)
        self.assertIn("预算保留 200 元", context)

    async def test_startup_recovers_unfinished_messages(self):
        await self.store.add_message(self.cid, "assistant", "未完成", "streaming")
        await self.store.recover_interrupted()
        message = (await self.store.list_messages(self.cid))[-1]
        self.assertEqual(message["status"], "interrupted")

    async def test_structured_attachment_keeps_user_text_literal(self):
        self.provider.pause_mode = "never"
        job = await self.engine.submit_user_message(
            self.cid, "正文里的 [文档：例子] 应保持原样", attachments=[
                {"filename": "计划.txt", "text": "本周预算 200 元", "extension": "txt"}
            ],
        )
        self.jobs.append(job)
        await job.wait()
        message = (await self.store.list_messages(self.cid))[0]
        self.assertEqual(message["content"], "正文里的 [文档：例子] 应保持原样")
        self.assertEqual(message["attachments"][0]["filename"], "计划.txt")


class DocumentLimitsTests(unittest.TestCase):
    def test_docx_rejects_dtd_and_high_compression(self):
        import io
        import zipfile
        for xml in ['<!DOCTYPE x [<!ENTITY y "test">]><x>&y;</x>', '<x>' + 'a' * 100000 + '</x>']:
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", xml)
            with self.assertRaises(DocumentParseError):
                extract_document_text("unsafe.docx", data.getvalue())

    def test_rejects_oversized_upload_before_decoding(self):
        with self.assertRaises(DocumentParseError):
            extract_document_text("large.txt", b"a" * (8 * 1024 * 1024 + 1))

    def test_malformed_xml_returns_document_error(self):
        import io
        import zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("word/document.xml", "<broken>")
        with self.assertRaises(DocumentParseError):
            extract_document_text("broken.docx", data.getvalue())

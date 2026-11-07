import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"DATA_DIR": self.directory.name, "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""})
        self.environment.start()
        import backend.app.main as main
        self.main = importlib.reload(main)
        self.client = TestClient(self.main.app).__enter__()
        self.cid = self.client.post("/api/conversations", json={"title": "契约测试"}).json()["conversation"]["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.environment.stop()
        self.directory.cleanup()

    def test_busy_cancel_and_snapshot_contract(self):
        path = f"/api/conversations/{self.cid}"
        payload = {"content": "评估一个长计划", "mode": "expert"}
        self.assertEqual(self.client.post(path + "/messages", json=payload).status_code, 200)
        self.assertEqual(self.client.post(path + "/messages", json=payload).status_code, 409)
        self.assertTrue(self.client.post(path + "/cancel").json()["cancelled"])
        snapshot = self.client.get(path).json()
        self.assertIsInstance(snapshot["cursor"], int)
        self.assertEqual(snapshot["messages"][-1]["status"], "cancelled")
        self.assertFalse(self.client.post(path + "/cancel").json()["cancelled"])

    def test_attachments_are_structured_and_history_does_not_ship_full_source(self):
        path = f"/api/conversations/{self.cid}"
        payload = {"content": "[文档：这是正文]", "attachments": [{"filename": "预算.txt", "text": "PRIVATE_SOURCE", "extension": "txt"}]}
        self.assertEqual(self.client.post(path + "/messages", json=payload).status_code, 200)
        self.client.post(path + "/cancel")
        snapshot = self.client.get(path).json()
        self.assertEqual(snapshot["messages"][0]["content"], "[文档：这是正文]")
        self.assertEqual(snapshot["messages"][0]["format_version"], 2)
        self.assertNotIn("PRIVATE_SOURCE", str(snapshot))

    def test_invalid_modes_empty_input_and_oversized_documents_are_rejected(self):
        path = f"/api/conversations/{self.cid}/messages"
        for payload in [{"content": ""}, {"content": "test", "mode": "vision"}, {"content": "a" * 16001}]:
            self.assertEqual(self.client.post(path, json=payload).status_code, 422)
        response = self.client.post("/api/documents/parse", files={"file": ("large.txt", b"a" * (8 * 1024 * 1024 + 1))})
        self.assertEqual(response.status_code, 413)

    def test_deleting_active_conversation_cancels_generation(self):
        path = f"/api/conversations/{self.cid}"
        self.client.post(path + "/messages", json={"content": "测试删除", "mode": "expert"})
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertNotIn(self.cid, self.main.engine.active_jobs)

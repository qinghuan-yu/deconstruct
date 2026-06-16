import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


class ApiTest(unittest.TestCase):
    def test_chat_api_accepts_message_and_persists_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["DATA_DIR"] = directory

            from fastapi.testclient import TestClient
            from backend.app.main import app

            with TestClient(app) as client:
                health = client.get("/api/health")
                self.assertEqual(health.status_code, 200)
                self.assertTrue(health.json()["ok"])

                created = client.post("/api/conversations", json={"title": "API Smoke"})
                self.assertEqual(created.status_code, 200)
                conversation_id = created.json()["conversation"]["id"]

                sent = client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={"content": "测试后台生成", "mode": "quick"},
                )
                self.assertEqual(sent.status_code, 200)
                self.assertTrue(sent.json()["accepted"])

                import time

                deadline = time.time() + 5
                messages = []
                while time.time() < deadline:
                    detail = client.get(f"/api/conversations/{conversation_id}")
                    self.assertEqual(detail.status_code, 200)
                    messages = detail.json()["messages"]
                    if len(messages) == 2 and messages[-1]["status"] == "complete":
                        break
                    time.sleep(0.1)

                self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
                self.assertEqual(messages[-1]["status"], "complete")
                self.assertIn("本地演示回复", messages[-1]["content"])

    def test_conversation_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["DATA_DIR"] = directory

            import importlib
            from fastapi.testclient import TestClient
            import backend.app.main as main_module

            main_module = importlib.reload(main_module)

            with TestClient(main_module.app) as client:
                created = client.post("/api/conversations", json={"title": "Delete me"})
                self.assertEqual(created.status_code, 200)
                conversation_id = created.json()["conversation"]["id"]

                deleted = client.delete(f"/api/conversations/{conversation_id}")
                self.assertEqual(deleted.status_code, 200)
                self.assertTrue(deleted.json()["deleted"])

                missing = client.get(f"/api/conversations/{conversation_id}")
                self.assertEqual(missing.status_code, 404)

    def test_default_conversation_title_is_renamed_from_first_message(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["DATA_DIR"] = directory

            import importlib
            from fastapi.testclient import TestClient
            import backend.app.main as main_module

            main_module = importlib.reload(main_module)

            with TestClient(main_module.app) as client:
                created = client.post("/api/conversations", json={"title": "新的决策对话"})
                self.assertEqual(created.status_code, 200)
                conversation_id = created.json()["conversation"]["id"]

                sent = client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={
                        "content": "我应该先部署多智能体辩论框架，还是先准备考试？背景是服务器资源有限。",
                        "mode": "quick",
                    },
                )
                self.assertEqual(sent.status_code, 200)

                conversations = client.get("/api/conversations").json()["conversations"]
                renamed = next(item for item in conversations if item["id"] == conversation_id)
                self.assertEqual(renamed["title"], "我应该先部署多智能体辩论框架")


if __name__ == "__main__":
    unittest.main()

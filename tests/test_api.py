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


if __name__ == "__main__":
    unittest.main()

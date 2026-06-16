import importlib
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


class ModelSettingsTest(unittest.TestCase):
    def test_api_key_can_be_saved_without_returning_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["DATA_DIR"] = directory

            from fastapi.testclient import TestClient
            import backend.app.main as main_module

            main_module = importlib.reload(main_module)

            with TestClient(main_module.app) as client:
                saved = client.post(
                    "/api/settings/model",
                    json={
                        "api_key": "sk-test-secret-123456",
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-chat",
                    },
                )
                self.assertEqual(saved.status_code, 200)
                body = saved.json()
                self.assertTrue(body["configured"])
                self.assertEqual(body["api_key_hint"], "sk-t...3456")
                self.assertNotIn("sk-test-secret-123456", str(body))

                fetched = client.get("/api/settings/model")
                self.assertEqual(fetched.status_code, 200)
                body = fetched.json()
                self.assertTrue(body["configured"])
                self.assertEqual(body["base_url"], "https://api.deepseek.com")
                self.assertEqual(body["model"], "deepseek-chat")
                self.assertNotIn("sk-test-secret-123456", str(body))

    def test_api_key_can_be_saved_from_plain_html_form(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["DATA_DIR"] = directory

            from fastapi.testclient import TestClient
            import backend.app.main as main_module

            main_module = importlib.reload(main_module)

            with TestClient(main_module.app) as client:
                response = client.post(
                    "/settings/model-form",
                    data={
                        "api_key": "sk-form-secret-abcdef",
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-chat",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)

                fetched = client.get("/api/settings/model")
                self.assertTrue(fetched.json()["configured"])
                self.assertEqual(fetched.json()["api_key_hint"], "sk-f...cdef")
                self.assertNotIn("sk-form-secret-abcdef", str(fetched.json()))


if __name__ == "__main__":
    unittest.main()

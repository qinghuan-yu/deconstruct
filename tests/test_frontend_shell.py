from html.parser import HTMLParser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ShellParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class FrontendShellTests(unittest.TestCase):
    def setUp(self):
        self.page = ShellParser()
        self.page.feed((ROOT / "frontend/index.html").read_text(encoding="utf-8"))

    def test_local_styles_and_scripts_are_shipped_with_the_app(self):
        for tag, attrs in self.page.elements:
            path = attrs.get("src") if tag == "script" else attrs.get("href") if tag == "link" else None
            if path and path.startswith("/assets/"):
                self.assertTrue((ROOT / "frontend" / path.removeprefix("/assets/").split("?")[0]).is_file(), path)

    def test_form_controls_have_unique_ids_and_labels(self):
        ids = [attrs["id"] for _, attrs in self.page.elements if "id" in attrs]
        self.assertEqual(len(ids), len(set(ids)))
        labels = {attrs.get("for") for tag, attrs in self.page.elements if tag == "label"}
        for tag, attrs in self.page.elements:
            if tag in {"input", "textarea"}:
                self.assertIn(attrs["id"], labels)

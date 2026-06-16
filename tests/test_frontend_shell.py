from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendShellTests(unittest.TestCase):
    def test_sidebar_does_not_render_history_search(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

        self.assertNotIn("searchInput", html)
        self.assertNotIn("search-box", html)
        self.assertNotIn("searchInput", script)
        self.assertNotIn("search-box", styles)

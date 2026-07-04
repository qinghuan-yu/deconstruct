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

    def test_composer_renders_document_upload_control(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="documentInput"', html)
        self.assertIn('accept=".docx,.doc,.txt,.md"', html)
        self.assertIn('class="paperclip-icon"', html)
        self.assertIn('id="attachmentTray"', html)
        self.assertIn("/api/documents/parse", script)
        self.assertIn("renderAttachments", script)
        self.assertIn("buildOutgoingContent", script)
        self.assertIn(".attachment-card", styles)

    def test_sent_document_messages_render_as_cards_not_full_text(self):
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("splitUserContent", script)
        self.assertIn("renderUserContent", script)
        self.assertIn("renderUserAttachmentCard", script)
        self.assertIn("documentBlocks", script)
        self.assertIn(".user-attachment-card", styles)
        self.assertIn(".user-text", styles)

    def test_markdown_and_scroll_space_handle_long_answers(self):
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("#{1,6}", script)
        self.assertIn("composerOffset", script)
        self.assertIn("setComposerOffset", script)
        self.assertIn("padding-bottom: calc(var(--composer-offset)", styles)

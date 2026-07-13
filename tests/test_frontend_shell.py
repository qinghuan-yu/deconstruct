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
        self.assertIn("width: min(300px, calc(100vw - 48px))", styles)
        self.assertIn("height: 72px", styles)
        self.assertIn("display: flex", styles)

    def test_markdown_and_scroll_space_handle_long_answers(self):
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("#{1,6}", script)
        self.assertIn("<!-- FINAL_DECISION -->", script)
        self.assertIn("finalMarkerIndex", script)
        self.assertIn('"# 决策书："', script)
        self.assertIn("final: content.slice(index).trim()", script)
        self.assertIn("composerOffset", script)
        self.assertIn("setComposerOffset", script)
        self.assertIn("padding-bottom: calc(var(--composer-offset)", styles)

    def test_model_settings_panel_collapses_when_key_is_configured(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="modelSettings"', html)
        self.assertIn('id="settingsForm"', html)
        self.assertIn('id="apiKeyInput"', html)
        self.assertIn('id="baseUrlInput"', html)
        self.assertIn('id="modelInput"', html)
        self.assertIn('id="settingsStatus"', html)
        self.assertNotIn('<details class="settings-inline" open>', html)
        self.assertIn("syncModelSettingsPanel", script)
        self.assertIn("settings.configured", script)
        self.assertIn("els.modelSettings.open = !settings.configured", script)
        self.assertIn("setValueIfPresent", script)
        self.assertIn("setTextIfPresent", script)

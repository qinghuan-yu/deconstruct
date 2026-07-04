import io
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


def make_docx(text):
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        "</w:body>"
        "</w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


class DocumentUploadTest(unittest.TestCase):
    def test_extracts_text_from_supported_document_formats(self):
        from backend.app.document_parser import extract_document_text

        cases = [
            ("notes.txt", b"plain text decision context", "plain text decision context"),
            ("brief.md", "# Plan\n\n- markdown context", "markdown context"),
            ("legacy.doc", b"\x00Legacy Word context\x00\x00More details", "Legacy Word context"),
            ("report.docx", make_docx("docx decision context"), "docx decision context"),
        ]
        for filename, content, expected in cases:
            with self.subTest(filename=filename):
                parsed = extract_document_text(filename, content)
                self.assertIn(expected, parsed["text"])
                self.assertEqual(parsed["filename"], filename)

    def test_upload_document_api_returns_extracted_text(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["DATA_DIR"] = directory

            import importlib
            from fastapi.testclient import TestClient
            import backend.app.main as main_module

            main_module = importlib.reload(main_module)

            with TestClient(main_module.app) as client:
                response = client.post(
                    "/api/documents/parse",
                    files={
                        "file": (
                            "context.md",
                            b"# Context\n\nThe user can deploy this week.",
                            "text/markdown",
                        )
                    },
                )

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["filename"], "context.md")
            self.assertIn("deploy this week", body["text"])

import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree


SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".doc"}
MAX_EXTRACTED_CHARS = 120_000


class DocumentParseError(ValueError):
    pass


def extract_document_text(filename, content):
    safe_name = Path(filename or "document").name
    extension = Path(safe_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise DocumentParseError(f"Unsupported document format: {extension or 'unknown'}. Supported: {supported}")

    if extension in {".txt", ".md"}:
        text = _decode_text(content)
    elif extension == ".docx":
        text = _extract_docx_text(content)
    else:
        text = _extract_legacy_doc_text(content)

    text = _normalize_text(text)
    if not text:
        raise DocumentParseError("No readable text found in document")

    return {
        "filename": safe_name,
        "extension": extension.lstrip("."),
        "text": text[:MAX_EXTRACTED_CHARS],
        "truncated": len(text) > MAX_EXTRACTED_CHARS,
    }


def _decode_text(content):
    if isinstance(content, str):
        return content
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _extract_docx_text(content):
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise DocumentParseError("Invalid docx document") from exc

    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{namespace}tab":
                parts.append("\t")
            elif node.tag == f"{namespace}br":
                parts.append("\n")
        if parts:
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _extract_legacy_doc_text(content):
    decoded_candidates = [_decode_text(content)]
    try:
        decoded_candidates.append(content.decode("utf-16le", errors="ignore"))
    except UnicodeDecodeError:
        pass

    chunks = []
    for decoded in decoded_candidates:
        chunks.extend(re.findall(r"[\w\u4e00-\u9fff][\w\s\u4e00-\u9fff，。！？、；：,.!?;:'\"()（）\-]{3,}", decoded))
    return "\n".join(dict.fromkeys(chunk.strip() for chunk in chunks if chunk.strip()))


def _normalize_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

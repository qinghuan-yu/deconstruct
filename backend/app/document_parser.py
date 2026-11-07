import re
import zipfile
from io import BytesIO
from pathlib import Path
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError


SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".doc"}
MAX_EXTRACTED_CHARS = 120_000
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024


class DocumentParseError(ValueError):
    pass


def extract_document_text(filename, content):
    if len(content.encode("utf-8") if isinstance(content, str) else content) > MAX_UPLOAD_BYTES:
        raise DocumentParseError("文件不能超过 8 MB。")
    safe_name = Path((filename or "document").replace("\\", "/")).name
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
        "warning": "旧版 DOC 仅能尝试提取文字，建议转换为 DOCX 后上传。" if extension == ".doc" else "",
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
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_XML_BYTES or info.file_size / max(info.compress_size, 1) > 200:
                raise DocumentParseError("DOCX 解压后过大或压缩比异常。")
            if len(archive.infolist()) > 2000 or sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
                raise DocumentParseError("DOCX 内容过大。")
            xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise DocumentParseError("Invalid docx document") from exc

    try:
        root = ElementTree.fromstring(xml, forbid_dtd=True)
    except (ParseError, DefusedXmlException) as exc:
        raise DocumentParseError("DOCX XML 无效或包含不支持的实体声明。") from exc
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

from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Any

import fitz
import httpx
import pdfplumber
from docx import Document

from app.core.config import get_settings


class ResumeParsingError(RuntimeError):
    pass


def _normalize_resume_link(link: str) -> str:
    parsed = urlparse(link)
    if parsed.netloc not in {"drive.google.com", "www.drive.google.com"}:
        return link
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "file" and parts[1] == "d":
        return f"https://drive.google.com/uc?export=download&id={parts[2]}"
    file_ids = parse_qs(parsed.query).get("id")
    if file_ids:
        return f"https://drive.google.com/uc?export=download&id={file_ids[0]}"
    return link


async def fetch_resume_bytes(link: str) -> tuple[bytes, str | None]:
    settings = get_settings()
    async with httpx.AsyncClient(follow_redirects=True, timeout=settings.resume_download_timeout_seconds) as client:
        response = await client.get(_normalize_resume_link(link))
        response.raise_for_status()
        content_type = response.headers.get("content-type")
        return response.content, content_type


def extract_text_from_bytes(content: bytes, file_name: str | None, mime_type: str | None) -> tuple[str, dict[str, Any]]:
    suffix = Path(file_name or "").suffix.lower()
    diagnostics: dict[str, Any] = {"parser": None, "fallback_used": False}
    normalized_mime = (mime_type or "").lower()
    if "pdf" in normalized_mime or suffix == ".pdf" or content.startswith(b"%PDF"):
        return _extract_pdf(content, diagnostics)
    if "word" in normalized_mime or "officedocument" in normalized_mime or suffix == ".docx" or content.startswith(b"PK\x03\x04"):
        return _extract_docx(content, diagnostics)
    raise ResumeParsingError(f"Unsupported resume type: {mime_type or suffix or 'unknown'}")


def _extract_pdf(content: bytes, diagnostics: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    diagnostics["parser"] = "pymupdf"
    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            page_count = doc.page_count
            text = "\n".join(page.get_text("text") for page in doc).strip()
        if text:
            diagnostics["page_count"] = page_count
            return text, diagnostics
    except Exception as exc:
        diagnostics["pymupdf_error"] = str(exc)

    diagnostics["fallback_used"] = True
    diagnostics["parser"] = "pdfplumber"
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
        if text:
            diagnostics["page_count"] = page_count
            return text, diagnostics
    except Exception as exc:
        diagnostics["pdfplumber_error"] = str(exc)

    diagnostics["ocr_required"] = True
    raise ResumeParsingError("PDF text extraction failed; OCR is required for this resume")


def _extract_docx(content: bytes, diagnostics: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    diagnostics["parser"] = "python-docx"
    document = Document(BytesIO(content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
    if not text:
        raise ResumeParsingError("DOCX contained no extractable text")
    return text, diagnostics

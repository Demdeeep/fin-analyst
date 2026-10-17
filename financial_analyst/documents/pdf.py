"""Text extraction from uploaded PDFs, preserving page numbers for citations."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO


class PDFExtractionError(ValueError):
    """A PDF could not be safely parsed into searchable page text."""


@dataclass(frozen=True)
class PDFPage:
    number: int
    text: str


@dataclass(frozen=True)
class PDFDocument:
    pages: tuple[PDFPage, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text_characters(self) -> int:
        return sum(len(page.text) for page in self.pages)


def extract_pdf(data: bytes, *, max_bytes: int = 25_000_000) -> PDFDocument:
    """Extract searchable text with pypdf; this does not perform OCR."""
    if not data:
        raise PDFExtractionError("The uploaded PDF is empty.")
    if len(data) > max_bytes:
        raise PDFExtractionError("PDFs must be 25 MB or smaller.")
    if not data.startswith(b"%PDF-"):
        raise PDFExtractionError("This file does not appear to be a valid PDF.")

    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise PDFExtractionError(
            "PDF support is not installed. Run: python -m pip install -e '.[pdf]'"
        ) from error

    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise PDFExtractionError("Password-protected PDFs are not supported yet.")
        pages = tuple(
            PDFPage(number=index, text=(page.extract_text() or "").strip())
            for index, page in enumerate(reader.pages, start=1)
        )
    except PDFExtractionError:
        raise
    except Exception as error:
        raise PDFExtractionError("Could not read this PDF.") from error

    if not pages:
        raise PDFExtractionError("This PDF has no pages.")
    if not any(page.text for page in pages):
        raise PDFExtractionError(
            "No selectable text was found. This may be a scanned PDF; OCR is not included yet."
        )
    return PDFDocument(pages=pages)

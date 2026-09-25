from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from .geography import PAGE_PLACEHOLDER, serialize_geography
from .ocr_pages import apply_page_ocr, page_needs_ocr, pdf_needs_ocr
from .torch_guard import apply_torch_windows_workaround

MIN_NATIVE_CHARS = 15
STAMP_MAX_CHARS = 300

_IMAGE_COMMENT = re.compile(r"<!--\s*image\s*-->", re.IGNORECASE)
_MULTI_BLANK = re.compile(r"\n{3,}")

_converter = None

__all__ = [
    "MIN_NATIVE_CHARS",
    "STAMP_MAX_CHARS",
    "clean_docling_markdown",
    "extract_pdf_with_docling",
    "page_needs_ocr",
    "pdf_needs_ocr",
]


def clean_docling_markdown(text: str) -> str:
    cleaned = _IMAGE_COMMENT.sub("", text)
    cleaned = _MULTI_BLANK.sub("\n\n", cleaned)
    return cleaned.strip() + "\n"


def extract_pdf_with_docling(path: str | Path) -> tuple[str, int, bool]:
    """Devolve markdown, numero de paginas e se alguma pagina imagem foi lida por OCR."""

    source = Path(path)
    converter = _get_converter()
    result = _convert(converter, source)
    markdown = clean_docling_markdown(
        result.document.export_to_markdown(page_break_placeholder=PAGE_PLACEHOLDER)
    )
    markdown = serialize_geography(markdown)
    filled = apply_page_ocr(source, markdown)
    page_count = len(result.document.pages) or 1
    return filled.markdown, page_count, filled.pages_read > 0


def _convert(converter, source: Path):
    """docling-parse (C++) nao abre caminho com acento no Windows; converte por copia ASCII."""

    if str(source).isascii():
        return converter.convert(source)
    with tempfile.TemporaryDirectory(prefix="anonimizador_") as tmp:
        safe = Path(tmp) / f"documento{source.suffix.lower()}"
        shutil.copy2(source, safe)
        return converter.convert(safe)


def _get_converter():
    """Docling so no texto nativo. Pagina-imagem e OCR seletivo em ocr_pages."""

    global _converter
    apply_torch_windows_workaround()
    if _converter is not None:
        return _converter

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = True
    options.do_code_enrichment = False
    options.do_formula_enrichment = False
    options.document_timeout = 900

    _converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        }
    )
    return _converter

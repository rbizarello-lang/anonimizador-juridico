"""Extracao rapida e fiel: PyMuPDF por pagina, ancora de pagina e marcadores do PDF.

O texto nativo sai como esta no PDF, sem reescrita nem Markdown lossy, em
milissegundos por pagina (o Docling leva segundos por pagina em CPU). Pagina
que e so imagem passa pelo OCR seletivo ja existente (``ocr_pages``).

Geografia sem regex no corpo: os marcadores (bookmarks) do PDF, quando
existem, viram titulo no inicio da pagina de destino. Mencao a "evento 12" no
meio de uma frase continua no meio da frase.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from .extractors import ExtractedDocument, UnsupportedDocumentError, _read_text_file

FAST_EXTRACTOR_VERSION = "pymupdf-rapido|1"
_LOG = logging.getLogger(__name__)


def extract_fast(path: str | Path) -> ExtractedDocument:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(source)
    if suffix == ".docx":
        return ExtractedDocument(
            markdown=docx_to_markdown(source),
            page_count=1,
            extractor="python-docx",
        )
    if suffix in {".txt", ".md"}:
        return ExtractedDocument(
            markdown=_read_text_file(source) + "\n",
            page_count=1,
            extractor="txt",
        )
    raise UnsupportedDocumentError(
        f"Formato nao suportado: {suffix or '(sem extensao)'}. Use PDF, DOCX, TXT ou MD."
    )


def _extract_pdf(source: Path) -> ExtractedDocument:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise RuntimeError("PyMuPDF nao esta instalado") from exc

    parts: list[str] = []
    with pymupdf.open(source) as document:
        page_count = document.page_count
        headings = _bookmarks_by_page(document.get_toc(simple=True), page_count)
        for number, page in enumerate(document, start=1):
            parts.append(f"<!-- pagina-origem: {number} -->\n")
            for level, title in headings.get(number, ()):
                parts.append(f"{'#' * min(level + 1, 6)} {title}\n\n")
            body = page.get_text("text").replace("\r\n", "\n").strip()
            if body:
                parts.append(body + "\n\n")
    markdown = "".join(parts)
    ocr_used = False
    try:
        from .ocr_pages import apply_page_ocr

        filled = apply_page_ocr(source, markdown)
        markdown = filled.markdown
        ocr_used = filled.pages_read > 0
    except Exception:  # noqa: BLE001 - OCR e opcional; a auditoria acusa a falta
        _LOG.exception("OCR seletivo falhou em %s", source.name)
    return ExtractedDocument(
        markdown=markdown,
        page_count=page_count,
        extractor="pymupdf-ocr" if ocr_used else "pymupdf",
        ocr_used=ocr_used,
    )


def _bookmarks_by_page(toc: list, page_count: int) -> dict[int, list[tuple[int, str]]]:
    headings: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for item in toc or ():
        if len(item) < 3:
            continue
        level, title, page = int(item[0]), " ".join(str(item[1]).split()), int(item[2])
        if title and 1 <= page <= page_count:
            headings[page].append((max(1, level), title))
    return headings


def docx_to_markdown(path: str | Path) -> str:
    """Paragrafos e tabelas na ordem do documento; estilo Titulo vira ``#``.

    O leitor antigo punha todas as tabelas no fim do texto, longe do
    paragrafo que as cita.
    """

    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise RuntimeError("python-docx nao esta instalado") from exc

    document = Document(str(path))
    chunks: list[str] = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            level = _heading_level(paragraph.style.name if paragraph.style is not None else "")
            chunks.append(f"{'#' * level} {text}" if level else text)
        elif tag == "tbl":
            rendered = _table_markdown(Table(child, document))
            if rendered:
                chunks.append(rendered)
    return "\n\n".join(chunks).rstrip() + "\n"


def _heading_level(style_name: str) -> int:
    name = style_name.casefold()
    for prefix in ("heading", "titulo", "título"):
        if name.startswith(prefix):
            digits = "".join(char for char in name if char.isdigit())
            return min(int(digits), 6) if digits else 1
    return 0


def _table_markdown(table) -> str:
    rows = []
    for row in table.rows:
        cells = [" ".join(cell.text.split()).replace("|", "/") for cell in row.cells]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)

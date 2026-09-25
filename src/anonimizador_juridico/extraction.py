from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    number: int
    text: str


def extract_pdf_pages(
    path: str | Path,
    *,
    sort_reading_order: bool = False,
) -> list[ExtractedPage]:
    """Extrai paginas em memoria, preservando a fronteira fisica do PDF.

    A ordenacao geometrica fica desligada por padrao. No PDF legado, o modo
    ordenado acrescentou muito espaco e perdeu caracteres nao brancos em dez
    paginas. A comparacao por corpus decidira a estrategia definitiva.
    """

    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise RuntimeError("PyMuPDF nao esta instalado") from exc

    source = Path(path)
    pages: list[ExtractedPage] = []
    with pymupdf.open(source) as document:
        for index, page in enumerate(document, start=1):
            text = page.get_text("text", sort=sort_reading_order).replace("\r\n", "\n")
            pages.append(ExtractedPage(number=index, text=text.rstrip()))
    return pages


def pages_to_markdown(pages: list[ExtractedPage]) -> str:
    """Serializa paginas sem inserir o nome potencialmente sensivel do arquivo."""

    chunks = ["# DOCUMENTO EXTRAIDO", ""]
    for page in pages:
        chunks.extend(
            [
                f"<!-- pagina-origem: {page.number} -->",
                f"## Pagina {page.number}",
                "",
                page.text,
                "",
            ]
        )
    return "\n".join(chunks).rstrip() + "\n"

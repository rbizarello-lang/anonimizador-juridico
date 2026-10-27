"""Cobertura da extracao: quantas folhas vieram do texto nativo, quantas do OCR e quais nao foram lidas.

Viaja no proprio Markdown (comentario no topo), como ``ocr-paginas``: sobrevive
ao cache e a troca por tokens, e a AUDITORIA e a trava de exportacao leem dali.
Folha nao lida fica marcada no lugar dela; nunca some em silencio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNREAD_MARK = "[PÁGINA NÃO LIDA — conferir no PDF original]"

_COMMENT = re.compile(r"<!--\s*cobertura:\s*(.*?)\s*-->")
_UNREAD_PAGE = re.compile(r"<!--\s*pagina-nao-lida:\s*([\w-]+)\s*-->")


@dataclass(frozen=True, slots=True)
class Coverage:
    pages: int
    native: int
    scanned: int = 0
    vector: int = 0
    broken_font: int = 0
    blank: int = 0
    unread: tuple[int, ...] = ()
    # H9-k: pouco texto nativo e o corpo em imagem (certidao de matricula): o OCR acrescenta o corpo.
    hybrid: int = 0
    # v0.4.12: imagens embutidas em folha de texto (>= 5% da folha) lidas por OCR, recortadas. A folha segue nativa.
    embedded: int = 0
    embedded_pages: int = 0
    embedded_empty: int = 0
    embedded_failed: int = 0

    @property
    def ocr(self) -> int:
        return self.scanned + self.vector + self.broken_font + self.hybrid

    def comment(self) -> str:
        unread = " ".join(str(number) for number in self.unread) or "nenhuma"
        return (
            f"<!-- cobertura: paginas={self.pages}; nativas={self.native}; "
            f"escaneadas={self.scanned}; vetoriais={self.vector}; "
            f"fonte-quebrada={self.broken_font}; em-branco={self.blank}; "
            + (f"hibridas={self.hybrid}; " if self.hybrid else "")
            + (
                f"recortes={self.embedded}; recortes-folhas={self.embedded_pages}; "
                f"recortes-sem-texto={self.embedded_empty}; recortes-falhos={self.embedded_failed}; "
                if self.embedded or self.embedded_empty or self.embedded_failed
                else ""
            )
            + f"nao-lidas={unread} -->"
        )


def unread_marker(reason: str) -> str:
    return f"<!-- pagina-nao-lida: {reason} -->\n{UNREAD_MARK}"


def parse_coverage(markdown: str) -> Coverage | None:
    match = _COMMENT.search(markdown[:2000])
    if not match:
        return None
    fields: dict[str, str] = {}
    for item in match.group(1).split(";"):
        key, _, value = item.partition("=")
        fields[key.strip()] = value.strip()

    def number(key: str) -> int:
        value = fields.get(key, "0")
        return int(value) if value.isdigit() else 0

    unread = tuple(int(item) for item in fields.get("nao-lidas", "").split() if item.isdigit())
    return Coverage(
        pages=number("paginas"),
        native=number("nativas"),
        scanned=number("escaneadas"),
        vector=number("vetoriais"),
        broken_font=number("fonte-quebrada"),
        blank=number("em-branco"),
        unread=unread,
        hybrid=number("hibridas"),
        embedded=number("recortes"),
        embedded_pages=number("recortes-folhas"),
        embedded_empty=number("recortes-sem-texto"),
        embedded_failed=number("recortes-falhos"),
    )


def unread_pages(markdown: str) -> tuple[int, ...]:
    coverage = parse_coverage(markdown)
    return coverage.unread if coverage is not None else ()


def pages_label(pages: tuple[int, ...] | list[int], limit: int = 20) -> str:
    listed = ", ".join(str(page) for page in list(pages)[:limit])
    return listed + ("..." if len(pages) > limit else "")


def unread_reasons(markdown: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in _UNREAD_PAGE.finditer(markdown):
        counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts

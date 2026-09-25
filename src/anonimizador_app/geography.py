"""Geografia do processo no Markdown: pagina e evento descolado, sem motor novo."""

from __future__ import annotations

import re
import unicodedata

PAGE_PLACEHOLDER = "<!-- pagina-origem -->"
_PAGE_NUMBERED = re.compile(r"<!--\s*pagina-origem:\s*\d+\s*-->")
_GLUED_EVENT = re.compile(
    r"(?<=\S)(\s+)((?:#{1,6}\s*)?(?:Evento|EVENTO)\s+(?:N[ºo°]\.?\s*)?\d+\b)",
    re.IGNORECASE,
)


def serialize_geography(markdown: str) -> str:
    """Numera quebras de pagina e separa eventos colados no meio do paragrafo."""

    text = _number_page_breaks(markdown)
    text = _split_glued_events(text)
    text = _mark_piece_blocks(text)
    return text


def fold_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _number_page_breaks(text: str) -> str:
    if _PAGE_NUMBERED.search(text):
        return text
    if PAGE_PLACEHOLDER not in text:
        return text
    parts = text.split(PAGE_PLACEHOLDER)
    chunks: list[str] = []
    for index, part in enumerate(parts, start=1):
        chunks.append(f"<!-- pagina-origem: {index} -->\n")
        chunks.append(part.lstrip("\n"))
    return "".join(chunks)


def _split_glued_events(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        raw = match.group(2)
        number = re.search(r"\d+", raw)
        label = f"Evento {number.group(0)}" if number else raw.strip()
        return f"\n\n## {label}\n\n"

    return _GLUED_EVENT.sub(replacer, text)


def _mark_piece_blocks(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        core = line.strip()
        if not core or core.startswith("<!--") or core.startswith("#"):
            lines.append(line)
            continue
        if fold_key(core).startswith("pagina de separac"):
            lines.append(f"<!-- bloco-peca -->\n## {core}\n")
            continue
        lines.append(line)
    return "".join(lines)

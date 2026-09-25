"""Geografia do processo no Markdown: pagina e evento descolado, sem motor novo."""

from __future__ import annotations

import re
import unicodedata

PAGE_PLACEHOLDER = "<!-- pagina-origem -->"
_PAGE_NUMBERED = re.compile(r"<!--\s*pagina-origem:\s*\d+\s*-->")
# "Evento"/"EVENTO" com inicial maiuscula: "no evento 12" em minusculas e citacao.
_GLUED_EVENT = re.compile(
    r"(?<=\S)(\s+)((?:#{1,6}\s*)?(?:Evento|EVENTO)\s+(?:[Nn][ºo°]\.?\s*)?\d+\b)"
)
# Palavra logo antes que faz de "Evento N" citacao dentro da frase.
_CITING_WORDS = frozenset(
    {
        "a", "o", "as", "os", "ao", "aos", "à", "às", "de", "do", "da", "dos", "das",
        "em", "no", "na", "nos", "nas", "pelo", "pela", "pelos", "pelas", "e", "ou",
        "conforme", "vide", "cf", "cf.", "ver", "veja", "segundo", "desde", "até",
        "ate", "após", "apos", "entre", "com", "sem", "sob", "este", "esse", "aquele",
        "neste", "nesse", "naquele", "deste", "desse", "daquele", "mesmo", "fl.",
        "fls.", "p.", "pág.", "pag.", "art.", "n.", "v.", "ev.",
    }
)
_SENTENCE_END = (".", "!", "?", ";", ":", "-->")
# Depois do numero: travessao/dois-pontos de cabecalho, ou fim da linha.
_HEADER_TAIL = re.compile(r"[ \t]*(?:[-–—:]|\r?\n|$)")
_LOOKBACK = 200


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
        if not _is_event_header(text, match):
            return match.group(0)
        raw = match.group(2)
        number = re.search(r"\d+", raw)
        label = f"Evento {number.group(0)}" if number else raw.strip()
        return f"\n\n## {label}\n\n"

    return _GLUED_EVENT.sub(replacer, text)


def _is_event_header(text: str, match: re.Match[str]) -> bool:
    """Cabecalho de evento colado, nao mencao na frase ("conforme o Evento 12, ...").

    Vira titulo quando ja vem marcado (``## Evento``), quando abre frase ou
    linha depois de ponto, titulo ou ancora, ou quando o numero e seguido de
    travessao/fim de linha sem preposicao antes ("... 10 Evento 13 - DESPACHO").
    """

    if match.group(2).startswith("#"):
        return True
    start = match.start()
    before = text[max(0, start - _LOOKBACK) : start].rstrip()
    last_line = before.rsplit("\n", 1)[-1].strip()
    words = last_line.split()
    previous = words[-1].casefold().lstrip("([\"'“«") if words else ""
    if previous in _CITING_WORDS:
        return False
    if not before or before.endswith(_SENTENCE_END) or last_line.startswith("#"):
        return True
    return bool(_HEADER_TAIL.match(text, match.end()))


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

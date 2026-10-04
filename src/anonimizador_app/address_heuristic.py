"""Heuristica deterministica de logradouro (Ciclo 3).

Varre via+numero em qualquer parte do documento e reconstitui o par
logradouro/numero do formulario da Receita. Nao mexe no GLiNER nem no
limiar 0,75. Rank de overlap igual ao HeaderHeuristic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from anonimizador_juridico import EntitySpan, resolve_overlaps

from .gemini_detector import is_kept_term

RECOGNIZER = "HeuristicaEndereco"
_SCORE = 0.90

# "R" sem ponto so abrevia Rua antes de palavra: "R E G I S T R O" (cabecalho espacado) e
# "R D ATIVA" (celula de tabela) nao sao logradouro.
_VIA = r"(?:Rua|R\.|R\s+(?=[A-ZÁ-Ú]{2})|Avenida|Ave\.?|Av\.|Avn\.|Alameda|Travessa|Pra[cç]a|P[cç]\.)"
_NUMBER_MARK = r"(?:n[º°o.]?\s*|n[uú]mero\s+)?"
# Nome do logradouro: nao atravessa celula de tabela ("|") nem vao de coluna (3+ espacos).
_NAME = r"(?:(?![^\S\n]{3})[^,\n|])"
_NAME_NO_DIGIT = r"(?:(?![^\S\n]{3})[^,\n|\d])"
# Predial com milhar ("2.350") e um numero so: parar no ponto deixava ".350" colado ao token.
_PREDIAL = r"\d{1,5}(?:\.\d{3})?"

_VIA_NUMBER = re.compile(
    r"(?ix)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    r"("
    rf"{_VIA}"
    r"\s*[A-ZÁ-Ú]"
    rf"{_NAME}{{2,80}}?"
    r"\s*(?:[,;.\-–—]\s*)?"
    rf"{_NUMBER_MARK}"
    r"(?<![A-Za-z0-9/])"
    rf"{_PREDIAL}"
    r")\b"
)

_VIA_ONLY = re.compile(
    rf"(?imx)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    r"("
    rf"{_VIA}"
    rf"\s+[A-ZÁ-Ú]{_NAME}{{3,80}}?"
    r")"
    r"\s+n[º°o.]?\s*$"
)

_VIA_EOL = re.compile(
    rf"(?imx)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    rf"({_VIA})"
    r"\s*$"
)

_VIA_PARTIAL = re.compile(
    rf"(?imx)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    r"("
    rf"{_VIA}"
    rf"\s+[A-ZÁ-Ú]{_NAME_NO_DIGIT}{{1,60}}"
    r")"
    r"\s*$"
)

_CONTINUATION_NUMBER = re.compile(
    r"(?ix)"
    r"^\s*"
    r"("
    rf"[A-ZÁ-Ú]{_NAME}{{0,60}}?"
    r"(?:[,;.\-–—]\s*)?"
    rf"{_NUMBER_MARK}"
    r"(?<![A-Za-z0-9/])"
    rf"{_PREDIAL}"
    r")"
    r"\b"
)

_NEXT_NUMBER = re.compile(rf"^\s*({_PREDIAL})\b")

_FORM_LABEL = re.compile(
    r"^(?:Logradouro|Endere[cç]o(?:\s+Completo)?|RUA|AV\.|AVENIDA)\b",
    re.IGNORECASE,
)
_VIA_LINE = re.compile(
    r"^(?:RUA|R\.|R\s+|AV\.|AVENIDA|AVN\.|ALAMEDA|TRAVESSA|PRA[CÇ]A|P[CÇ]\.)\b",
    re.IGNORECASE,
)
_NUMBER_LABEL = re.compile(r"^N[uú]mero\b", re.IGNORECASE)
_NUMBER_MARKED = re.compile(r"^N[º°o.]?\s*[:.]?\s*(\d{1,5})\b", re.IGNORECASE)
_ISOLATED_NUMBER = re.compile(r"^(\d{2,5})\s*$")

_BARE_VIA = re.compile(
    r"^(?:rua|r\.|avenida|av\.|avn\.|alameda|travessa|pra[cç]a|p[cç]\.)\s*$",
    re.IGNORECASE,
)


class AddressHeuristicEngine:
    """Via+numero no corpo do documento e formulario Receita (logradouro / numero)."""

    provider = "address"

    def __init__(self, *, extra_keep: Iterable[str] = ()) -> None:
        self.extra_keep = tuple(extra_keep)

    def detect(self, text: str) -> list[EntitySpan]:
        spans = list(_spans_via_number(text, self.extra_keep))
        spans.extend(_spans_broken_number(text, self.extra_keep))
        spans.extend(_spans_wrapped_via(text, self.extra_keep))
        spans.extend(_spans_receita_form(text, self.extra_keep))
        return resolve_overlaps(spans)

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []


def _ok_span(text: str, start: int, end: int, extra_keep: Iterable[str]) -> bool:
    if end <= start or end > len(text):
        return False
    value = text[start:end]
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) < 8:
        return False
    if _BARE_VIA.match(collapsed):
        return False
    if is_kept_term(collapsed, extra_keep):
        return False
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    if line and is_kept_term(line, extra_keep):
        return False
    return True


def _make_span(start: int, end: int) -> EntitySpan:
    return EntitySpan(
        start=start,
        end=end,
        entity_type="LOCATION",
        score=_SCORE,
        recognizer=RECOGNIZER,
    )


def _spans_via_number(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    found: list[EntitySpan] = []
    for match in _VIA_NUMBER.finditer(text):
        start, end = match.start(1), match.end(1)
        if not _ok_span(text, start, end, extra_keep):
            continue
        found.append(_make_span(start, end))
    return found


def _spans_broken_number(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    """Via termina em n° / nº e o predial esta na linha seguinte (Raposo Tavares)."""

    found: list[EntitySpan] = []
    for match in _VIA_ONLY.finditer(text):
        tail = text[match.end() :]
        number = _NEXT_NUMBER.match(tail)
        if number is None:
            continue
        start = match.start(1)
        end = match.end() + number.end()
        if not _ok_span(text, start, end, extra_keep):
            continue
        found.append(_make_span(start, end))
    return found


_NUMBER_CUE = re.compile(
    r"(?ix)(?:n[º°o.]|n[uú]mero|[,;.\-–—])\s*\d{1,5}\b"
)


def _spans_wrapped_via(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    """Via no fim da linha e nome+numero na seguinte (a Rua / Raposo Tavares n° 281)."""

    found: list[EntitySpan] = []
    for pattern in (_VIA_EOL, _VIA_PARTIAL):
        for match in pattern.finditer(text):
            cont = _CONTINUATION_NUMBER.match(text[match.end() :])
            if cont is None or not _NUMBER_CUE.search(cont.group(1)):
                continue
            start = match.start(1)
            end = match.end() + cont.end(1)
            if not _ok_span(text, start, end, extra_keep):
                continue
            found.append(_make_span(start, end))
    return found


def _iter_lines(text: str) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    cursor = 0
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        rows.append((cursor, cursor + len(body), body))
        cursor += len(raw)
    return rows


def _next_nonempty(
    rows: list[tuple[int, int, str]], start: int, *, limit: int = 8
) -> tuple[int, int, int, str] | None:
    seen = 0
    index = start
    while index < len(rows) and seen < limit:
        line_start, line_end, body = rows[index]
        stripped = body.strip()
        if stripped:
            return index, line_start, line_end, stripped
        seen += 1
        index += 1
    return None


def _looks_like_street(value: str) -> bool:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if _BARE_VIA.match(collapsed):
        return False
    if _VIA_LINE.match(collapsed) and not re.search(r"\d", collapsed):
        return True
    return False


def _spans_receita_form(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    """Logradouro numa linha, numero na seguinte (formulario Receita / QSA)."""

    rows = _iter_lines(text)
    found: list[EntitySpan] = []
    index = 0
    while index < len(rows):
        _line_start, _line_end, body = rows[index]
        stripped = body.strip()
        street_start = street_end = -1
        if _FORM_LABEL.match(stripped) and len(stripped.split()) <= 4:
            nxt = _next_nonempty(rows, index + 1)
            if nxt is not None and _looks_like_street(nxt[3]):
                _sidx, street_start, street_end, _sbody = nxt
        elif _looks_like_street(stripped):
            street_start = _line_start + (len(body) - len(body.lstrip()))
            street_end = _line_end
        if street_start < 0:
            index += 1
            continue
        number_hit = _find_form_number(rows, index + 1)
        if number_hit is None:
            index += 1
            continue
        _nidx, _nstart, number_end = number_hit
        if not _ok_span(text, street_start, number_end, extra_keep):
            index += 1
            continue
        found.append(_make_span(street_start, number_end))
        index = max(index + 1, _nidx + 1)
    return found


def _find_form_number(
    rows: list[tuple[int, int, str]], start: int
) -> tuple[int, int, int] | None:
    seen = 0
    index = start
    expect_isolated = False
    while index < len(rows) and seen < 10:
        line_start, line_end, body = rows[index]
        stripped = body.strip()
        if not stripped:
            index += 1
            seen += 1
            continue
        marked = _NUMBER_MARKED.match(stripped)
        if marked:
            abs_start = line_start + body.find(marked.group(1))
            return index, abs_start, abs_start + len(marked.group(1))
        if _NUMBER_LABEL.match(stripped):
            expect_isolated = True
            index += 1
            seen += 1
            continue
        isolated = _ISOLATED_NUMBER.match(stripped)
        if isolated and expect_isolated:
            abs_start = line_start + body.find(isolated.group(1))
            return index, abs_start, abs_start + len(isolated.group(1))
        if expect_isolated:
            return None
        index += 1
        seen += 1
    return None

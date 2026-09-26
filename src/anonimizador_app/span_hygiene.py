"""Corta spans que engolem ancora HTML, rotulo de campo, paragrafo alheio ou celula vizinha.

Nao e NER. Razao social com UMA quebra de linha continua valida (lista de alvos).
Duas ou mais quebras, comentario HTML, rotulo sozinho ou endereco gigante caem.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from anonimizador_juridico import EntitySpan, resolve_overlaps
from anonimizador_juridico.pseudonymizer import fold_lexical

from .fidelity import FidelityDropStats

_TARGET = "ListaDeAlvos"
_CONTEXTUAL = frozenset({"PERSON", "ORGANIZATION", "LOCATION"})
_MAX_LEN = {
    "PERSON": 80,
    "ORGANIZATION": 120,
    "LOCATION": 90,
}
_FIELD_LABEL = re.compile(
    r"(?i)(?:^|\n)\s*(?:CEP|Conta|Ag[eê]ncia|N[uú]mero|CNPJ|CPF)\s*:"
)
_LABEL_ONLY = re.compile(
    r"(?i)^(CEP|Conta|Ag[eê]ncia|N[uú]mero|CNPJ|CPF)\s*:?\s*$"
)
_GEO_MARKER = re.compile(
    r"(?i)^\s*(?:<!--\s*)?pagina-origem\s*:"
)
_DATE_AFTER_BREAK = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2,4}")
_DATE_IN_VALUE = re.compile(r"/\d{1,2}/\d{2,4}")
# Endereco nao atravessa celula de tabela nem vao de coluna (3+ espacos): o que vem
# depois e outra coluna (valor, data, descricao), nao o logradouro.
_CELL_BREAK = re.compile(r"\||[^\S\n]{3,}")
# Numero que continua depois do fim do span ("Rua X, 2" + ".350"; "... NA 242" + ",12").
_NUMBER_ENDS_VALUE = re.compile(r"\d[.,/-]?\Z")
_NUMBER_AFTER = re.compile(r"[.,/-]?\d")
_LAST_WORD = re.compile(r"[^\s(]*\Z")
_NUMBER_MARK_AT_END = re.compile(r"(?i)(?<![^\s,;])(?:n[º°o]\.?|n\.|n[uú]mero)\Z")
_TRAILING_SEPARATORS = " \t\r\n,;:.-–—("
_BARE_VIA = re.compile(
    r"(?i)^(?:rua|r|avenida|av|avn|ave|alameda|travessa|pra[cç]a|p[cç])\.?$"
)


def sanitize_spans(
    text: str,
    spans: Sequence[EntitySpan],
    *,
    stats: FidelityDropStats | None = None,
) -> list[EntitySpan]:
    """Encolhe ou descarta spans que descaracterizariam a peca."""

    cleaned: list[EntitySpan] = []
    for span in spans:
        clipped = _clip(text, span, stats)
        if clipped is not None:
            cleaned.append(clipped)
    return resolve_overlaps(cleaned)


def _clip(
    text: str,
    span: EntitySpan,
    stats: FidelityDropStats | None,
) -> EntitySpan | None:
    start, end = span.start, span.end
    if start < 0 or end > len(text) or end <= start:
        return None
    value = text[start:end]
    if _LABEL_ONLY.match(value.strip()):
        if stats is not None:
            stats.higiene_rotulo += 1
        return None
    if _GEO_MARKER.match(value.strip()):
        if stats is not None:
            stats.higiene_html += 1
        return None

    html = value.find("<!--")
    if html == 0:
        if stats is not None:
            stats.higiene_html += 1
        return None
    if html > 0:
        end = start + html
        value = text[start:end]
        if stats is not None:
            stats.higiene_html += 1
        if end <= start:
            return None

    label = _FIELD_LABEL.search(value)
    if label and label.start() > 0:
        end = start + label.start()
        value = text[start:end]
        if stats is not None:
            stats.higiene_rotulo += 1
        if end <= start or _LABEL_ONLY.match(value.strip()):
            return None

    if span.entity_type == "LOCATION":
        date = _DATE_IN_VALUE.search(value)
        if date:
            end = start + date.start()
            value = text[start:end]
            if end <= start:
                return None
        break_at = value.find("\n")
        if break_at >= 0 and _DATE_AFTER_BREAK.match(value[break_at + 1 :]):
            end = start + break_at
            value = text[start:end]
            if end <= start:
                return None
        cut = _location_cell_limit(text, start, end)
        if cut != end:
            end = cut
            value = text[start:end]
            if stats is not None:
                stats.higiene_limite += 1
            if not _has_place_name(value):
                return None

    newlines = value.count("\n")
    if span.entity_type in _CONTEXTUAL and newlines >= 2:
        cut = _nth_newline_offset(value, 1)
        if cut is not None:
            end = start + cut
            value = text[start:end]
            if stats is not None:
                stats.higiene_fusao += 1
            if end <= start:
                return None

    max_len = _MAX_LEN.get(span.entity_type)
    if (
        max_len is not None
        and len(value) > max_len
        and span.recognizer != _TARGET
    ):
        end = start + max_len
        while end > start and not text[end - 1].isspace() and text[end - 1] not in ",;":
            end -= 1
        if end <= start:
            end = start + max_len
        value = text[start:end]
        if stats is not None:
            stats.higiene_tamanho += 1
        if end <= start:
            return None

    if span.entity_type == "LOCATION":
        cut = _before_cut_number(text, start, end)
        if cut != end:
            end = cut
            value = text[start:end]
            if stats is not None:
                stats.higiene_limite += 1
            if not _has_place_name(value):
                return None

    if fold_lexical(value) == "":
        return None
    if end == span.end and start == span.start:
        return span
    return replace(span, end=end)


def _rstrip_separators(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in _TRAILING_SEPARATORS:
        end -= 1
    return end


def _location_cell_limit(text: str, start: int, end: int) -> int:
    """Fim do endereco antes da proxima celula/coluna e antes de parentese nao fechado."""

    value = text[start:end]
    cell = _CELL_BREAK.search(value)
    if cell is not None and cell.start() > 0:
        end = _rstrip_separators(text, start, start + cell.start())
        value = text[start:end]
    paren = value.rfind("(")
    if paren > 0 and ")" not in value[paren:]:
        # "Rua X (Lote Q.8" + ".1-A-3": o parentese e a descricao do lote, nao o logradouro.
        end = _rstrip_separators(text, start, start + paren)
    return end


def _before_cut_number(text: str, start: int, end: int) -> int:
    """O token nao termina dentro de numero: recua para antes da palavra que contem o numero."""

    value = text[start:end]
    if not (_NUMBER_ENDS_VALUE.search(value) and _NUMBER_AFTER.match(text, end)):
        return end
    word = _LAST_WORD.search(value)
    end = _rstrip_separators(text, start, start + (word.start() if word else len(value)))
    mark = _NUMBER_MARK_AT_END.search(text[start:end])
    if mark is not None:
        end = _rstrip_separators(text, start, start + mark.start())
    return end


def _has_place_name(value: str) -> bool:
    collapsed = re.sub(r"\s+", " ", value).strip()
    return bool(re.search(r"[^\W\d_]{2}", collapsed)) and not _BARE_VIA.match(collapsed)


def _nth_newline_offset(value: str, keep: int) -> int | None:
    """Offset do newline que deve cortar. keep=0 corta no primeiro; keep=1 no segundo."""

    seen = 0
    for index, char in enumerate(value):
        if char != "\n":
            continue
        if seen == keep:
            return index
        seen += 1
    return None

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Iterable
from itertools import accumulate

from .types import EntitySpan
from .validators import (
    at_most_one_digit_edit,
    is_valid_cnpj,
    is_valid_cpf,
    normalize_cnpj,
    only_digits,
)

_CPF_SEQ = re.compile(r"(?<!\d)(?:\d[.,/\-\s]*){9,12}\d(?!\d)")
_CNPJ_SEQ = re.compile(r"(?<!\d)(?:\d[.,/\-\s]*){12,15}\d(?!\d)")
_CPF_LABEL = re.compile(r"(?i)(?:c\.?\s*p\.?\s*f(?:\s*/\s*mf)?|\bcic\b)")
_CNPJ_LABEL = re.compile(r"(?i)c\.?\s*n\.?\s*p\.?\s*j(?:\s*/\s*mf)?")
_FORMATTED_CNPJ = re.compile(
    r"(?<![0-9A-Z])"
    r"[0-9A-Z]{2}[.,\s]?[0-9A-Z]{3}[.,\s]?[0-9A-Z]{3}"
    r"[/\s]?[0-9A-Z]{4}[.\-\s]?\s*\d{2}"
    r"(?![0-9A-Z])"
)
# Raízes de órgão público — manter alinhado a fidelity.PUBLIC_CNPJ_ROOTS.
_PUBLIC_ROOTS = frozenset(
    {"00394460", "00394411", "00394494", "00360305", "33683111", "29979036"}
)

_PROPAGATOR = "MappedIdPropagator"
_LABEL_WINDOW = 25
_FIRST_DIGIT = re.compile(r"\d")


def propagate_mapped_ids(
    text: str,
    spans: Iterable[EntitySpan],
    mapping: object | None = None,
) -> list[EntitySpan]:
    """Alias OCR de CPF/CNPJ já vistos: mesmos dígitos ou 1 edição, só com rótulo.

    Sem rótulo não entra (P10). ``canonical_key`` aponta ao número canônico
    já mapeado para o mesmo token.
    """

    current = list(spans)
    known_cpf = _known_cpfs(text, current, mapping)
    known_cnpj = _known_cnpjs(text, current, mapping)
    aliased = [_alias_span(text, span, known_cpf, known_cnpj) for span in current]
    extras: list[EntitySpan] = []
    extras.extend(_scan(text, aliased, known_cpf, (10, 12), "BR_CPF", _CPF_SEQ, _CPF_LABEL))
    extras.extend(
        _scan(text, aliased, known_cnpj, (13, 15), "BR_CNPJ", _CNPJ_SEQ, _CNPJ_LABEL)
    )
    extras.extend(_filial_spans(text, aliased, extras, known_cnpj))
    return [*aliased, *extras]


def _records(mapping: object | None) -> tuple:
    records = getattr(mapping, "records", ())
    return tuple(records or ())


def _known_cpfs(text: str, spans: list[EntitySpan], mapping: object | None) -> dict[str, str]:
    known: dict[str, str] = {}
    for record in _records(mapping):
        if getattr(record, "entity_type", "") != "BR_CPF":
            continue
        canonical = str(getattr(record, "canonical", "") or "")
        if len(canonical) == 11 and is_valid_cpf(canonical):
            known[canonical] = canonical
    for span in spans:
        if span.entity_type != "BR_CPF":
            continue
        digits = only_digits(span.value_from(text))
        if len(digits) == 11 and is_valid_cpf(digits):
            known[digits] = digits
    return known


def _known_cnpjs(text: str, spans: list[EntitySpan], mapping: object | None) -> dict[str, str]:
    known: dict[str, str] = {}
    for record in _records(mapping):
        if getattr(record, "entity_type", "") != "BR_CNPJ":
            continue
        canonical = str(getattr(record, "canonical", "") or "")
        if len(canonical) == 14 and is_valid_cnpj(canonical):
            known[canonical] = canonical
    for span in spans:
        if span.entity_type != "BR_CNPJ":
            continue
        compact = normalize_cnpj(span.value_from(text))
        compact = re.sub(r"^(?:CNPJ|MF)+", "", compact)
        if len(compact) == 14 and is_valid_cnpj(compact):
            known[compact] = compact
        digits = only_digits(span.value_from(text))
        if len(digits) == 14 and is_valid_cnpj(digits):
            known[digits] = digits
    return known


def _alias_span(
    text: str,
    span: EntitySpan,
    known_cpf: dict[str, str],
    known_cnpj: dict[str, str],
) -> EntitySpan:
    if span.canonical_key:
        return span
    if span.entity_type == "BR_CPF":
        digits = only_digits(span.value_from(text))
        target = _match_known(digits, known_cpf, text, span.start, require_label=False)
        if target:
            if digits == target or _has_label(text, span.start, _CPF_LABEL):
                return _with_key(span, target)
            return span
    elif span.entity_type == "BR_CNPJ":
        digits = only_digits(span.value_from(text))
        target = _match_known(digits, known_cnpj, text, span.start, require_label=False)
        if not target:
            target = _root_canonical(digits, known_cnpj)
        if target:
            root_key = _root_canonical(digits, known_cnpj) or target
            if digits == target or _has_label(text, span.start, _CNPJ_LABEL) or root_key:
                return _with_key(span, root_key)
            return span
    return span


def _match_known(
    compact: str,
    known: dict[str, str],
    text: str,
    start: int,
    *,
    require_label: bool,
    label_re: re.Pattern[str] | None = None,
) -> str:
    if compact in known:
        return known[compact]
    if require_label and label_re is not None and not _has_label(text, start, label_re):
        return ""
    for candidate in known:
        if at_most_one_digit_edit(compact, candidate):
            return candidate
    return ""


def _number_start(text: str, start: int) -> int:
    """Início do bloco numérico: o rótulo CPF/CNPJ pode estar dentro do span."""

    snippet = text[start : min(len(text), start + 24)]
    match = _FIRST_DIGIT.search(snippet)
    return start + match.start() if match else start


def _has_label(text: str, start: int, label_re: re.Pattern[str]) -> bool:
    number_start = _number_start(text, start)
    window = text[max(0, number_start - _LABEL_WINDOW) : number_start]
    return bool(label_re.search(window))


def _with_key(span: EntitySpan, canonical_key: str) -> EntitySpan:
    return EntitySpan(
        start=span.start,
        end=span.end,
        entity_type=span.entity_type,
        score=max(span.score, 0.95),
        recognizer=span.recognizer,
        canonical_key=canonical_key,
        alvo=span.alvo,
    )


def _covered(start: int, end: int, spans: list[EntitySpan], entity_type: str) -> bool:
    return any(
        span.entity_type == entity_type and span.start < end and start < span.end
        for span in spans
    )


class _Coverage:
    """Mesma pergunta de ``_covered`` em O(log n), para a lista fixa de spans.

    Os spans podem se sobrepor entre si: guarda o maior fim acumulado.
    """

    def __init__(self, spans: Iterable[EntitySpan], entity_type: str) -> None:
        items = sorted(
            (span.start, span.end) for span in spans if span.entity_type == entity_type
        )
        self._starts = [start for start, _end in items]
        self._max_end = list(accumulate((end for _start, end in items), max))

    def covers(self, start: int, end: int) -> bool:
        index = bisect_left(self._starts, end)
        return index > 0 and self._max_end[index - 1] > start


def _scan(
    text: str,
    spans: list[EntitySpan],
    known: dict[str, str],
    width_range: tuple[int, int],
    entity_type: str,
    seq_re: re.Pattern[str],
    label_re: re.Pattern[str],
) -> list[EntitySpan]:
    if not known:
        return []
    low, high = width_range
    coverage = _Coverage(spans, entity_type)
    extras: list[EntitySpan] = []
    for match in seq_re.finditer(text):
        if match.group().count("\n") > 1:
            continue
        compact = only_digits(match.group())
        if not low <= len(compact) <= high:
            continue
        if not _has_label(text, match.start(), label_re):
            continue
        target = _match_known(
            compact, known, text, match.start(), require_label=True, label_re=label_re
        )
        if not target:
            continue
        if coverage.covers(match.start(), match.end()):
            continue
        if _covered(match.start(), match.end(), extras, entity_type):
            continue
        extras.append(
            EntitySpan(
                start=match.start(),
                end=match.end(),
                entity_type=entity_type,
                score=0.95,
                recognizer=_PROPAGATOR,
                canonical_key=target,
            )
        )
    return extras


def _root_canonical(compact: str, known: dict[str, str]) -> str:
    """Uma raiz privada → um token. Filiais 0001/0004 compartilham a chave."""

    if len(compact) < 8:
        return ""
    root = compact[:8]
    if root in _PUBLIC_ROOTS:
        return ""
    members = sorted({item for item in known if len(item) >= 8 and item[:8] == root})
    return members[0] if members else ""


def _filial_spans(
    text: str,
    spans: list[EntitySpan],
    extras: list[EntitySpan],
    known_cnpj: dict[str, str],
) -> list[EntitySpan]:
    """CNPJ de filial da mesma raiz já vista, inclusive com DV de OCR."""

    if not known_cnpj:
        return []
    coverage = _Coverage(spans, "BR_CNPJ")
    found: list[EntitySpan] = []
    for match in _FORMATTED_CNPJ.finditer(text):
        if match.group().count("\n") > 1:
            continue
        compact = normalize_cnpj(match.group())
        compact = re.sub(r"^(?:CNPJ|MF)+", "", compact)
        digits = only_digits(match.group())
        key = compact if len(compact) >= 12 else digits
        target = _root_canonical(key, known_cnpj)
        if not target:
            continue
        if key in known_cnpj or digits in known_cnpj:
            continue
        if coverage.covers(match.start(), match.end()):
            continue
        if _covered(match.start(), match.end(), extras, "BR_CNPJ"):
            continue
        if _covered(match.start(), match.end(), found, "BR_CNPJ"):
            continue
        found.append(
            EntitySpan(
                start=match.start(),
                end=match.end(),
                entity_type="BR_CNPJ",
                score=0.95,
                recognizer=_PROPAGATOR,
                canonical_key=target,
            )
        )
    return found

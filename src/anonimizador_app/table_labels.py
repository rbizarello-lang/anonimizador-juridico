"""Placa, RENAVAM e chassi por cabeçalho de coluna ou campo rotulado (Ciclo 4a).

Cabeçalho de tabela Markdown e rótulo de formulário contam como rótulo (P10).
Regex sem nenhum rótulo continua proibida: UF/AAA9A99 só na coluna de placa.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from anonimizador_juridico import EntitySpan, resolve_overlaps
from anonimizador_juridico.pseudonymizer import canonicalize
from anonimizador_juridico.validators import only_digits

from .settings import default_enabled_tags

RECOGNIZER = "TableLabels"
_SCORE = 0.88

BR_UF = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
BR_UF_SET = frozenset(BR_UF)
_UF_ALT = "|".join(BR_UF)

_PLATE_BODY = rf"[A-Z]{{3}}-?(?:\d{{4}}|\d[A-Z]\d{{2}})"
_PLATE_CELL = re.compile(rf"^(?:(?:{_UF_ALT})/)?{_PLATE_BODY}$", re.IGNORECASE)
_PLATE_BODY_CANON = re.compile(r"^[A-Z]{3}(?:\d{4}|\d[A-Z]\d{2})$")
_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
_RENAVAM_CELL = re.compile(r"^\d{9,11}$")

_HEADER_PLACA = re.compile(r"(?i)(?:uf\s*/\s*placa|\bplaca\b)")
_HEADER_RENAVAM = re.compile(r"(?i)n\.?\s*renavam|\brenavam\b")
_HEADER_CHASSI = re.compile(r"(?i)\bchassis?\b")
_HEADER_CEP = re.compile(r"(?i)\bcep\b")
_HEADER_PHONE = re.compile(r"(?i)\b(?:telefone|fone|celular)\b")

_CEP_CELL = re.compile(r"^\d{2}\.?\d{3}-?\d{3}$")
_PHONE_CELL = re.compile(
    r"^(?:\+?55[\s.\-]?)?(?:\(?[1-9]\d\)?[\s.\-]?)?(?:9\d{4}|[2-9]\d{3})[\s.\-]?\d{4}$"
)

_FIELD_RENAVAM = re.compile(r"(?i)^\s*n\.?\s*renavam\s*:?\s*$|^\s*renavam\s*:?\s*$")
_FIELD_CHASSI = re.compile(r"(?i)^\s*chassis?\s*:?\s*$")
_FIELD_PLACA = re.compile(r"(?i)^\s*(?:uf\s*/\s*)?placa\s*:?\s*$")

_INLINE_RENAVAM = re.compile(
    r"(?i)\b(?:n\.?\s*)?renavam\s*:?\s*(\d{9,11})(?!\d)"
)
_INLINE_CHASSI = re.compile(
    r"(?i)\bchassis?\s*:?\s*([A-HJ-NPR-Z0-9]{17})\b"
)
_INLINE_PLACA = re.compile(
    rf"(?i)\bplaca\s*:?\s*({_PLATE_BODY})\b"
)

_PAGE_BREAK = re.compile(r"(?i)^\s*<!--\s*pagina")
_SEP_CELL = re.compile(r"^:?-{2,}:?$")


class TableLabelsEngine:
    """Células da mesma coluna de um cabeçalho placa/RENAVAM/chassi, e formulário empilhado."""

    provider = "table_labels"

    def __init__(
        self,
        *,
        extra_keep: Iterable[str] = (),
        enabled_tags: dict[str, bool] | None = None,
    ) -> None:
        del extra_keep
        tags = enabled_tags if enabled_tags is not None else default_enabled_tags()
        self.enabled_tags = tags

    def detect(self, text: str) -> list[EntitySpan]:
        spans: list[EntitySpan] = []
        if any(
            self._on(tag)
            for tag in ("BR_PLACA", "BR_RENAVAM", "BR_CHASSI", "BR_CEP", "BR_PHONE")
        ):
            spans.extend(_spans_from_tables(text, self.enabled_tags))
        if self._on("BR_PLACA") or self._on("BR_RENAVAM") or self._on("BR_CHASSI"):
            spans.extend(_spans_from_inline_fields(text, self.enabled_tags))
            spans.extend(_spans_from_stacked_form(text, self.enabled_tags))
        return resolve_overlaps(spans)

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []

    def _on(self, entity_type: str) -> bool:
        return self.enabled_tags.get(entity_type, True)


def plate_canonical(value: str) -> str:
    """Letras+dígitos da placa, sem UF."""

    return canonicalize("BR_PLACA", value)


def _on(enabled: dict[str, bool], entity_type: str) -> bool:
    return enabled.get(entity_type, True)


def _line_records(text: str) -> list[tuple[int, str]]:
    records: list[tuple[int, str]] = []
    start = 0
    for line in text.splitlines(keepends=True):
        records.append((start, line.rstrip("\r\n")))
        start += len(line)
    return records


def _is_separator_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cells = [cell.strip().replace(" ", "") for cell in stripped.strip("|").split("|")]
    return bool(cells) and all(_SEP_CELL.fullmatch(cell) for cell in cells)


def _is_pipe_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _cell_spans(line: str, line_start: int) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    last = 0
    for match in re.finditer(r"\|", line):
        if match.start() > last:
            raw = line[last : match.start()]
            stripped = raw.strip()
            if stripped:
                pad = len(raw) - len(raw.lstrip())
                begin = line_start + last + pad
                found.append((begin, begin + len(stripped), stripped))
        last = match.end()
    if last < len(line):
        raw = line[last:]
        stripped = raw.strip()
        if stripped:
            pad = len(raw) - len(raw.lstrip())
            begin = line_start + last + pad
            found.append((begin, begin + len(stripped), stripped))
    return found


def _header_kinds(cells: list[str]) -> dict[int, str]:
    kinds: dict[int, str] = {}
    for index, cell in enumerate(cells):
        if _HEADER_PLACA.search(cell):
            kinds[index] = "placa"
        elif _HEADER_RENAVAM.search(cell):
            kinds[index] = "renavam"
        elif _HEADER_CHASSI.search(cell):
            kinds[index] = "chassi"
        elif _HEADER_CEP.search(cell):
            kinds[index] = "cep"
        elif _HEADER_PHONE.search(cell):
            kinds[index] = "telefone"
    return kinds


def _emit_cell(
    start: int,
    end: int,
    value: str,
    kind: str,
    enabled: dict[str, bool],
) -> EntitySpan | None:
    compact = value.strip()
    if kind == "placa" and _on(enabled, "BR_PLACA") and _PLATE_CELL.fullmatch(compact):
        return EntitySpan(
            start=start,
            end=end,
            entity_type="BR_PLACA",
            score=_SCORE,
            recognizer=RECOGNIZER,
            canonical_key=plate_canonical(compact),
        )
    if kind == "renavam" and _on(enabled, "BR_RENAVAM") and _RENAVAM_CELL.fullmatch(only_digits(compact) if compact.isdigit() else compact):
        digits = only_digits(compact)
        if 9 <= len(digits) <= 11 and compact.isdigit():
            return EntitySpan(
                start=start,
                end=end,
                entity_type="BR_RENAVAM",
                score=_SCORE,
                recognizer=RECOGNIZER,
                canonical_key=digits,
            )
    if kind == "chassi" and _on(enabled, "BR_CHASSI") and _VIN.fullmatch(compact):
        return EntitySpan(
            start=start,
            end=end,
            entity_type="BR_CHASSI",
            score=_SCORE,
            recognizer=RECOGNIZER,
            canonical_key=compact.upper(),
        )
    if kind == "cep" and _on(enabled, "BR_CEP") and _CEP_CELL.fullmatch(compact):
        return EntitySpan(
            start=start,
            end=end,
            entity_type="BR_CEP",
            score=_SCORE,
            recognizer=RECOGNIZER,
            canonical_key=canonicalize("BR_CEP", compact),
        )
    if kind == "telefone" and _on(enabled, "BR_PHONE") and _PHONE_CELL.fullmatch(compact):
        return EntitySpan(
            start=start,
            end=end,
            entity_type="BR_PHONE",
            score=_SCORE,
            recognizer=RECOGNIZER,
            canonical_key=canonicalize("BR_PHONE", compact),
        )
    return None


def _spans_from_tables(text: str, enabled: dict[str, bool]) -> Iterator[EntitySpan]:
    lines = _line_records(text)
    index = 0
    total = len(lines)
    while index < total - 1:
        _start, header_line = lines[index]
        _sep_start, sep_line = lines[index + 1]
        if _is_pipe_row(header_line) and _is_separator_line(sep_line):
            header_cells = [cell.strip() for cell in header_line.strip().strip("|").split("|")]
            kinds = _header_kinds(header_cells)
            if kinds:
                index += 2
                while index < total:
                    line_start, line = lines[index]
                    stripped = line.strip()
                    if not stripped or _PAGE_BREAK.match(stripped):
                        break
                    if index + 1 < total and _is_pipe_row(line) and _is_separator_line(lines[index + 1][1]):
                        break
                    if _is_pipe_row(line):
                        cells = _cell_spans(line, line_start)
                        for col, kind in kinds.items():
                            if col >= len(cells):
                                continue
                            cell_start, cell_end, value = cells[col]
                            span = _emit_cell(cell_start, cell_end, value, kind, enabled)
                            if span is not None:
                                yield span
                    index += 1
                continue
        index += 1


def _spans_from_inline_fields(text: str, enabled: dict[str, bool]) -> Iterator[EntitySpan]:
    if _on(enabled, "BR_RENAVAM"):
        for match in _INLINE_RENAVAM.finditer(text):
            yield EntitySpan(
                start=match.start(1),
                end=match.end(1),
                entity_type="BR_RENAVAM",
                score=_SCORE,
                recognizer=RECOGNIZER,
                canonical_key=match.group(1),
            )
    if _on(enabled, "BR_CHASSI"):
        for match in _INLINE_CHASSI.finditer(text):
            yield EntitySpan(
                start=match.start(1),
                end=match.end(1),
                entity_type="BR_CHASSI",
                score=_SCORE,
                recognizer=RECOGNIZER,
                canonical_key=match.group(1).upper(),
            )
    if _on(enabled, "BR_PLACA"):
        for match in _INLINE_PLACA.finditer(text):
            yield EntitySpan(
                start=match.start(1),
                end=match.end(1),
                entity_type="BR_PLACA",
                score=_SCORE,
                recognizer=RECOGNIZER,
                canonical_key=plate_canonical(match.group(1)),
            )


def _stacked_kind(line: str) -> str | None:
    stripped = line.strip()
    if _FIELD_RENAVAM.fullmatch(stripped):
        return "renavam"
    if _FIELD_CHASSI.fullmatch(stripped):
        return "chassi"
    if _FIELD_PLACA.fullmatch(stripped):
        return "placa"
    return None


def _next_nonempty(
    lines: list[tuple[int, str]],
    start: int,
    *,
    skip_empty: int = 2,
) -> tuple[int, int, str] | None:
    seen_empty = 0
    index = start
    while index < len(lines):
        line_start, line = lines[index]
        stripped = line.strip()
        if not stripped:
            seen_empty += 1
            if seen_empty > skip_empty:
                return None
            index += 1
            continue
        if _PAGE_BREAK.match(stripped) or _stacked_kind(stripped) is not None:
            return None
        pad = len(line) - len(line.lstrip()) if line.strip() else 0
        value_start = line_start + pad
        return index, value_start, stripped
    return None


def _spans_from_stacked_form(text: str, enabled: dict[str, bool]) -> Iterator[EntitySpan]:
    lines = _line_records(text)
    index = 0
    total = len(lines)
    while index < total:
        kind = _stacked_kind(lines[index][1])
        if kind is None:
            index += 1
            continue
        stack: list[str] = []
        while index < total:
            current = _stacked_kind(lines[index][1])
            if current is None:
                break
            stack.append(current)
            index += 1
        if len(stack) >= 2:
            for kind in stack:
                nxt = _next_nonempty(lines, index, skip_empty=2)
                if nxt is None:
                    break
                value_index, value_start, value = nxt
                span = _emit_cell(value_start, value_start + len(value), value, kind, enabled)
                if span is not None:
                    yield span
                index = value_index + 1
            continue
        if len(stack) == 1:
            nxt = _next_nonempty(lines, index, skip_empty=2)
            if nxt is not None:
                value_index, value_start, value = nxt
                span = _emit_cell(
                    value_start, value_start + len(value), value, stack[0], enabled
                )
                if span is not None:
                    yield span
                index = value_index + 1
            continue
        index += 1

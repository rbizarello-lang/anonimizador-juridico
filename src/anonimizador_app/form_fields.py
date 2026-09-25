"""Formulario rotulado da Receita: nascimento, filiacao e titulo (Ciclo 4b).

Rank de overlap = Header. Rótulo de campo conta (P10). Datas de evento,
contrato, protocolo, julgado em e DJe permanecem em claro.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from anonimizador_juridico import EntitySpan, resolve_overlaps
from anonimizador_juridico.validators import is_valid_cpf, is_valid_titulo_eleitor, only_digits

from .gemini_detector import is_kept_term, is_too_generic
from .settings import default_enabled_tags

RECOGNIZER = "FormFields"
_SCORE_ID = 0.88
_SCORE_PERSON = 0.88
_SKIP_EMPTY = 2

_BIRTH_LABEL = re.compile(
    r"(?i)(?:data\s+de\s+nascimento\b|\bnascimento\s*:|"
    r"nascid[oa]\s+em(?:\s+d+e|\s+data\s+de)?)"
)
_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_BIRTH_FORBIDDEN = re.compile(
    r"(?i)julgado\s+em|\bdje\b|data\s+do\s+contrato|protocolo|"
    r"data\s+de\s+inscri|audi[eê]ncia|\bevento\s+\d|"
    r"publica[cç][aã]o|\bvencimento\b|\bdespacho\b|\bemiss[aã]o\b"
)

_PARENT_LABEL = re.compile(
    r"(?i)(?:nome\s+d[ao]\s+(?:m[aã]e|pai)|filia[cç][aã]o|"
    r"(?<![A-Za-zÀ-ÿ])(?:m[aã]e|pai)\s*:)"
)
_PARENT_REFUSE = frozenset(
    {
        "nao informado",
        "não informado",
        "-",
        "–",
        "—",
        "feminino",
        "masculino",
        "regular",
        "nao",
        "não",
        "sim",
        ".",
    }
)
_PARENT_NEXT_IS_LABEL = re.compile(
    r"(?i)^(?:sexo|cpf|cnpj|nome|data|naturalidade|uf|logradouro|numero|"
    r"n[uú]mero|titulo|t[íi]tulo|nacionalidade|situa[cç][aã]o|telefone|"
    r"estrangeiro|ano|complemento|bairro|munic[ií]pio|cep)\b"
)

_TITULO_LABEL = re.compile(
    r"(?i)t[íi]tulo\s+(?:de\s+)?eleitor(?:a|al)?"
)
_TITULO_DIGITS = re.compile(r"(?<!\d)(\d{10,12})(?!\d)")
_TITULO_FORBIDDEN = re.compile(
    r"(?i)\b(?:nire|protocolo|cpf|cnpj|registro)\b"
)

_PAGE_BREAK = re.compile(r"(?i)^\s*<!--\s*pagina")


class FormFieldsEngine:
    """Campos rotulados de nascimento, filiação e título eleitoral."""

    provider = "form_fields"

    def __init__(
        self,
        *,
        extra_keep: Iterable[str] = (),
        enabled_tags: dict[str, bool] | None = None,
    ) -> None:
        self.extra_keep = tuple(extra_keep)
        self.enabled_tags = enabled_tags if enabled_tags is not None else default_enabled_tags()

    def detect(self, text: str) -> list[EntitySpan]:
        spans: list[EntitySpan] = []
        if self.enabled_tags.get("BR_NASCIMENTO", True):
            spans.extend(_spans_nascimento(text))
        if self.enabled_tags.get("BR_TITULO_ELEITOR", True):
            spans.extend(_spans_titulo(text))
        spans.extend(_spans_filiacao(text, self.extra_keep))
        return resolve_overlaps(spans)

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []


def _line_records(text: str) -> list[tuple[int, str]]:
    records: list[tuple[int, str]] = []
    start = 0
    for line in text.splitlines(keepends=True):
        records.append((start, line.rstrip("\r\n")))
        start += len(line)
    return records


def _line_index_at(lines: list[tuple[int, str]], pos: int) -> int:
    for index, (start, line) in enumerate(lines):
        if start <= pos < start + len(line) + 1:
            return index
    return len(lines) - 1 if lines else 0


def _next_nonempty(
    lines: list[tuple[int, str]],
    start_index: int,
    *,
    skip_empty: int = _SKIP_EMPTY,
) -> tuple[int, str] | None:
    seen_empty = 0
    index = start_index
    while index < len(lines):
        line_start, line = lines[index]
        stripped = line.strip()
        if not stripped:
            seen_empty += 1
            if seen_empty > skip_empty:
                return None
            index += 1
            continue
        if _PAGE_BREAK.match(stripped):
            return None
        pad = len(line) - len(line.lstrip())
        return line_start + pad, stripped
    return None


def _spans_nascimento(text: str) -> list[EntitySpan]:
    lines = _line_records(text)
    spans: list[EntitySpan] = []
    seen: set[tuple[int, int]] = set()
    for match in _BIRTH_LABEL.finditer(text):
        before = text[max(0, match.start() - 48) : match.start()]
        if _BIRTH_FORBIDDEN.search(before) or _BIRTH_FORBIDDEN.search(match.group()):
            continue
        same = text[match.end() : text.find("\n", match.end()) if text.find("\n", match.end()) >= 0 else len(text)]
        date = _DATE.search(same)
        if date is not None:
            start = match.end() + date.start(1)
            end = match.end() + date.end(1)
        else:
            line_idx = _line_index_at(lines, match.start())
            nxt = _next_nonempty(lines, line_idx + 1)
            if nxt is None:
                continue
            value_start, value = nxt
            date = _DATE.search(value)
            if date is None:
                continue
            start = value_start + date.start(1)
            end = value_start + date.end(1)
        window = text[max(0, match.start() - 24) : end]
        if _BIRTH_FORBIDDEN.search(window):
            continue
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        spans.append(
            EntitySpan(
                start=start,
                end=end,
                entity_type="BR_NASCIMENTO",
                score=_SCORE_ID,
                recognizer=RECOGNIZER,
                canonical_key=only_digits(text[start:end]),
            )
        )
    return spans


def _spans_titulo(text: str) -> list[EntitySpan]:
    lines = _line_records(text)
    spans: list[EntitySpan] = []
    seen: set[tuple[int, int]] = set()
    for match in _TITULO_LABEL.finditer(text):
        same_end = text.find("\n", match.end())
        if same_end < 0:
            same_end = len(text)
        same = text[match.end() : same_end]
        digits = _TITULO_DIGITS.search(same)
        if digits is not None:
            start = match.end() + digits.start(1)
            end = match.end() + digits.end(1)
        else:
            line_idx = _line_index_at(lines, match.start())
            nxt = _next_nonempty(lines, line_idx + 1)
            if nxt is None:
                continue
            value_start, value = nxt
            digits = _TITULO_DIGITS.search(value)
            if digits is None:
                continue
            start = value_start + digits.start(1)
            end = value_start + digits.end(1)
        window = text[max(0, match.start() - 20) : min(len(text), end + 12)]
        if _TITULO_FORBIDDEN.search(window):
            continue
        compact = only_digits(text[start:end])
        if len(compact) == 12:
            if not is_valid_titulo_eleitor(compact):
                continue
        elif 10 <= len(compact) <= 11:
            padded = compact.zfill(12)
            if is_valid_titulo_eleitor(padded):
                compact = padded
        else:
            continue
        if len(compact) == 11 and is_valid_cpf(compact) and re.search(r"(?i)\bcpf\b", window):
            continue
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        spans.append(
            EntitySpan(
                start=start,
                end=end,
                entity_type="BR_TITULO_ELEITOR",
                score=_SCORE_ID,
                recognizer=RECOGNIZER,
                canonical_key=compact,
            )
        )
    return spans


def _parent_value_ok(value: str, extra_keep: Iterable[str]) -> bool:
    cleaned = re.sub(r"\s+", " ", value).strip(" -,;:|")
    if not cleaned:
        return False
    folded = cleaned.casefold()
    if folded in _PARENT_REFUSE:
        return False
    if _PARENT_NEXT_IS_LABEL.match(cleaned):
        return False
    letters = re.sub(r"[^A-Za-zÀ-ÿ ]", "", cleaned)
    words = [item for item in letters.split() if len(item) >= 2]
    if len(words) < 1:
        return False
    if is_kept_term(cleaned, extra_keep) or is_too_generic("PERSON", cleaned):
        return False
    return True


def _spans_filiacao(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    lines = _line_records(text)
    spans: list[EntitySpan] = []
    seen: set[tuple[int, int]] = set()
    for match in _PARENT_LABEL.finditer(text):
        same_end = text.find("\n", match.end())
        if same_end < 0:
            same_end = len(text)
        same = text[match.end() : same_end].strip(" :|-")
        if same and _parent_value_ok(same, extra_keep):
            raw = text[match.end() : same_end]
            stripped = raw.strip(" :|-")
            pad = raw.find(stripped)
            start = match.end() + pad
            end = start + len(stripped)
        else:
            line_idx = _line_index_at(lines, match.start())
            nxt = _next_nonempty(lines, line_idx + 1)
            if nxt is None:
                continue
            start, value = nxt
            if not _parent_value_ok(value, extra_keep):
                continue
            end = start + len(value)
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        spans.append(
            EntitySpan(
                start=start,
                end=end,
                entity_type="PERSON",
                score=_SCORE_PERSON,
                recognizer=RECOGNIZER,
            )
        )
    return spans

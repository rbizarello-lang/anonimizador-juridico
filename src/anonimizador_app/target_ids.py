"""CPF/CNPJ da carteira na lista de alvos.

Nao e regex faminta (P10): so localiza o numero que o usuario listou, com
digito verificador. CNPJ de orgao publico nao entra. Com o indice Sul no
disco, o CNPJ listado tambem traz a razao para esta rodada, mesmo se o
numero nao aparecer na peca.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from anonimizador_juridico.validators import (
    is_valid_cnpj,
    is_valid_cpf,
    map_ocr_digits,
    normalize_cnpj,
    only_digits,
)

from .fidelity import PUBLIC_CNPJ_ROOTS
from .gemini_detector import is_kept_term, is_too_generic

_SPLIT = re.compile(r"[\n;,]+")
_PREFIX = re.compile(
    r"(?is)^\s*(?:c\.?\s*n\.?\s*p\.?\s*j|c\.?\s*p\.?\s*f)(?:\s*/\s*mf)?"
    r"\s*(?:n[ºo°.]{0,3})?\s*[:.\-]?\s*"
)
_SEP = r"[\s.,/\-]*"
_OCR_ATOM = {
    "0": r"[0Oo]",
    "1": r"[1Il]",
    "5": r"[5Ss]",
    "8": r"[8Bb]",
    "9": r"[9GgQq]",
}


@dataclass(frozen=True, slots=True)
class ListedId:
    formatted: str
    canonical: str
    entity_type: str


def parse_target_ids(values: Iterable[str]) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for piece in _SPLIT.split(str(raw or "")):
            parsed = parse_target_id_line(piece)
            if parsed is None:
                continue
            key = id_canonical(parsed)
            if key in seen:
                continue
            seen.add(key)
            found.append(parsed)
    return tuple(found)


def parse_target_id_line(raw: str) -> str | None:
    stripped = _PREFIX.sub("", str(raw or "")).strip(" \t-–—|")
    if not stripped:
        return None
    if is_valid_cpf(stripped):
        return format_cpf(stripped)
    if is_valid_cnpj(stripped):
        if is_public_target_cnpj(stripped):
            return None
        return format_cnpj(stripped)
    return None


def id_canonical(value: str) -> str:
    if is_valid_cpf(value):
        return only_digits(value)
    return normalize_cnpj(value)


def format_cpf(value: str) -> str:
    digits = only_digits(value)
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def format_cnpj(value: str) -> str:
    compact = normalize_cnpj(value)
    return f"{compact[:2]}.{compact[2:5]}.{compact[5:8]}/{compact[8:12]}-{compact[12:]}"


def is_public_target_cnpj(value: str) -> bool:
    compact = normalize_cnpj(value)
    digits = only_digits(value)
    return any(
        compact.startswith(root) or digits.startswith(root) for root in PUBLIC_CNPJ_ROOTS
    )


def describe_listed_ids(values: Iterable[str]) -> tuple[ListedId, ...]:
    records: list[ListedId] = []
    for formatted in parse_target_ids(values):
        canonical = id_canonical(formatted)
        entity_type = "BR_CPF" if is_valid_cpf(formatted) else "BR_CNPJ"
        records.append(
            ListedId(formatted=formatted, canonical=canonical, entity_type=entity_type)
        )
    return tuple(records)


def find_listed_id_spans(
    text: str, ids: Iterable[str]
) -> list[tuple[int, int, ListedId]]:
    hits: list[tuple[int, int, ListedId]] = []
    occupied: list[tuple[int, int]] = []
    for record in describe_listed_ids(ids):
        pattern = _pattern_for(record)
        for match in pattern.finditer(text):
            snippet = match.group(0)
            if not _matches_canonical(snippet, record):
                continue
            start, end = match.span()
            if any(start < other_end and end > other_start for other_start, other_end in occupied):
                continue
            occupied.append((start, end))
            hits.append((start, end, record))
    hits.sort(key=lambda item: (item[0], -item[1]))
    return hits


def names_from_listed_cnpjs(
    ids: Iterable[str],
    *,
    extra_keep: Iterable[str] = (),
    index_path: Path | None = None,
) -> tuple[str, ...]:
    cnpjs = [
        record.canonical
        for record in describe_listed_ids(ids)
        if record.entity_type == "BR_CNPJ"
    ]
    if not cnpjs:
        return ()
    from .cnpj_index import CnpjIndex, default_index_path

    index = CnpjIndex(Path(index_path) if index_path else default_index_path())
    if not index.exists():
        return ()
    names: list[str] = []
    seen: set[str] = set()
    keep = tuple(extra_keep)
    for cnpj in cnpjs:
        record = index.lookup(cnpj)
        if record is None or record.publica:
            continue
        private = record.private_names()
        if not private:
            continue
        razao, *aliases = private
        _append_name(names, seen, razao, keep, generic=False)
        for alias in aliases:
            _append_name(names, seen, alias, keep, generic=True)
    return tuple(names)


def _append_name(
    names: list[str],
    seen: set[str],
    value: str,
    extra_keep: Sequence[str],
    *,
    generic: bool,
) -> None:
    name = value.strip()
    if not name or is_kept_term(name, extra_keep):
        return
    if generic and is_too_generic("ORGANIZATION", name):
        return
    key = name.casefold()
    if key in seen:
        return
    seen.add(key)
    names.append(name)


def _pattern_for(record: ListedId) -> re.Pattern[str]:
    atoms: list[str] = []
    ocr = record.entity_type == "BR_CPF"
    for char in record.canonical:
        if ocr and char in _OCR_ATOM:
            atoms.append(_OCR_ATOM[char])
        else:
            atoms.append(re.escape(char))
    body = _SEP.join(atoms)
    flags = re.IGNORECASE if record.entity_type == "BR_CNPJ" else 0
    return re.compile(rf"(?<![0-9A-Za-z])(?:{body})(?![0-9A-Za-z])", flags)


def _matches_canonical(snippet: str, record: ListedId) -> bool:
    if record.entity_type == "BR_CPF":
        return (
            only_digits(snippet) == record.canonical
            or map_ocr_digits(snippet) == record.canonical
        )
    return normalize_cnpj(snippet) == record.canonical

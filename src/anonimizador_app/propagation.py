"""Propaga as variantes do elenco a todas as ocorrencias do texto.

O LLM aponta uma ocorrencia; esta camada acha as outras, indiferente a
maiusculas, acentos, apostrofo e quebra de linha, com fronteira de palavra.
Variante de uma palavra so (sobrenome solto) nao se propaga: vale so onde o
LLM a ancorou, para nao comer homonimo (o juiz Souza nao e o socio Souza).
Termo protegido (KEEP_TERMS, exclusoes), data e ``R$`` nunca viram variante.
"""

from __future__ import annotations

import re
import unicodedata
from array import array
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

from anonimizador_juridico import EntitySpan

from .gemini_detector import KEEP_TERMS, is_registry_office, is_system_term
from .roster import TOKENIZE, RosterEntry, entity_core, has_token

RECOGNIZER = "ElencoLLM"
_SCORE = 0.95
_APOSTROPHES = frozenset("'’`´ʼ′")
_CONTENT_WORD = re.compile(r"[^\W\d_]{3,}")
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
_EDGE = " .,;:-–—()"
# Sufixo societario logo apos a razao ("acme comercio" + " ltda").
_COMPANY_SUFFIX = re.compile(r"(?:[ ,\-]+(?:ltda|me|epp|eireli|s/?a|s\.a))+(?![^\W_])")


@dataclass(frozen=True, slots=True)
class FoldedText:
    """Texto dobrado e, para cada caractere dele, a posicao no original."""

    text: str
    index: array

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        return self.index[start], self.index[end - 1] + 1


_HYPHENS = frozenset("-‐‑‒")
_BLANKS = " \t\r\n"


def fold_with_index(text: str) -> FoldedText:
    """Minusculas, sem acento nem apostrofo, espacos (inclusive quebra) colapsados.

    Hifen entre letras, inclusive o de quebra de linha (hifen no fim da linha, resto da palavra
    na seguinte), some: "ME-TAIS" e "METAIS" viram a mesma palavra (o OCR parte a palavra no
    fim da linha). Hifen com espaco dos dois lados ("ACME - MATRIZ") fica."""

    chars: list[str] = []
    index = array("q")
    size = len(text)
    position = 0
    while position < size:
        char = text[position]
        if char in _HYPHENS and chars and chars[-1].isalpha():
            ahead = position + 1
            while ahead < size and text[ahead] in _BLANKS and ahead - position <= 3:
                ahead += 1
            # Colado ("ME-TAIS") ou quebra de linha ("ME-" no fim, "TAIS" na seguinte).
            if ahead < size and text[ahead].isalpha() and (ahead == position + 1 or "\n" in text[position + 1 : ahead]):
                position = ahead
                continue
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                index.append(position)
            position += 1
            continue
        if char in _APOSTROPHES:
            position += 1
            continue
        folded = char.lower() if char.isascii() else _fold_char(char)
        for item in folded:
            chars.append(item)
            index.append(position)
        position += 1
    return FoldedText("".join(chars), index)


@lru_cache(maxsize=4096)
def _fold_char(char: str) -> str:
    decomposed = unicodedata.normalize("NFKD", char)
    return "".join(item for item in decomposed if not unicodedata.combining(item)).casefold()


def fold_value(value: str) -> str:
    return fold_with_index(value).text.strip(_EDGE)


def protected_forms(extra_keep: Iterable[str] = ()) -> frozenset[str]:
    return _protected_forms(tuple(str(item) for item in extra_keep))


@lru_cache(maxsize=32)
def _protected_forms(extra_keep: tuple[str, ...]) -> frozenset[str]:
    return frozenset(
        fold_value(item) for item in (*KEEP_TERMS, *extra_keep) if item.strip()
    )


def is_protected(value: str, protected: frozenset[str]) -> bool:
    folded = fold_value(value)
    return (
        not folded
        or is_registry_office(value)
        or folded in protected
        or "r$" in folded
        or bool(_DATE.search(folded))
        or not re.search(r"[^\W\d_]", folded)
    )


def propagable_variants(
    entry: RosterEntry,
    protected: frozenset[str],
) -> list[str]:
    """Formas dobradas buscaveis no texto inteiro: duas palavras de conteudo ou mais."""

    found: list[str] = []
    listed = entry.source == "lista"
    for value in (entry.name, *entry.variants):
        if has_token(value) or (is_protected(value, protected) and not (listed and is_system_term(value))):
            continue
        candidates = [fold_value(value)]
        if entry.kind == "empresa":
            candidates.append(entity_core("empresa", value).strip(_EDGE))
        for folded in candidates:
            if len(folded) < 4 or (folded in protected and not listed) or folded in found:
                continue
            if len(_CONTENT_WORD.findall(folded)) < 2:
                continue
            found.append(folded)
    return found


def spans_for_roster(
    text: str,
    entries: Sequence[RosterEntry],
    *,
    anchored: Iterable[tuple[int, int, str]] = (),
    extra_keep: Iterable[str] = (),
    folded: FoldedText | None = None,
) -> list[EntitySpan]:
    """Spans de todas as ocorrencias das entidades a tokenizar.

    ``anchored``: (inicio, fim, chave) no texto original, vindos do LLM. E o
    unico caminho de uma variante de palavra unica.
    """

    protected = protected_forms(extra_keep)
    source = folded or fold_with_index(text)
    by_key: Mapping[str, RosterEntry] = {entry.key: entry for entry in entries}
    spans: list[EntitySpan] = []
    for entry in entries:
        if entry.action != TOKENIZE:
            continue
        for variant in propagable_variants(entry, protected):
            for start, end in _find_all(source.text, variant):
                if entry.kind == "empresa":
                    end = _extend_company_suffix(source.text, end)
                original_start, original_end = source.to_original(start, end)
                spans.append(_span(entry, original_start, original_end))
    for start, end, key in anchored:
        entry = by_key.get(key)
        if entry is None or entry.action != TOKENIZE:
            continue
        start, end = _trim(text, start, end)
        if end <= start:
            continue
        if is_protected(text[start:end], protected) and not (
            entry.source == "lista" and is_system_term(text[start:end])
        ):
            continue  # a lista do caso vence a lista protegida de sistemas (H9-i lote B1)
        spans.append(_span(entry, start, end))
    return longest_first(spans)


def kept_name_ranges(
    text: str,
    entries: Iterable[RosterEntry],
    *,
    extra_keep: Iterable[str] = (),
    folded: FoldedText | None = None,
) -> list[tuple[int, int]]:
    """Ocorrencias (no texto original) do nome inteiro das entradas de pessoa ``manter`` (agente publico, banco...):
    duas palavras de conteudo ou mais, como na propagacao. H9-i lote B: a lista do caso vence a lista protegida, mas
    nao entra no meio do nome de quem o elenco mantem em claro."""

    protected = protected_forms(extra_keep)
    source = folded or fold_with_index(text)
    ranges: list[tuple[int, int]] = []
    for entry in entries:
        if entry.action == TOKENIZE or entry.kind != "pessoa":
            continue
        for variant in propagable_variants(entry, protected):
            for start, end in _find_all(source.text, variant):
                ranges.append(source.to_original(start, end))
    return ranges


def longest_first(spans: Iterable[EntitySpan]) -> list[EntitySpan]:
    """Sobreposicao entre entidades do elenco: vence a mencao mais longa."""

    starts: list[int] = []
    chosen: list[EntitySpan] = []
    for span in sorted(spans, key=lambda item: (-(item.end - item.start), item.start)):
        position = bisect_left(starts, span.end)
        if position and chosen[position - 1].end > span.start:
            continue
        starts.insert(position, span.start)
        chosen.insert(position, span)
    return chosen


def _span(entry: RosterEntry, start: int, end: int) -> EntitySpan:
    return EntitySpan(
        start=start,
        end=end,
        entity_type=entry.entity_type,
        score=_SCORE,
        recognizer=RECOGNIZER,
        canonical_key=entry.key,
        alvo=entry.name,
    )


def _find_all(haystack: str, needle: str) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    start = haystack.find(needle)
    while start >= 0:
        end = start + len(needle)
        before = haystack[start - 1] if start > 0 else " "
        after = haystack[end] if end < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            hits.append((start, end))
        start = haystack.find(needle, start + 1)
    return hits


def _extend_company_suffix(folded: str, end: int) -> int:
    match = _COMPANY_SUFFIX.match(folded, end)
    return match.end() if match else end


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in _EDGE + "\n\t":
        start += 1
    while end > start and text[end - 1] in _EDGE + "\n\t":
        end -= 1
    return start, end

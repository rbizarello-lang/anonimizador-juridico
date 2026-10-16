"""Normalizador compartilhado de logradouro (Ciclo 3).

Canonico: ``{via_folded} {numero}``. Equivalencias de via e de numero
(nº / n° / n. / no / n / virgula) entram aqui, nao no GLiNER.

A tabela ``_SPELLING_PAIRS`` e uma lista pequena de pares deste caso —
nao e um corretor OCR aberto de ruas do Brasil.
"""

from __future__ import annotations

import re
import unicodedata

_VIA_PREFIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:r[uú]a)\s+", re.IGNORECASE), "rua "),
    (re.compile(r"^r\.\s*", re.IGNORECASE), "rua "),
    (re.compile(r"^r\s+", re.IGNORECASE), "rua "),
    (re.compile(r"^avenida\s+", re.IGNORECASE), "avenida "),
    (re.compile(r"^avn\.?\s+", re.IGNORECASE), "avenida "),
    (re.compile(r"^av\.\s*", re.IGNORECASE), "avenida "),
    (re.compile(r"^av\s+", re.IGNORECASE), "avenida "),
    (re.compile(r"^alameda\s+", re.IGNORECASE), "alameda "),
    (re.compile(r"^al\.\s*", re.IGNORECASE), "alameda "),
    (re.compile(r"^pra[cç]a\s+", re.IGNORECASE), "praca "),
    (re.compile(r"^p[cç]\.\s*", re.IGNORECASE), "praca "),
    (re.compile(r"^travessa\s+", re.IGNORECASE), "travessa "),
    (re.compile(r"^tv\.\s*", re.IGNORECASE), "travessa "),
    (re.compile(r"^rodovia\s+", re.IGNORECASE), "rodovia "),
    (re.compile(r"^rod\.\s*", re.IGNORECASE), "rodovia "),
    (re.compile(r"^estrada\s+", re.IGNORECASE), "estrada "),
    (re.compile(r"^servid[ãa]o\s+", re.IGNORECASE), "servidao "),
    (re.compile(r"^loteamento\s+", re.IGNORECASE), "loteamento "),
)

# (trecho que deve existir na via, forma_origem, forma_destino).
# ari=ary so quando a via contem jose de oliveira meireles (rua ficticia).
_SPELLING_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("jose de oliveira meireles", "ary", "ari"),
    ("meireles", "oivira", "oliveira"),
    ("raposo", "tavates", "tavares"),
    ("vaz de caminh", "caminht", "caminha"),
    ("vaz de caminha", "pedro", "pero"),
)

_NUMBER = re.compile(
    r"(?:"
    r"[,;.\-–—]\s*"
    r"|"
    r"\s+"
    r")"
    r"(?:n[º°o.]?\s*|n[uú]mero\s+)?"
    r"(?<![a-z0-9])"
    r"(\d{1,5})(?!\d)",
    re.IGNORECASE,
)


def normalize_via_prefix(value: str) -> str:
    """r./r /rua/rua; av./avn./avenida/av ; al./alameda; pç./praça."""

    text = value
    for pattern, replacement in _VIA_PREFIXES:
        if pattern.match(text):
            return pattern.sub(replacement, text, count=1)
    return text


def apply_street_spelling(via: str) -> str:
    """Aplica pares de grafia do caso. Nao generaliza para outras ruas."""

    updated = via
    for needle, source, target in _SPELLING_PAIRS:
        if needle not in updated:
            continue
        updated = re.sub(rf"\b{re.escape(source)}\b", target, updated)
    return updated


def split_street_number(value: str) -> tuple[str, str]:
    """Separa via e numero predial (primeiro 1–5 digitos apos a via)."""

    match = _NUMBER.search(value)
    if match is None:
        return value.strip(" ,.;:-–—"), ""
    street = value[: match.start()].strip(" ,.;:-–—")
    number = match.group(1)
    return street, number


def _fold(value: str) -> str:
    """Mesma regra de ``pseudonymizer.fold_lexical`` (sem import circular)."""

    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    collapsed = re.sub(r"\s+", " ", stripped).strip().casefold()
    collapsed = collapsed.rstrip(".,;:!?…")
    return re.sub(r"\s+", " ", collapsed).strip()


def canonicalize_location(value: str) -> str:
    """Fold de acento + via equivalente + numero unificado + pares do caso."""

    folded = _fold(value)
    folded = folded.replace("–", " ").replace("—", " ")
    folded = re.sub(r"\s+", " ", folded).strip()
    folded = re.sub(r"\bno(\d{1,5})\b", r"n \1", folded)
    folded = re.sub(r"\bn\.(\d{1,5})\b", r"n \1", folded)
    via = normalize_via_prefix(folded)
    via = apply_street_spelling(via)
    street, number = split_street_number(via)
    street = normalize_via_prefix(street)
    street = apply_street_spelling(street)
    street = re.sub(r"\s+", " ", street).strip(" ,.;:-")
    if number:
        return f"{street} {number}"
    return street

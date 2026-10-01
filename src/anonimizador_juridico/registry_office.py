"""Cartorio de registro de imoveis mais proximo de uma matricula (endurecimento, deteccao 2.7).

A matricula so tem sentido dentro do cartorio que a abriu: a matricula 12.345 do 1o Registro de
Imoveis de X e a do 2o Registro de Imoveis de X sao imoveis diferentes. A chave de uma matricula
passa a ser (cartorio, numero) quando o texto traz o cartorio por perto; sem cartorio, a chave e o
numero sozinho, como antes.

O cartorio e o que o texto diz, no que distingue um do outro: o numero de ordem ("1o", "2o") e o lugar
("de X", "da Comarca de X"). Mencao sem numero de ordem e sem lugar ("Registro de Imoveis" solto) nao
distingue nada e nao vira chave.
"""

from __future__ import annotations

import re

from .pseudonymizer import fold_lexical

# Quanto o texto pode distar da matricula: antes (certidao do cartorio X ... matricula N) e depois
# ("matricula N do cartorio X").
_BEFORE = 600
_AFTER = 220

_ORDINAL_WORDS = {
    "primeiro": "1", "primeira": "1", "segundo": "2", "segunda": "2", "terceiro": "3", "terceira": "3",
    "quarto": "4", "quarta": "4", "quinto": "5", "quinta": "5", "sexto": "6", "sexta": "6",
    "setimo": "7", "setima": "7", "oitavo": "8", "oitava": "8", "nono": "9", "nona": "9",
    "decimo": "10", "decima": "10",
}

# A mencao do cartorio: "Registro de Imoveis", "Registro Geral de Imoveis", "Cartorio de Registro de Imoveis",
# "Oficio de Registro de Imoveis", "Servico Registral", "1o Oficio", "Cartorio do 2o Oficio".
_ANCHOR = re.compile(
    r"(?i)(?:\b\d{1,2}\s*[ºo°ª.]\s*)?\b(?:(?:cart[oó]rio|of[ií]cio|servi[cç]o)\s+(?:de\s+|do\s+)?)?"
    r"registro\s+(?:geral\s+)?de\s+im[oó]veis\b"
    r"|\bservi[cç]o\s+registral(?:\s+imobili[aá]rio)?\b"
    r"|\bcart[oó]rio\s+(?:de\s+|do\s+)?(?:registro|\d{1,2}\s*[ºo°ª])"
    r"|\b\d{1,2}\s*[ºo°ª.]?\s*(?:of[ií]cio|cart[oó]rio)\b"
    r"|\bof[ií]cio\s+(?:de\s+)?(?:registro|im[oó]veis)\b"
)
_ORDINAL_BEFORE = re.compile(
    r"(?i)(?:(?<![\d.,/-])(?P<num>\d{1,2})\s?\.?\s?[ºo°ª]|\b(?P<word>primeir[oa]|segund[oa]|terceir[oa]|quart[oa]|"
    r"quint[oa]|sext[oa]|s[eé]tim[oa]|oitav[oa]|non[oa]|d[eé]cim[oa]))"
    r"\s*(?:[-–]\s*)?(?:servi[cç]o\s+(?:de\s+)?|of[ií]cio\s+(?:de\s+)?|cart[oó]rio\s+(?:de\s+)?)?"
    r"(?:registro\s+(?:geral\s+)?(?:de\s+im[oó]veis)?)?\s*$"
)
_ORDINAL_AFTER = re.compile(
    r"(?i)^\s*(?:d[oa]\s+)?(?:(?P<num>\d{1,2})\s*[ºo°ª]|(?P<word>primeir[oa]|segund[oa]|terceir[oa]|quart[oa]|"
    r"quint[oa]|sext[oa]|s[eé]tim[oa]|oitav[oa]|non[oa]|d[eé]cim[oa]))\s*(?:of[ií]cio|cart[oó]rio|servi[cç]o|"
    r"registro|distrito|ofic|circunscri[cç][aã]o|zona)"
)
_ORDINAL_INSIDE = re.compile(r"(?i)^(\d{1,2})\s*[ºo°ª.]")
# "de X", "da Comarca de X", "do Municipio de X" logo depois: palavras com inicial maiuscula (ou tudo em
# maiuscula), conectivos "de/da/do/dos/das" no meio, ate quatro palavras; UF, numero e o rotulo da matricula
# encerram o lugar.
_PLACE = re.compile(
    r"^\s*,?\s*(?i:d[eao]s?\s+)?(?i:(?:comarca|cidade|munic[ií]pio|foro)\s+(?:d[eao]s?\s+)?)?"
    r"(?P<place>[A-ZÀ-Ý][\wÀ-ÿ'’]+(?:[ \t]+(?i:d[eao]s?[ \t]+)?[A-ZÀ-Ý][\wÀ-ÿ'’]+){0,3})"
)
_STOP = frozenset(
    {
        "matricula", "matriculas", "certidao", "imovel", "imoveis", "registro", "cartorio", "oficio", "servico",
        "comarca", "estado", "processo", "pagina", "folha", "fls", "livro", "ficha", "rgi", "inteiro", "teor",
        "numero", "data", "emitida", "expedida", "titulo", "proprietario", "proprietaria", "averbacao",
    }
)
_UF = re.compile(r"^[A-Z]{2}$")


def _place_of(text: str, position: int) -> str:
    match = _PLACE.match(text[position : position + 120])
    if match is None:
        return ""
    words: list[str] = []
    for word in re.findall(r"[\wÀ-ÿ'’]+", match.group("place")):
        folded = fold_lexical(word)
        if folded in _STOP or _UF.match(word):
            break
        words.append(folded)
    while words and words[-1] in {"de", "da", "do", "dos", "das"}:
        words.pop()
    return " ".join(words)


def _ordinal(text: str, start: int, end: int) -> str:
    """Numero de ordem do cartorio: logo antes da mencao ("1o Registro de Imoveis") ou logo depois
    ("Cartorio do 2o Oficio")."""

    inside = _ORDINAL_INSIDE.match(text[start:end])
    if inside is not None:
        return str(int(inside.group(1)))
    before = _ORDINAL_BEFORE.search(text[max(0, start - 40) : start])
    after = _ORDINAL_AFTER.match(text[end : end + 40])
    match = before or after
    if match is None:
        return ""
    if match.groupdict().get("num"):
        return str(int(match.group("num")))
    if match.groupdict().get("word"):
        return _ORDINAL_WORDS.get(fold_lexical(match.group("word")), "")
    return ""


def office_key(text: str, start: int, end: int) -> str:
    """Chave do cartorio mais proximo do trecho [start, end) ("1|joinville", "2|", "|joinville") ou "".

    O mais proximo vence; empate, o que vem antes. Mencao que nao diz nem a ordem nem o lugar nao conta."""

    best: tuple[int, int, str] | None = None
    window_start = max(0, start - _BEFORE)
    for match in _ANCHOR.finditer(text, window_start, min(len(text), end + _AFTER)):
        if match.start() < start:
            gap, side = max(0, start - match.end()), 0
        else:
            gap, side = max(0, match.start() - end), 1
        if match.end() <= start and gap > _BEFORE or match.start() >= end and gap > _AFTER:
            continue
        ordinal = _ordinal(text, match.start(), match.end())
        place = _place_of(text, match.end())
        if not ordinal and not place:
            continue
        candidate = (gap, side, f"{ordinal}|{place}")
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    return best[2] if best else ""

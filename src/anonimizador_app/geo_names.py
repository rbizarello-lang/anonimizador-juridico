"""Nomes de municipio e de estado (IBGE) e contexto de foro/endereco, para a marca de empresa (B3).

A marca de uma palavra (E13) tokeniza a palavra inicial do nome de uma empresa quando o lote a usa
sozinha. Razao social que abre por nome de cidade ("Cidade Metais Ltda") faria o foro, a comarca e o
endereco virarem "empresa" no texto. Duas travas, ambas sem rede:

1. a palavra que e nome de municipio ou de estado (lista oficial do IBGE, ``data/municipios_ibge.txt``,
   baixada uma vez e guardada no pacote) nunca e marca;
2. a ocorrencia em contexto de foro ou de endereco ("Vara ... de X", "Subsecao Judiciaria de X",
   "Comarca de X", "Municipio de X", "X/UF", linha de endereco, ou dentro de nome composto de
   municipio/estado, "Santa X") nao recebe o token, mesmo em CAIXA ALTA, onde "nunca em minuscula"
   nao diz nada.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from anonimizador_juridico.pseudonymizer import fold_lexical

DATA_FILE = Path(__file__).with_name("data") / "municipios_ibge.txt"
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_MAX_NAME_WORDS = 5

UF_SIGLAS = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"

_PREP = r"(?:de|da|do|em)"
# Rotulo que anuncia lugar ("Comarca: X", "Cidade | X").
_LABEL_BEFORE = re.compile(
    r"(?i)\b(?:cidade|munic[ií]pio|comarca|localidade|naturalidade|foro|bairro|uf|estado|local)\s*[:|]\s*$"
)
# Palavras que ficam entre "Vara" e "de X": "Vara Federal de Execucoes Fiscais de X".
_VARA_WORDS = (
    r"(?:\s+(?:federal|c[ií]vel|criminal|trabalho|execu[cç][õo]es|fiscais?|[uú]nica|fam[ií]lia|fazenda|"
    r"p[uú]blica|estadual|especial|especializada|adjunta|comum|previdenci[aá]ria|d[aeo]s?|\d+[ªº°a]?|[ivx]+))*"
)
# Foro e divisao territorial: "Vara Federal de X", "Subsecao Judiciaria de X", "Comarca de X"...
_COURT_BEFORE = re.compile(
    rf"(?i)(?:\bvara{_VARA_WORDS}|\bsubse[cç][aã]o\s+judici[aá]ria|\bse[cç][aã]o\s+judici[aá]ria|"
    r"\bcomarca|\bmunic[ií]pio|\bestado|\bcidade|\bfor[oó]|\bdistrito|\bjuizado\s+especial(?:\s+\w+)?|"
    r"\bregistro\s+de\s+im[oó]veis|\bcart[oó]rio(?:\s+de\s+(?:registro|notas|protesto)(?:\s+\w+){0,3}?)?|"
    r"\btabelionato(?:\s+de\s+\w+)?|\bturma\s+recursal|\bjusti[cç]a\s+(?:federal|do\s+trabalho))"
    rf"\s+{_PREP}\s+(?:(?:cidade|munic[ií]pio|comarca|estado)\s+(?:d[aeo]s?\s+)?)?$"
)
_SEAT_BEFORE = re.compile(r"(?i)\bsede\s+(?:social\s+)?(?:em|na\s+cidade\s+d[eo])\s+$")
# Logo depois: "X/SC", "X - SC", "X, SC", "X (SC)"; "X, 12 de marco de 2026" (local e data da peca).
_UF_AFTER = re.compile(rf"\s*[/\-–—,(]\s*(?:{UF_SIGLAS})(?![\wÀ-ÿ])")
_PLACE_DATE_AFTER = re.compile(r"\s*,\s*\d{1,2}\s*(?:de|/)\s*[\wÀ-ÿ]+\s*(?:de|/)\s*\d{2,4}", re.I)
# Endereco em volta: CEP perto, ou "Rua X, 10," / "Endereco:" na mesma frase antes da palavra.
_CEP_NEAR = re.compile(r"(?i)\bcep\b|(?<!\d)\d{5}-?\d{3}(?!\d)")
_STREET_BEFORE = re.compile(
    r"(?i)(?:\b(?:rua|r\.|av\.?|avenida|alameda|travessa|rodovia|estrada|pra[cç]a|servid[ãa]o|loteamento)\s+[^.;\n]{0,70}?\d[^.;\n]{0,30}|"
    r"\b(?:endere[cç]o|logradouro|domic[ií]lio)\b[^.;\n]{0,100})$"
)
_COURT_HEADER = re.compile(
    r"(?i)\b(?:vara|ju[ií]zo|subse[cç][aã]o|se[cç][aã]o\s+judici|comarca|f[oó]rum|poder\s+judici[aá]rio)\b"
)


@lru_cache(maxsize=1)
def _load() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(nomes de uma palavra, nomes de 2+ palavras, estados), tudo dobrado (sem acento, minusculo)."""

    single: set[str] = set()
    multi: set[str] = set()
    states: set[str] = set()
    section = ""
    for raw in DATA_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line
            continue
        name = line.split(";", 1)[1] if section == "[UF]" and ";" in line else line
        folded = fold_lexical(name)
        if not folded:
            continue
        (multi if " " in folded else single).add(folded)
        if section == "[UF]":
            states.add(folded)
    return frozenset(single), frozenset(multi), frozenset(states)


def geo_name_count() -> int:
    single, multi, _states = _load()
    return len(single) + len(multi)


def multi_word_geo_names() -> frozenset[str]:
    """Nomes de municipio ou estado com 2+ palavras, dobrados ("sao paulo", "santa catarina")."""

    return _load()[1]


def is_geo_word(word: str) -> bool:
    """A palavra (inteira) e nome de municipio ou de estado do Brasil."""

    return fold_lexical(word) in _load()[0]


def _words_around(text: str, start: int, end: int) -> tuple[list[str], int, int]:
    """Palavras dobradas da linha em volta do trecho (ate 4 de cada lado), indice da primeira palavra
    do trecho e quantas palavras o trecho tem."""

    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    before = _WORD.findall(text[max(line_start, start - 80) : start])[-(_MAX_NAME_WORDS - 1) :]
    after = _WORD.findall(text[end : min(line_end, end + 80)])[: _MAX_NAME_WORDS - 1]
    middle = _WORD.findall(text[start:end]) or [text[start:end]]
    words = [fold_lexical(word) for word in (*before, *middle, *after)]
    return words, len(before), len(middle)


def inside_geo_name(text: str, start: int, end: int) -> bool:
    """O trecho faz parte de nome composto de municipio ou estado ("Santa Catarina", "Sao Paulo")."""

    _single, multi, _states = _load()
    words, index, span = _words_around(text, start, end)
    for first in range(max(0, index - (_MAX_NAME_WORDS - 1)), index + 1):
        for last in range(index + span, min(len(words), first + _MAX_NAME_WORDS) + 1):
            if last - first >= 2 and " ".join(words[first:last]) in multi:
                return True
    return False


def geo_context(text: str, start: int, end: int) -> bool:
    """O trecho esta em contexto de foro ou de endereco: o que ele nomeia e um lugar, nao uma empresa."""

    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    before = text[max(line_start, start - 160) : start]
    after = text[end : min(line_end, end + 40)]
    if _LABEL_BEFORE.search(before) or _COURT_BEFORE.search(before) or _SEAT_BEFORE.search(before):
        return True
    if _UF_AFTER.match(after) or _PLACE_DATE_AFTER.match(after):
        return True
    if _STREET_BEFORE.search(before) or _CEP_NEAR.search(
        text[max(line_start, start - 100) : min(line_end, end + 60)]
    ):
        return True
    # Cabecalho em CAIXA ALTA de foro ("PODER JUDICIARIO ... JOINVILLE"): a caixa nao distingue nada.
    line = text[line_start:line_end]
    if any(char.isalpha() for char in line) and line.upper() == line and _COURT_HEADER.search(line):
        return True
    return inside_geo_name(text, start, end)


_UF_ONLY = re.compile(rf"^(?:{UF_SIGLAS})$")


def is_geo_name(value: str) -> bool:
    """O texto inteiro e nome de municipio ou de estado do Brasil (uma ou mais palavras), ou sigla de UF.

    A sigla de UF so vale em maiuscula ("SC"); "sc" ou "Pa" podem ser outra coisa."""

    compact = " ".join(value.split()).strip(" ,.;:-")
    if not compact:
        return False
    if _UF_ONLY.match(compact):
        return True
    single, multi, _states = _load()
    folded = fold_lexical(compact)
    return folded in single or folded in multi


def is_state_name(value: str) -> bool:
    """O texto inteiro e nome de estado do Brasil ("Santa Catarina") ou sigla de UF: nunca e pessoa."""

    compact = " ".join(value.split()).strip(" ,.;:-")
    if not compact:
        return False
    if _UF_ONLY.match(compact):
        return True
    return fold_lexical(compact) in _load()[2]


def place_label_before(text: str, start: int) -> bool:
    """O trecho vem logo depois de rotulo ou expressao que anuncia lugar ("Comarca de", "Cidade:", "Vara
    Federal de", "sede em"). Nao olha o que vem depois nem o CEP em volta: serve para nome solto."""

    line_start = text.rfind("\n", 0, start) + 1
    before = text[max(line_start, start - 160) : start]
    return bool(_LABEL_BEFORE.search(before) or _COURT_BEFORE.search(before) or _SEAT_BEFORE.search(before))

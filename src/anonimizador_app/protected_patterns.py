"""Padroes protegidos com trava (H9-i, lista do Operador, itens 6a a 6d): orgao fracionado, sigla com UF ou regiao,
cooperativa de credito e legislacao. Cada padrao devolve a regiao exata que protege; so o span que cabe inteiro na
regiao (nome de orgao, empresa ou endereco) deixa de ser trocado. Nome de pessoa ou empresa que venha DEPOIS da regiao
continua sujeito a troca: nao ha janela livre. A lista do caso (``ListaDeAlvos``) vence estes padroes (trava geral).
"""

from __future__ import annotations

import re
from functools import lru_cache

from .geo_names import UF_SIGLAS, is_geo_name

_ORDINAL = (
    r"(?:\d{1,2}\s*[ªaºo°]|primeir[ao]|segund[ao]|terceir[ao]|quart[ao]|quint[ao]|sext[ao]|s[eé]tim[ao]|oitav[ao]"
    r"|non[ao]|d[eé]cim[ao](?:\s+(?:primeir[ao]|segund[ao]|terceir[ao]|quart[ao]|quint[ao]))?|[uú]nic[ao])"
)
_COMPLEMENTO = (
    r"(?:Federal|Estadual|C[ií]vel|Criminal|do\s+Trabalho|Empresarial|Previdenci[aá]ria|Recursal|Judici[aá]ria|Fiscal"
    r"|Especial\s+Federal|da\s+Fazenda\s+P[uú]blica|de\s+Execu[cç][oõ]es\s+Fiscais"
    r"|de\s+Fal[eê]ncias(?:\s+e\s+Recupera[cç][oõ]es\s+Judiciais)?)"
)
_ORGAO = re.compile(
    rf"(?i:\b{_ORDINAL}\s+(?:Vara|Ju[ií]zo|Turma|C[âa]mara|Se[cç][aã]o|Subse[cç][aã]o|Comarca|Circunscri[cç][aã]o"
    rf"|Regi[aã]o\s+Fiscal)(?:\s+{_COMPLEMENTO})*)"
)
_CARTORIO = re.compile(
    rf"(?i:\b{_ORDINAL}\s+(?:Tabelionato|Of[ií]cio|Cart[oó]rio)\s+(?:de\s+)?(?:Notas|Protestos?|Registro\s+de\s+Im[oó]veis"
    r"|Registro\s+Civil|Registro\s+de\s+T[ií]tulos\s+e\s+Documentos|Civil))"
)
_MUNICIPIO = re.compile(
    r"\s+(?i:de|do|da)\s+((?:[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÀ-ÿ'’\-]*)(?:\s+(?:(?i:d[aeo]s?)\s+)?[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÀ-ÿ'’\-]*){0,3})"
)
_UF_DEPOIS = re.compile(rf"\s*[/\-]\s*(?:{UF_SIGLAS})\b")
_SIGLA_UF = re.compile(
    r"\b(?:TRF|TRT|TRE|TJ|PRFN|PSFN|SRFB|DRF|DRJ|DETRAN|SEFAZ|PGE|OAB|CREA|CRM|CRC|INCRA|IBAMA|JUCE[A-Z]{0,3})"
    rf"(?:[-/ ]\s?(?:{UF_SIGLAS})\b|\s?\d{{1,2}}\s?[ªa]?\s?Regi[aã]o|-?\d{{1,2}}\b)"
)
_OAB_COM_NUMERO = re.compile(r"\s*(?:n[º°o.]*\s*)?:?\s*\d")
_COOPERATIVA = re.compile(
    r"\b(?:Cooperativa\s+de\s+Cr[eé]dito|Sicredi|Sicoob|Unicred|Cresol|Ailos)(?:[ \t]+[^\s,.;\n\d]+){0,5}"
)
_LEGISLACAO = re.compile(
    r"(?i:\b(?:Lei(?:\s+Complementar)?|Decreto(?:-Lei)?|Portaria|Instru[cç][aã]o\s+Normativa|Resolu[cç][aã]o|S[úu]mula"
    r"|Tema|Ato\s+Declarat[óo]rio|Parecer\s+Normativo|Emenda\s+Constitucional)"
    r"(?:\s+(?:Federal|Estadual|Municipal|Conjunta|PGFN|RFB|CNJ|STJ|STF))?"
    r"(?:\s*(?:n[º°o.]{1,2}|n\.)\s*|\s+)\d[\d.\-/]*\d|\b(?:Lei(?:\s+Complementar)?|Decreto(?:-Lei)?|S[úu]mula|Tema)"
    r"(?:\s*(?:n[º°o.]{1,2}|n\.)\s*|\s+)\d)"
)


def _with_place(text: str, end: int) -> int:
    """Estende a regiao com " de/do/da <municipio>" (se o nome for municipio ou estado) e "/UF"."""

    match = _MUNICIPIO.match(text, end)
    if match is not None:
        words = match.group(1).split()
        for size in range(len(words), 0, -1):
            if is_geo_name(" ".join(words[:size])):
                cursor = match.start(1)
                for word in words[:size]:
                    cursor = text.index(word, cursor) + len(word)
                end = cursor
                break
    uf = _UF_DEPOIS.match(text, end)
    return uf.end() if uf is not None else end


@lru_cache(maxsize=2)
def protected_regions(text: str) -> tuple[tuple[int, int], ...]:
    """Regioes ``(inicio, fim)`` do texto protegidas pelos padroes 6a a 6c (orgao, sigla com UF, cooperativa)."""

    regions: list[tuple[int, int]] = []
    for pattern in (_ORGAO, _CARTORIO):
        for match in pattern.finditer(text):
            regions.append((match.start(), _with_place(text, match.end())))
    for match in _SIGLA_UF.finditer(text):
        if match.group().startswith("OAB") and _OAB_COM_NUMERO.match(text, match.end()):
            continue  # "OAB/SC 12.345": o numero da OAB e trocado
        if match.group().startswith("OAB") and re.search(r"\d", match.group()):
            continue
        regions.append((match.start(), _with_place(text, match.end())))
    for match in _COOPERATIVA.finditer(text):
        regions.append((match.start(), match.end()))
    return tuple(regions)


@lru_cache(maxsize=2)
def legislation_regions(text: str) -> tuple[tuple[int, int], ...]:
    """Regioes de citacao de ato normativo ("Lei Complementar nº 123", "Tema 1.209"): nada ali e entidade nem troca."""

    return tuple((match.start(), match.end()) for match in _LEGISLACAO.finditer(text))


def inside_protected_region(text: str, start: int, end: int) -> bool:
    """O span cabe inteiro numa regiao protegida de orgao, sigla com UF ou cooperativa."""

    return any(rs <= start and end <= re_ for rs, re_ in protected_regions(text))


def touches_legislation(text: str, start: int, end: int) -> bool:
    return any(start < re_ and rs < end for rs, re_ in legislation_regions(text))

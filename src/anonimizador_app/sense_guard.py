"""v0.4.12: o que nao e nome nao vira token (falsos positivos do caso 2, motor sozinho; decisao do Operador de 22/10, noite:
"na duvida entre tokenizar e preservar o sentido, palavra comum ou expressao juridica nunca vira token").

Tres casos medidos no caso 2 (so contagens):

- expressao generica tomada por nome: "empresa executada" virou [EMPRESA_n] 10 vezes; "socio remanescente" e "empresa
  individual de responsabilidade limitada" tambem. Uma expressao feita so de palavras comuns de papel processual ou
  societario ("a executada", "sociedade empresaria", "pessoa juridica executada") nao e nome;
- ministro citado com o titulo abreviado DENTRO do trecho ("Min. X", "Rel. Min. X"): a regra do julgador olhava o titulo
  antes do trecho e, dentro dele, so "Ministro/Ministra" por extenso;
- contato do orgao publico no cabecalho de cada folha (PRFN, Procuradoria da Fazenda Nacional): o endereco, o CEP e o
  telefone ficam duas ou tres linhas abaixo do nome do orgao, e a regra antiga so olhava a linha de cima (263 trocas).

Dado generico; nenhum dado de caso. Chave no config.json: ``case2_false_positives``.
"""

from __future__ import annotations

import re
import unicodedata

# Palavras de papel processual ou societario (dobradas, sem acento). Expressao feita SO delas nao e nome.
GENERIC_WORDS = frozenset(
    """
    a o as os e de da do das dos em na no nas nos ao aos pela pelo pelas pelos sua seu suas seus esta este essa esse
    empresa empresas sociedade sociedades firma firmas pessoa pessoas juridica juridicas fisica fisicas
    empresaria empresario empresarias empresarios individual responsabilidade limitada limitado ltda
    executada executado executadas executados requerida requerido requeridas requeridos exequente exequentes
    devedora devedor devedoras devedores credora credor credoras credores autora autor autoras autores re reu reus
    parte partes adversa adverso contraria contrario demandada demandado embargante embargada embargado agravante
    agravada agravado interessada interessado terceira terceiro terceiros requerente requerentes
    socio socia socios socias remanescente remanescentes retirante retirantes ingressante cotista quotista
    administrador administradora administradores gerente titular sucessora sucessor sucedida sucedido
    holding matriz filial filiais grupo economico familiar patrimonial controladora controlada coligada
    nova novo antiga antigo atual referida referido aludida aludido citada citado mencionada mencionado dita dito
    mesma mesmo propria proprio ora primeira primeiro segunda segundo outra outro outras outros demais
    individual eireli unipessoal simples limitada
    """.split()
)
# A expressao comeca por um destes substantivos (o resto pode ser qualquer palavra comum em minuscula).
_ROLE_HEADS = frozenset(
    {"empresa", "sociedade", "firma", "pessoa", "socio", "socia", "parte", "executada", "executado", "requerida",
     "requerido", "devedora", "devedor", "holding", "grupo", "titular", "administradora", "administrador"}
)
_WORD = re.compile(r"[^\W\d_]+")

# Titulo abreviado no comeco do trecho: "Min. X", "Rel. Min. X", "Des. Fed. X", "Exmo. Min. X".
_ABBREVIATED_TITLE = re.compile(
    r"(?i)^\s*(?:exm[oa]\.?\s+(?:sr[a]?\.?\s+)?)?(?:rel(?:ator(?:a)?)?\.?\s*(?::\s*)?)?"
    r"(?:min\.|minist(?:ro|ra)\b|des\.(?:\s*fed(?:eral|\.))?|desembargador(?:a)?\b|ju[ií]z(?:a)?\b)"
)

# Nome de orgao publico numa linha de cabecalho (a lista antiga, em ``fidelity._ORGAN_LINE``, mais siglas da PGFN).
_PUBLIC_ORGAN = re.compile(
    r"(?i)(?<![^\W\d_])(?:procuradoria|pgfn|prfn|psfn|pfn|advocacia-geral|receita\s+federal|justi[cç]a\s+federal"
    r"|tribunal|se[cç][aã]o\s+judici[aá]ria|subse[cç][aã]o\s+judici[aá]ria|vara\s+federal|f[oó]rum"
    r"|minist[ée]rio\s+(?:p[úu]blico|da\s+fazenda|da\s+economia)|defensoria|fazenda\s+nacional"
    r"|n[uú]cleo\s+de\s+investiga[cç][aã]o|unidade\s+de\s+apoio|junta\s+comercial)(?![^\W\d_])"
)
_CONTACT_TYPES = frozenset({"LOCATION", "BR_CEP", "BR_PHONE", "EMAIL_ADDRESS"})
_PRIVATE_CUE = re.compile(
    r"(?i)\b(?:residente|domiciliad[oa]|reside|resid[êe]ncia|propriet[áa]ri[oa]|portador|cpf|cnpj|ltda|eireli|s\.?\s*a\.)"
)
_HEADER_LINE_MAX = 120
# A linha do contato no cabecalho comeca pelo contato (nunca e frase: "Penhora no imovel da Rua X" e do particular).
_CONTACT_START = re.compile(
    r"(?i)^[\s\-–—|•·]*(?:rua|r\.|av\.?|avenida|alameda|travessa|pra[cç]a|rodovia|estrada|largo|setor|quadra|sas|saus"
    r"|edif[ií]cio|ed\.|fone|tel\.?|telefone|fax|cep|e-?mail|endere[cç]o|\(?\d{2}\)?\s*\d{4})"
)
_HEADER_LOOKBACK = 4


def fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def is_generic_expression(value: str) -> bool:
    """Expressao comum de papel processual/societario, nao nome: "empresa executada", "a executada", "sociedade
    empresaria", "socio remanescente". Nome proprio no meio ("empresa Alfa") nao e generico."""

    words = _WORD.findall(fold(value))
    if not words:
        return False
    if all(word in GENERIC_WORDS for word in words):
        return True
    # Tudo em minuscula, comecando por um substantivo de papel: "empresa executada nos autos", "pessoa juridica
    # devedora". Nome proprio vem com maiuscula.
    raw = _WORD.findall(value)
    return (
        len(words) >= 2
        and not any(char.isupper() for char in value)
        and words[0] in _ROLE_HEADS
        and all(word.islower() for word in raw)
    )


def abbreviated_title_in_value(value: str) -> bool:
    """O trecho comeca pelo titulo do julgador, abreviado ou nao ("Min. X", "Rel. Min. X", "Des. Fed. X")."""

    return bool(_ABBREVIATED_TITLE.match(value))


def public_organ_contact(text: str, start: int, end: int, entity_type: str) -> bool:
    """Endereco, CEP, telefone ou e-mail no bloco de cabecalho de um orgao publico: as linhas curtas logo abaixo do nome
    do orgao (ate 4), sem pista de particular (CPF, residente, Ltda) na linha do contato."""

    if entity_type not in _CONTACT_TYPES:
        return False
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start : line_end if line_end >= 0 else len(text)]
    if len(line.strip()) > 160 or _PRIVATE_CUE.search(line):
        return False
    if _PUBLIC_ORGAN.match(line.strip()) and len(line.strip()) <= _HEADER_LINE_MAX + 40:
        return True  # "Procuradoria da Fazenda Nacional - Rua X, 100 - CEP"
    if not (_CONTACT_START.match(line) or not line[: start - line_start].strip()):
        return False
    cursor = line_start
    for _step in range(_HEADER_LOOKBACK):
        if cursor <= 0:
            return False
        previous_start = text.rfind("\n", 0, cursor - 1) + 1
        previous = text[previous_start : cursor - 1].strip()
        cursor = previous_start
        if not previous:
            continue  # linha em branco entre as linhas do cabecalho
        if len(previous) > _HEADER_LINE_MAX or previous.startswith("<!--"):
            return False
        if _PRIVATE_CUE.search(previous):
            return False
        if _PUBLIC_ORGAN.match(previous) or (len(previous) <= 60 and _PUBLIC_ORGAN.search(previous)):
            return True  # a linha do orgao comeca pelo nome dele ("Procuradoria...", "Nucleo... - PRFN/nR")
    return False


def misread(text: str, start: int, end: int, entity_type: str) -> str:
    """Motivo para o trecho NAO virar token ("" = nenhum)."""

    value = text[start:end]
    if entity_type in {"PERSON", "ORGANIZATION", "FAMILY_NAME"} and is_generic_expression(value):
        return "expressao_generica"
    if entity_type == "PERSON" and abbreviated_title_in_value(value):
        return "julgador_titulo_no_trecho"
    if public_organ_contact(text, start, end, entity_type):
        return "contato_orgao_publico"
    return ""

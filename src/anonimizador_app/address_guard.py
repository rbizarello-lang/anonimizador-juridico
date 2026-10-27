"""Guarda de tipo ENDERECO (v0.4.11): o que nao e endereco nao vira [ENDERECO_n].

Leitura cega do caso real (v0.4.10, 22/10/2026): o cartorio ("2º CRI de <cidade>"), o "protocolo nº ...", o titulo de
peca em caixa alta ("AUTO DE PENHORA") e a propria razao social da requerida sairam como [ENDERECO_n]. Tipo errado nao
tem conserto na revisao (o token ja disse "endereco"). Regras, para o span do LLM (``llm_hygiene``) e do motor
(``final_guard``):

- cartorio e registro publico (CRI, Registro de Imoveis, Tabelionato, Cartorio, Oficio de Registro, Serventia) nao e
  endereco;
- "protocolo nº ..." e numero de protocolo nao sao endereco;
- titulo de peca ou documento (AUTO DE PENHORA, CERTIDAO, MANDADO, TERMO, OFICIO, DECISAO...) nao e endereco;
- a razao social ou o nome de empresa conhecido no lote nao e endereco (vira a empresa);
- endereco exige marcador: logradouro (rua, av., travessa, rodovia, estrada...), numero predial com logradouro ou
  depois de virgula ("..., 123"), bairro, CEP, lote/quadra, km, apartamento/sala/bloco, "s/n".

Dado generico; nenhum dado de caso.
"""

from __future__ import annotations

import re

from anonimizador_juridico.pseudonymizer import fold_lexical

_REGISTRY = re.compile(
    r"(?i)(?<![^\W\d_])(?:\d{1,2}\s*[ºo°ª]?\s*)?(?:c\.?r\.?i\.?|r\.?i\.?)(?![^\W\d_])"
    r"|\b(?:registro\s+(?:geral\s+)?de\s+im[oó]veis|registro\s+de\s+t[ií]tulos|registro\s+civil|tabelionato"
    r"|tabeli[aã]o|cart[oó]rio|of[ií]cio\s+(?:de\s+)?(?:registro|notas|im[oó]veis)|serventia|servi[cç]o\s+registral"
    r"|junta\s+comercial)\b"
)
_PROTOCOL = re.compile(r"(?i)\bprotocolo\b|\bprot\.\s*n|\bn[º°o]\s*de\s+protocolo")
_DOCUMENT_TITLE = re.compile(
    r"(?i)^\W*(?:auto|certid[aã]o|mandado|termo|of[ií]cio|despacho|decis[aã]o|senten[cç]a|peti[cç][aã]o|laudo|edital"
    r"|ata|aviso|intima[cç][aã]o|cita[cç][aã]o|penhora|avalia[cç][aã]o|escritura|contrato|altera[cç][aã]o|ementa"
    r"|ac[oó]rd[aã]o|relat[oó]rio|voto|certifico|requerimento|procura[cç][aã]o|comprovante|extrato|recibo"
    r"|declara[cç][aã]o|notifica[cç][aã]o|guia|anexo|evento|documento)\b"
)
_MARKER = re.compile(
    r"(?i)(?<![^\W\d_])(?:rua|r\.|avenida|av\.|avn\.|ave\.|travessa|tv\.|alameda|al\.|rodovia|rod\.|estrada|pra[cç]a"
    r"|p[cç]\.|largo|servid[aã]o|beco|viela|via|bairro|b\.|cep|lote|lt\.|quadra|qd\.|gleba|ch[aá]cara|s[ií]tio"
    r"|fazenda|linha|localidade|distrito|loteamento|condom[ií]nio|residencial|edif[ií]cio|ed\.|apto\.?|apartamento"
    r"|sala|bloco|casa|km|vila|parque|jardim|setor|conjunto|n[uú]cleo|comunidade|esquina)(?![^\W\d_])"
    r"|\b(?:br|sc|sp|rs|pr|mg|rj|go|ba)-\s?\d{2,3}\b"
    r"|\bn[º°o.]\s*\d|\bn[uú]mero\s+\d|\bs/n[º°o]?\b"
    r"|\b\d{5}-?\d{3}\b|\[CEP_\d+\]"
    r"|,\s*\d{1,5}(?:\.\d{3})?\b"
)


def has_address_marker(value: str) -> bool:
    return bool(_MARKER.search(value))


def not_an_address(value: str, companies: frozenset[str] | set[str] = frozenset()) -> str:
    """Motivo para o valor NAO ser endereco ("" se pode ser). ``companies``: nomes de empresa do lote, dobrados (sem
    sufixo societario)."""

    compact = " ".join(value.split()).strip(" ,.;:-–")
    if not compact:
        return "endereco_vazio"
    if _REGISTRY.search(compact):
        return "endereco_cartorio"
    if _PROTOCOL.search(compact):
        return "endereco_protocolo"
    if _DOCUMENT_TITLE.match(compact) and not _MARKER.search(compact):
        return "endereco_titulo_documento"
    if companies and company_form(compact) in companies:
        return "endereco_empresa"
    if not _MARKER.search(compact):
        return "endereco_sem_marcador"
    return ""


_SOCIETARY_TAIL = re.compile(r"(?i)[\s,.\-]+(?:ltda|eireli|epp|me|s\.?\s*/?\s*a)\.?\s*$")


def company_form(value: str) -> str:
    """Forma de comparacao do nome de empresa: dobrado, sem sufixo societario, espacos simples."""

    bare = _SOCIETARY_TAIL.sub("", " ".join(value.split()))
    return " ".join(re.findall(r"[^\W_]+", fold_lexical(bare)))


# v0.4.11 (avaliador, sintetico): a assinatura "A. R. <Sobrenome> Neto" e o nome "MARIA M. R. SOUZA" viravam ENDERECO: o
# "R." era lido como "Rua". "R." (ou "R" solto) logo depois de uma inicial ("A.") ou de uma palavra de nome capitalizada,
# na mesma linha e sem virgula/dois-pontos/preposicao no meio, e abreviatura de prenome, nao logradouro.
_R_STREET = re.compile(r"(?i)^r\.?(?=\s)")
_NAME_BEFORE_R = re.compile(r"(?:(?<![^\W\d_])[A-ZÀ-Ý]\.|(?<![^\W\d_])[A-ZÀ-Ý][^\W\d_]+)[ \t]*$")
_NOT_NAME_BEFORE = re.compile(
    r"(?i)(?<![^\W\d_])(?:na|no|à|a|em|da|do|de|pela|pelo|sito|sita|situad[oa]|localizad[oa]|endere[cç]o|residente"
    r"|domiciliad[oa]|sede|av|rua|estabelecid[oa])[ \t]*$"
)


def initial_not_street(text: str, start: int) -> bool:
    """O endereco que comeca por "R." e na verdade uma inicial do meio de um nome ("A. R. Souza Neto")."""

    if not _R_STREET.match(text[start : start + 3]):
        return False
    line_start = text.rfind("\n", 0, start) + 1
    before = text[max(line_start, start - 40) : start]
    return bool(_NAME_BEFORE_R.search(before)) and not _NOT_NAME_BEFORE.search(before)

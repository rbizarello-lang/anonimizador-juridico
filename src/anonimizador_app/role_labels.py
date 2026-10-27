"""Cargo e rotulo de qualificacao que nunca e nome (H9-h): "SOCIO ADMINISTRADOR", "SOCIA ADMINISTRADORA",
"ADMINISTRADOR(ES)", "socio-administrador".

Medido no lote IDPJ real depois da H9-g: "SOCIO ADMINISTRADOR", a qualificacao que o contrato social escreve ao lado do
nome, virou [PESSOA_n]. O texto formado so por palavras de cargo de sociedade (socio, administrador, no singular, plural
ou feminino, com "(es)" e "(a)" e hifen) e o rotulo da qualificacao, nunca o nome de alguem. Com uma palavra de nome junto
("Maria Socia"), deixa de ser so rotulo e a regra nao vale.
"""

from __future__ import annotations

import re

from anonimizador_juridico.pseudonymizer import fold_lexical

ROLE_LABEL_WORDS = frozenset(
    "socio socia socios socias administrador administradora administradores administradoras".split()
    # H9-j: qualidade processual (no teste de 16/10, "Requerida" virou empresa e "requerida e agravante" virou pessoa).
    + """requerida requerido requeridas requeridos requerente requerentes agravante agravantes agravada agravado
    agravadas agravados executada executado executadas executados exequente exequentes embargante embargantes
    embargada embargado embargadas embargados impugnante impugnantes impugnada impugnado excipiente excipientes
    excepta excepto recorrente recorrentes recorrida recorrido apelante apelantes apelada apelado autora autoras
    reu reus interessada interessado suscitante suscitada suscitado""".split()
)
# H9-j: artigo e "ora" ("A REQUERIDA", "requerida e ora agravante") tambem ligam as palavras do rotulo.
_LINK_WORDS = frozenset({"e", "de", "da", "do", "das", "dos", "a", "o", "as", "os", "ora"})
_PLURAL_MARK = re.compile(r"\((?:a|as|es|s|is|o|os)\)")
_WORD = re.compile(r"[^\W\d_]+")


def is_role_label(value: str) -> bool:
    """O texto e so cargo de socio ou administrador (com conectivos), sem nenhuma palavra de nome."""

    words = _WORD.findall(_PLURAL_MARK.sub("", fold_lexical(value)))
    if not any(word in ROLE_LABEL_WORDS for word in words):
        return False
    return all(word in ROLE_LABEL_WORDS or word in _LINK_WORDS for word in words)


# --- rotulos fixos de documento e sufixos societarios soltos (H9-i) ------------------------------------------------
# "CPF/CNPJ:", "CPF/MF", "CNPJ/MF", "CPF ou CNPJ" sem numero ao lado sao o rotulo do campo, nunca pessoa nem empresa.
# "LTDA", "EPP", "ME", "S.A.", "S/A", "EIRELI", "MEI" sozinhos tambem nunca sao entidade: o sufixo so entra no token
# junto da razao social.
_DOC_WORDS = frozenset({"cpf", "cnpj", "mf", "cic", "rg", "cnh"})
_DOC_LINKS = frozenset({"e", "ou", "de", "do", "da", "no", "na", "n", "nº", "no.", "numero", "num", "inscricao"})
_SOCIETARY_ALONE = re.compile(
    r"(?i)^\s*(?:ltda|epp|eireli|mei|me|ei|slu|spe|s\s*[/.]?\s*[as]|s\.\s*[as]|sa|cia|companhia|sociedade\s+an[oô]nima)\s*[.,;:]*\s*$"
)


def is_fixed_label(value: str) -> bool:
    """O texto e so o rotulo de documento ("CPF/CNPJ", "CPF ou CNPJ:", "CNPJ/MF") ou so um sufixo societario
    ("LTDA", "S/A", "ME", "EPP", "EIRELI", "MEI"): nao e entidade."""

    if _SOCIETARY_ALONE.match(value):
        return True
    words = _WORD.findall(fold_lexical(value).replace("/", " "))
    if not words or not any(word in _DOC_WORDS for word in words):
        return False
    return all(word in _DOC_WORDS or word in _DOC_LINKS for word in words)

"""Sintaxe dos tokens do exportavel, num lugar so.

Token: ``[ROTULO_n]`` (``[EMPRESA_3]``). Token ligado a outros no ``ELENCO.md`` (cadeia de mudancas de
nome, mesmo CNPJ nas capas) leva o sufixo de grupo ``·Gn`` NO PROPRIO TEXTO: ``[EMPRESA_3·G1]``,
``[EMPRESA_7·G1]``. Os tokens continuam separados (cada um com a sua numeracao e o seu nome); o sufixo
so diz que o ``ELENCO.md`` os liga, e a legenda dele explica o grupo.

Quem le token no texto exportado aceita o sufixo opcional (``GROUP``) e, para identificar a entidade,
ignora-o (``strip_groups``); o mapa privado so tem ``[EMPRESA_3]``.
"""

from __future__ import annotations

import re

GROUP_MARK = "·G"
# Parte opcional do sufixo, para dentro de uma regex de token: ``\[EMPRESA_\d+`` + GROUP + ``\]``.
GROUP = r"(?:·G\d+)?"
# Rotulo em maiuscula (com acento) e numero, sem o sufixo: ``[PESSOA_12]``.
LABEL = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9_]+?"
_GROUP_SUFFIX = re.compile(r"(\[[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9_]+_\d+)·G\d+\]")


def strip_groups(text: str) -> str:
    """O texto com o sufixo de grupo tirado dos tokens: ``[EMPRESA_3·G1]`` vira ``[EMPRESA_3]``."""

    return _GROUP_SUFFIX.sub(r"\1]", text)


def with_group(token: str, group: int | None) -> str:
    """``[EMPRESA_3]`` e 1 -> ``[EMPRESA_3·G1]``; sem grupo, o token como esta."""

    base = strip_groups(token)
    return base if not group else f"{base[:-1]}{GROUP_MARK}{group}]"

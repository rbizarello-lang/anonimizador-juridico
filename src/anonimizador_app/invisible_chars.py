"""Caracteres invisiveis do PDF (H9-j): o espaco de largura zero vira espaco; o que fica dentro da palavra sai.

Medido no teste de 16/10/2026 (lote IDPJ de 130 folhas): 45 folhas tinham ~29.800 U+200B, e em ~5 mil pontos ele era
o UNICO separador entre duas palavras ("Incidente​​de​​Desconsideracao"). O detector e o LLM liam
"Incidentede..." como uma palavra so: regras com rotulo (CDA, placa, processo) falhavam e a Gemma respondia vazio em
38% dessas folhas (4% nas demais). Apagar o caractere colaria as palavras; por isso:

- U+200B (espaco de largura zero) e U+2060 (word joiner) -> um espaco; espacos repetidos na linha viram um so;
- U+200C, U+200D (ZWNJ/ZWJ), U+FEFF (BOM) e U+00AD (hifen invisivel) -> removidos (ficam dentro da palavra).

Quebras de linha, ancoras e tabelas nao mudam: so o que estava na mesma linha.
"""

from __future__ import annotations

import re

_SPACE_LIKE = re.compile(r"[ \t]*[​⁠]+[ \t]*(?:[​⁠]+[ \t]*)*")
_REMOVE = re.compile(r"[‌‍﻿­]")
_ANY = re.compile(r"[​‌‍⁠﻿­]")
_LINE_EDGE = re.compile(r"^[ \t]+|[ \t]+$", re.MULTILINE)


def has_invisible(text: str) -> bool:
    return bool(_ANY.search(text or ""))


def count_invisible(text: str) -> int:
    return len(_ANY.findall(text or ""))


def normalize_invisible(text: str) -> str:
    """O texto sem os invisiveis, com as palavras que eles separavam separadas por um espaco."""

    if not has_invisible(text):
        return text
    text = _REMOVE.sub("", text)
    out: list[str] = []
    for line in text.split("\n"):
        if "​" in line or "⁠" in line:
            # Indentacao no comeco da linha (lista, tabela) fica; o resto vira um espaco so.
            stripped = line.lstrip(" \t​⁠")
            lead = line[: len(line) - len(stripped)].replace("​", "").replace("⁠", "")
            body = _SPACE_LIKE.sub(" ", stripped)
            line = lead + body.rstrip(" ") if body.strip() else lead.rstrip(" ")
        out.append(line)
    return "\n".join(out)

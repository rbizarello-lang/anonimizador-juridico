"""Conta, por entidade de um gabarito privado, quantas mencoes ficaram em claro em cada lote.

Uso:
    python tools/medir_vazamento.py <gabarito.json> <pasta_do_lote> [<outra_pasta_de_lote> ...]

O gabarito tem dados reais e fica so na pasta privada (``saidas/privados/gabaritos``),
fora do git. Formato:

    {"entidades": [{"categoria": "PESSOA-parte", "rotulo": "socio A", "padroes": ["nome completo", ...]}],
     "ids": [{"categoria": "ID", "rotulo": "CPF socio A", "valor": "000.000.000-00"}]}

A saida so traz o rotulo e as contagens. Categoria AGENTE-PUBLICO deve ficar em claro
(item H): ali, contagem alta e o esperado.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def _phrase(pattern: str) -> re.Pattern[str]:
    words = [re.escape(_fold(word)) for word in pattern.split()]
    return re.compile(r"(?<![a-z0-9])" + r"[\s/|*_-]+".join(words) + r"(?![a-z0-9])")


def _identifier(value: str) -> re.Pattern[str]:
    if re.fullmatch(r"[A-Za-z]{3}\d[A-Za-z0-9]\d{2}", value):
        return re.compile(rf"(?<![A-Za-z0-9]){value[:3]}[\s-]?{value[3:]}(?![A-Za-z0-9])", re.I)
    digits = re.sub(r"\D", "", value)
    return re.compile(r"(?<!\d)" + r"[\s.\-/]?".join(digits) + r"(?!\d)")


def _count(text: str, patterns: list[re.Pattern[str]], folded: bool) -> int:
    haystack = _fold(text) if folded else text
    return len({match.start() for pattern in patterns for match in pattern.finditer(haystack)})


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    gabarito = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    lotes = [Path(item) for item in argv[2:]]
    texts = {
        lote.name: "\n".join(path.read_text(encoding="utf-8") for path in sorted(lote.glob("*.md")))
        for lote in lotes
    }
    header = "".join(f"{name[-15:]:>17s}" for name in texts)
    print(f"{'categoria':18s} {'entidade':44s}{header}")
    for entity in gabarito.get("entidades", []):
        patterns = [_phrase(pattern) for pattern in entity["padroes"]]
        counts = "".join(f"{_count(text, patterns, True):17d}" for text in texts.values())
        print(f"{entity['categoria'][:18]:18s} {entity['rotulo'][:44]:44s}{counts}")
    for item in gabarito.get("ids", []):
        pattern = [_identifier(item["valor"])]
        counts = "".join(f"{_count(text, pattern, False):17d}" for text in texts.values())
        print(f"{item['categoria'][:18]:18s} {item['rotulo'][:44]:44s}{counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

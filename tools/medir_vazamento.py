"""Conta, por entidade de um gabarito privado, quantas mencoes ficaram em claro em cada lote.

Uso:
    python tools/medir_vazamento.py <gabarito.json> <pasta_do_lote> [<outra_pasta_de_lote> ...]

O gabarito tem dados reais e fica so na pasta privada (``saidas/privados/gabaritos``),
fora do git. Formato:

    {"entidades": [{"categoria": "PESSOA-parte", "rotulo": "socio A", "padroes": ["nome completo", ...]}],
     "ids": [{"categoria": "ID", "rotulo": "CPF socio A", "valor": "000.000.000-00"}]}

A saida so traz o rotulo e as contagens. Categoria AGENTE-PUBLICO deve ficar em claro
(item H): ali, contagem alta e o esperado.

Gabarito revisado (``montar_gabarito.py``) traz ``acao`` por entidade; entao a tabela sai em dois blocos,
"devem ser trocadas" (acao=tokenizar: contagem alta = vazamento) e "devem ficar em claro" (acao=manter:
contagem zero = tokenizacao indevida), cada um ordenado por categoria e fechado por um resumo por
categoria e lote: quantas entidades ficaram com zero mencoes em claro e quantas com alguma. Gabarito sem
``acao`` sai como antes, num bloco so.
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


def _rows(gabarito: dict, texts: dict[str, str]) -> list[tuple[dict, list[int]]]:
    """Cada entidade e ID do gabarito com suas contagens por lote, na ordem do arquivo."""

    rows: list[tuple[dict, list[int]]] = []
    for entity in gabarito.get("entidades", []):
        patterns = [_phrase(pattern) for pattern in entity["padroes"]]
        rows.append((entity, [_count(text, patterns, True) for text in texts.values()]))
    for item in gabarito.get("ids", []):
        pattern = [_identifier(item["valor"])]
        rows.append((item, [_count(text, pattern, False) for text in texts.values()]))
    return rows


def _print_table(rows: list[tuple[dict, list[int]]], texts: dict[str, str]) -> None:
    header = "".join(f"{name[-15:]:>17s}" for name in texts)
    print(f"{'categoria':18s} {'entidade':44s}{header}")
    for item, counts in rows:
        cells = "".join(f"{count:17d}" for count in counts)
        print(f"{item['categoria'][:18]:18s} {item['rotulo'][:44]:44s}{cells}")


def summary(rows: list[tuple[dict, list[int]]], lotes: list[str]) -> list[tuple[str, str, int, int]]:
    """Por categoria e lote: (categoria, lote, entidades com zero mencoes em claro, com alguma)."""

    result: list[tuple[str, str, int, int]] = []
    for categoria in sorted({item["categoria"] for item, _counts in rows}):
        group = [counts for item, counts in rows if item["categoria"] == categoria]
        for index, lote in enumerate(lotes):
            zero = sum(1 for counts in group if counts[index] == 0)
            result.append((categoria, lote, zero, len(group) - zero))
    return result


def _print_summary(rows: list[tuple[dict, list[int]]], texts: dict[str, str]) -> None:
    print(f"RESUMO por categoria ({len(rows)} entidades): zero mencoes em claro / com alguma")
    for categoria, lote, zero, some in summary(rows, list(texts)):
        print(f"  {categoria[:28]:28s} {lote[-15:]:>17s}   zero: {zero:4d}   alguma: {some:4d}")


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
    rows = _rows(gabarito, texts)
    if not any("acao" in item for item, _counts in rows):
        _print_table(rows, texts)
        return 0
    # Gabarito revisado: a acao decidida pelo Operador manda, nao a categoria antiga.
    blocks = (
        ("DEVEM SER TROCADAS (acao=tokenizar): contagem alta = vazamento",
         [row for row in rows if row[0].get("acao", "tokenizar") != "manter"]),
        ("DEVEM FICAR EM CLARO (acao=manter): contagem ZERO = tokenizacao indevida",
         [row for row in rows if row[0].get("acao") == "manter"]),
    )
    for title, block in blocks:
        block.sort(key=lambda row: row[0]["categoria"])
        print(f"== {title} ==")
        _print_table(block, texts)
        _print_summary(block, texts)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

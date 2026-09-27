"""ELENCO.md do lote (fila 3.x E5): quem e quem, sem nomes.

Os papeis vem da tabela "Partes e Representantes" da capa do eproc de cada processo
(REQUERENTE/REQUERIDO, EXEQUENTE/EXECUTADO, PERITO, UNIDADE EXTERNA...), ja tokenizada.
A legenda so escreve tokens e entes publicos ou instituicoes (Uniao, cartorio): parte que
ficou em claro no texto nao tem o nome copiado para ca. Depois dos papeis, toda pessoa e
empresa tokenizada do lote, com o numero de ocorrencias e os arquivos em que aparece.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

CAST_NAME = "ELENCO.md"

_TOKEN = re.compile(r"\[([A-Z_]+)_\d+\]")
_PARTY_TOKEN = re.compile(r"\[(?:PESSOA|EMPRESA)_\d+\]")
_PARTIES = re.compile(r"Partes e Representantes\s*\n\s*\n((?:\|[^\n]*\n)+)")
_PROCESS = re.compile(r"\[PROCESSO_\d+\]")
_CLASS = re.compile(r"Classe da ação:\s*\n\s*(.+)")
_INSTITUTION = re.compile(
    r"(?i)^(?:uni[aã]o|fazenda|minist[eé]rio|inss|cart[oó]rio|of[ií]cio|tabelionato|ju[ií]zo|"
    r"receita|procuradoria|estado|munic[ií]pio)"
)
_KIND = re.compile(r"-\s*(Pessoa Jur[ií]dica|Pessoa F[ií]sica|Entidade)")
_REPRESENTATIVES = re.compile(r"(?:Procurador\(es\):|Entidade)\s*(.*)$")
_LABELS = {"PESSOA": "pessoa", "EMPRESA": "empresa"}


def build_cast(documents: Sequence[tuple[str, str]]) -> str:
    """``documents``: (nome do .md, texto tokenizado), na ordem do lote."""

    occurrences: Counter[str] = Counter()
    files: dict[str, set[str]] = defaultdict(set)
    for name, text in documents:
        for match in _PARTY_TOKEN.finditer(text):
            occurrences[match.group(0)] += 1
            files[match.group(0)].add(name)
    lines = [
        "# Elenco do lote (sem nomes)",
        "",
        "Papéis tirados da tabela “Partes e Representantes” da capa de cada processo. Os "
        "nomes não estão aqui: só os tokens do texto. O mesmo token é a mesma pessoa ou "
        "empresa em todos os arquivos. Parte que a capa traz sem token aparece como "
        "“parte sem token no texto”.",
        "",
    ]
    listed: set[str] = set()
    for name, text in documents:
        for table in _PARTIES.finditer(text):
            process = _PROCESS.findall(text[: table.start()])
            label = process[-1] if process else name
            kind = _CLASS.search(text[: table.start()])
            title = f"## Processo {label}" + (f" — {kind.group(1).strip()}" if kind else "")
            lines += [title, "", f"Capa em {name}.", ""]
            lines += ["| Token | Papel | Tipo | Representado por | Ocorrências no lote |", "|---|---|---|---|---|"]
            represented: dict[str, list[str]] = defaultdict(list)
            parties = _parse_parties(table.group(1))
            for party, role, party_kind, representatives in parties:
                count = occurrences.get(party, "")
                reps = ", ".join(representatives)
                lines.append(f"| {party} | {role} | {party_kind} | {reps} | {count} |")
                listed.add(party)
                for representative in representatives:
                    represented[representative].append(party)
            party_tokens = {party for party, *_rest in parties}
            for representative, clients in represented.items():
                if representative in party_tokens:
                    continue
                lines.append(
                    f"| {representative} | procurador(a) de {', '.join(dict.fromkeys(clients))} | "
                    f"pessoa |  | {occurrences.get(representative, '')} |"
                )
                listed.add(representative)
            lines.append("")
    rest = [token for token, _count in occurrences.most_common() if token not in listed]
    if rest:
        lines += [
            "## Demais pessoas e empresas do lote",
            "",
            "Papel não informado na capa; o contexto de cada ocorrência diz o papel.",
            "",
            "| Token | Tipo | Ocorrências | Arquivos |",
            "|---|---|---|---|",
        ]
        for token in rest:
            kind = _LABELS.get(_TOKEN.match(token).group(1), "")
            names = ", ".join(sorted(files[token]))
            lines.append(f"| {token} | {kind} | {occurrences[token]} | {names} |")
    return "\n".join(lines).rstrip() + "\n"


def write_lote_cast(export_dir: Path, markdown_paths: Sequence[Path]) -> Path:
    documents = [(path.name, path.read_text(encoding="utf-8")) for path in markdown_paths]
    target = Path(export_dir) / CAST_NAME
    target.write_text(build_cast(documents), encoding="utf-8")
    return target


def _parse_parties(table: str) -> list[tuple[str, str, str, list[str]]]:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in table.splitlines()
        if line.strip().startswith("|") and not line.strip().startswith("|---")
    ]
    if not rows:
        return []
    roles = [cell.lower() for cell in rows[0]]
    section: str | None = None
    found: list[tuple[str, str, str, list[str]]] = []
    for row in rows[1:]:
        filled = [cell for cell in row if cell]
        if len(filled) == 1 and row[0] and row[0].isupper() and not _TOKEN.search(row[0]):
            section = row[0].lower()
            continue
        for index, cell in enumerate(row):
            if not cell:
                continue
            role = section or (roles[index] if index < len(roles) else "")
            found.append((*_party(cell), *_rest(cell, role)))
    return [(party, role, kind, reps) for party, kind, role, reps in found]


def _party(cell: str) -> tuple[str, str]:
    head = re.split(r"\s+\(|\s+-\s+(?=Pessoa|Entidade)|\s+Procurador\(es\):", cell, maxsplit=1)[0].strip()
    tokens = _PARTY_TOKEN.findall(head)
    if tokens and head.startswith(tokens[0]):
        party = tokens[0]
    elif _INSTITUTION.match(head):
        party = head
    else:
        party = "parte sem token no texto"
    kind = _KIND.search(cell)
    return party, kind.group(1).lower() if kind else ""


def _rest(cell: str, role: str) -> tuple[str, list[str]]:
    representatives = _REPRESENTATIVES.search(cell)
    tokens = _PARTY_TOKEN.findall(representatives.group(1)) if representatives else []
    return role, list(dict.fromkeys(tokens))

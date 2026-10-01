"""ELENCO.md do lote (fila 3.x E5, E13): quem e quem, sem nomes.

Os papeis vem da tabela "Partes e Representantes" da capa de TODO processo do lote
(REQUERENTE/REQUERIDO, EXEQUENTE/EXECUTADO, AGRAVANTE/AGRAVADO, PERITO, UNIDADE EXTERNA...), ja
tokenizada. A legenda so escreve tokens e entes publicos ou instituicoes (Uniao, cartorio): parte
que ficou em claro no texto nao tem o nome copiado para ca. Depois dos papeis, os vinculos entre
tokens (o mesmo CNPJ/CPF nas capas; a denominacao anterior, quando um documento diz que o nome
mudou) e toda pessoa e empresa tokenizada do lote, com o numero de ocorrencias, os arquivos e o
papel que o cabecalho das pecas da ("EXECUTADO: [EMPRESA_2]") para quem nao esta em capa.

Os tokens nao sao unificados aqui: o vinculo e dito, nao decidido.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

CAST_NAME = "ELENCO.md"

_TOKEN = re.compile(r"\[([A-Z_]+)_\d+\]")
_PARTY_TOKEN = re.compile(r"\[(?:PESSOA|EMPRESA)_\d+\]")
_ID_TOKEN = re.compile(r"\[(?:CNPJ|CPF)_\d+\]")
_PARTIES = re.compile(r"Partes e Representantes[ \t]*\n+((?:\|[^\n]*\n)+)")
_PROCESS = re.compile(r"\[PROCESSO_\d+\]")
_PROCESS_NUMBER = re.compile(r"N[ºo°.]{0,2}\s*do processo:?\s*(\[PROCESSO_\d+\])")
_CLASS = re.compile(r"Classe da ação:[ \t]*\n?[ \t]*([^\n|]+)")
_FOLHA = re.compile(r"\[fl\. (\d+)")
_INSTITUTION = re.compile(
    r"(?i)^(?:uni[aã]o|fazenda|minist[eé]rio|inss|cart[oó]rio|of[ií]cio|tabelionato|ju[ií]zo|"
    r"receita|procuradoria|estado|munic[ií]pio)"
)
_KIND = re.compile(r"-\s*(Pessoa Jur[ií]dica|Pessoa F[ií]sica|Entidade)")
_REPRESENTATIVES = re.compile(r"(?:Procurador\(es\):|Entidade)\s*(.*)$")
_LABELS = {"PESSOA": "pessoa", "EMPRESA": "empresa"}

# Papel no cabecalho da peca: "EXECUTADO: [EMPRESA_2]" no inicio da linha.
_HEADER_ROLE = re.compile(
    r"(?im)^[ \t>*_|-]*(EXEQUENTE|EXECUTAD[OA]S?|REQUERENTE|REQUERID[OA]S?|AGRAVANTE|AGRAVAD[OA]S?|"
    r"EMBARGANTE|EMBARGAD[OA]S?|RECORRENTE|RECORRID[OA]S?|APELANTE|APELAD[OA]S?|AUTOR(?:A|ES)?|"
    r"R[ÉE]U|R[ÉE]|IMPETRANTE|IMPETRAD[OA]S?|INTERESSAD[OA]S?)[ \t]*:[ \t]*"
    r"((?:\[(?:PESSOA|EMPRESA)_\d+\][ \t,;e]*)+)"
)
_ROLE_NAMES = {
    "exequente": "exequente", "requerente": "requerente", "agravante": "agravante",
    "embargante": "embargante", "recorrente": "recorrente", "apelante": "apelante",
    "impetrante": "impetrante", "réu": "réu/ré", "reu": "réu/ré", "ré": "réu/ré", "re": "réu/ré",
}
_ROLE_STEMS = (
    ("executad", "executado(a)"), ("requerid", "requerido(a)"), ("agravad", "agravado(a)"),
    ("embargad", "embargado(a)"), ("recorrid", "recorrido(a)"), ("apelad", "apelado(a)"),
    ("impetrad", "impetrado(a)"), ("interessad", "interessado(a)"), ("autor", "autor(a)"),
)

# Mudanca de nome dita no documento. So empresa com empresa.
_COMPANY = r"\[EMPRESA_\d+\]"
_RENAME_PAIR = re.compile(
    rf"(?is)NOME\s+INICIAL\s*:\s*({_COMPANY})[^\[]{{0,60}}?ALTERAD[OA]\s+PARA\s*:?\s*({_COMPANY})"
)
_RENAME_STEP = re.compile(
    rf"(?is)altera[çc][ãa]o\s+d[oa]\s+(?:nome\s+social|denomina[çc][ãa]o(?:\s+social)?|raz[ãa]o\s+social|"
    rf"nome\s+empresarial)\s+para\s*:?\s*({_COMPANY})"
)
_RENAME_FORMER = re.compile(
    rf"(?is)({_COMPANY})[^.\[]{{0,40}}?\b(?:antiga|anterior|ex)\w*[\s-]+(?:denomina\w+|raz[ãa]o\s+social|"
    rf"nome(?:\s+empresarial|\s+social)?)\s*:?\s*(?:era\s+)?({_COMPANY})"
)
_RENAME_NOW = re.compile(
    rf"(?is)({_COMPANY})[^.\[]{{0,40}}?(?:atualmente\s+denominad[ao]|hoje\s+denominad[ao]|"
    rf"passou\s+a\s+(?:se\s+)?denominar-?\s*se|passando\s+a\s+(?:se\s+)?denominar-?\s*se|"
    rf"alterou\s+(?:sua|a)\s+(?:denomina\w+|raz[ãa]o\s+social)\s+para|"
    rf"atual\s+(?:denomina\w+|raz[ãa]o\s+social))\s*:?\s*({_COMPANY})"
)
_STEP_GAP = 900


def build_cast(documents: Sequence[tuple[str, str]]) -> str:
    """``documents``: (nome do .md, texto tokenizado), na ordem do lote."""

    occurrences: Counter[str] = Counter()
    files: dict[str, set[str]] = defaultdict(set)
    for name, text in documents:
        for match in _PARTY_TOKEN.finditer(text):
            occurrences[match.group(0)] += 1
            files[match.group(0)].add(name)
    header_roles = _header_roles(documents)
    sections: list[str] = []
    listed: set[str] = set()
    cover_ids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for name, text in documents:
        for table in _PARTIES.finditer(text):
            before = text[: table.start()]
            process = _PROCESS_NUMBER.findall(before) or _PROCESS.findall(before)
            label = process[-1] if process else name
            kinds = _CLASS.findall(before)
            title = f"## Processo {label}" + (f" — {kinds[-1].strip()}" if kinds else "")
            lines = [title, "", f"Capa em {name}.", ""]
            lines += ["| Token | Papel | Tipo | Representado por | Ocorrências no lote |", "|---|---|---|---|---|"]
            represented: dict[str, list[str]] = defaultdict(list)
            parties = _parse_parties(table.group(1))
            for party, role, party_kind, representatives, ids in parties:
                count = occurrences.get(party, "")
                reps = ", ".join(representatives)
                lines.append(f"| {party} | {role} | {party_kind} | {reps} | {count} |")
                listed.add(party)
                for identifier in ids:
                    cover_ids[identifier][party].add(label)
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
            sections.append("\n".join(lines))
    covers = len(sections)
    out = [
        "# Elenco do lote (sem nomes)",
        "",
        "Papéis tirados da tabela “Partes e Representantes” da capa de cada processo "
        f"({covers} capa(s) lida(s) neste lote). Os nomes não estão aqui: só os tokens do texto. "
        "O mesmo token é a mesma pessoa ou empresa em todos os arquivos. Parte que a capa traz sem "
        "token aparece como “parte sem token no texto”. Quem não está em capa leva, na última seção, "
        "o papel que o cabeçalho das peças lhe dá, quando há.",
        "",
    ]
    links = _links(documents, cover_ids)
    if links:
        out += [
            "## Vínculos entre tokens",
            "",
            "Mais de um token para a mesma pessoa ou empresa, **só quando o próprio texto diz** "
            "(CNPJ ou CPF igual nas capas; documento que registra a mudança de nome). Os tokens "
            "não foram unificados: cada um mantém a numeração e a contagem dele.",
            "",
            "| Token | Vínculo | Token | Evidência |",
            "|---|---|---|---|",
        ]
        out += [f"| {a} | {kind} | {b} | {evidence} |" for a, kind, b, evidence in links]
        out.append("")
    out += sections
    rest = [token for token, _count in occurrences.most_common() if token not in listed]
    if rest:
        out += [
            "## Demais pessoas e empresas do lote",
            "",
            "Papel não informado na capa; o contexto de cada ocorrência diz o papel. “Papel no "
            "cabeçalho das peças” é o que consta em linhas como “EXECUTADO: [token]” (número de linhas).",
            "",
            "| Token | Tipo | Ocorrências | Papel no cabeçalho das peças | Arquivos |",
            "|---|---|---|---|---|",
        ]
        for token in rest:
            kind = _LABELS.get(_TOKEN.match(token).group(1), "")
            names = ", ".join(sorted(files[token]))
            roles = _role_list(header_roles.get(token))
            out.append(f"| {token} | {kind} | {occurrences[token]} | {roles} | {names} |")
    return "\n".join(out).rstrip() + "\n"


def write_lote_cast(export_dir: Path, markdown_paths: Sequence[Path]) -> Path:
    documents = [(path.name, path.read_text(encoding="utf-8")) for path in markdown_paths]
    target = Path(export_dir) / CAST_NAME
    target.write_text(build_cast(documents), encoding="utf-8")
    return target


def cast_summary(cast: str) -> dict[str, int]:
    """Contagens do ELENCO.md pronto: capas lidas, tokens com papel de capa, de cabecalho, vinculos."""

    covers = len(re.findall(r"(?m)^## Processo ", cast))
    cover_roles: set[str] = set()
    for section in re.split(r"(?m)^## ", cast):
        if not section.startswith("Processo "):
            continue
        for line in section.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if line.startswith("|") and len(cells) >= 2 and _PARTY_TOKEN.fullmatch(cells[0]) and cells[1]:
                cover_roles.add(cells[0])
    header_roles: set[str] = set()
    rest = cast.split("## Demais pessoas e empresas do lote", 1)
    if len(rest) == 2:
        for line in rest[1].splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if line.startswith("|") and len(cells) >= 5 and _PARTY_TOKEN.fullmatch(cells[0]) and cells[3]:
                header_roles.add(cells[0])
    links = 0
    block = cast.split("## Vínculos entre tokens", 1)
    if len(block) == 2:
        part = re.split(r"(?m)^## ", block[1])[0]
        links = sum(1 for line in part.splitlines() if line.startswith("| [") and not line.startswith("|---"))
    return {
        "capas": covers,
        "tokens_com_papel_de_capa": len(cover_roles),
        "tokens_com_papel_de_cabecalho": len(header_roles),
        "vinculos": links,
    }


def _normalize_role(raw: str) -> str:
    word = raw.lower()
    if word in _ROLE_NAMES:
        return _ROLE_NAMES[word]
    for stem, role in _ROLE_STEMS:
        if word.startswith(stem):
            return role
    return word


def _header_roles(documents: Sequence[tuple[str, str]]) -> dict[str, Counter[str]]:
    found: dict[str, Counter[str]] = defaultdict(Counter)
    for _name, text in documents:
        for match in _HEADER_ROLE.finditer(text):
            role = _normalize_role(match.group(1))
            for token in _PARTY_TOKEN.findall(match.group(2)):
                found[token][role] += 1
    return found


def _role_list(roles: Counter[str] | None) -> str:
    if not roles:
        return ""
    return ", ".join(f"{role} ({count})" for role, count in roles.most_common())


def _links(
    documents: Sequence[tuple[str, str]],
    cover_ids: dict[str, dict[str, set[str]]],
) -> list[tuple[str, str, str, str]]:
    """(token, vinculo, token, evidencia): so o que o texto diz, nunca por semelhanca de nome."""

    links: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(first: str, kind: str, second: str, evidence: str) -> None:
        if first == second or (first, kind, second) in seen:
            return
        seen.add((first, kind, second))
        links.append((first, kind, second, evidence))

    # Mudanca de nome: o documento registra "nome inicial ... alterado para ...", ou a sequencia de
    # alteracoes do nome social, ou "antiga denominacao"/"atualmente denominada".
    renames: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, text in documents:
        for match in _RENAME_PAIR.finditer(text):
            renames[(match.group(1), match.group(2))].append(
                f"alteração contratual “nome inicial … alterado para …” ({_where(name, text, match.start())})"
            )
        steps = list(_RENAME_STEP.finditer(text))
        for earlier, later in zip(steps, steps[1:]):
            if later.start() - earlier.end() <= _STEP_GAP and earlier.group(1) != later.group(1):
                renames[(earlier.group(1), later.group(1))].append(
                    f"alterações sucessivas do nome social ({_where(name, text, earlier.start())})"
                )
        for match in _RENAME_FORMER.finditer(text):
            renames[(match.group(2), match.group(1))].append(
                f"“antiga denominação” no texto ({_where(name, text, match.start())})"
            )
        for match in _RENAME_NOW.finditer(text):
            renames[(match.group(1), match.group(2))].append(
                f"“denominação atual” no texto ({_where(name, text, match.start())})"
            )
    for (old, new), evidence in renames.items():
        shown = "; ".join(dict.fromkeys(evidence[:2]))
        add(new, "antiga denominação", old, shown)
        add(old, "denominação posterior", new, shown)

    # Mesmo CNPJ/CPF nas capas: a mesma pessoa, ainda que o nome tenha sido lido de outro jeito.
    for identifier, parties in cover_ids.items():
        tokens = sorted(parties, key=_token_order)
        for index, first in enumerate(tokens):
            for second in tokens[index + 1 :]:
                where = ", ".join(sorted(parties[first] | parties[second]))
                add(first, "mesmo " + ("CNPJ" if identifier.startswith("[CNPJ") else "CPF"), second,
                    f"{identifier} nas capas dos processos {where}")
    return links


def _where(name: str, text: str, position: int) -> str:
    folha = _FOLHA.findall(text[:position])
    return f"{name}, fl. {folha[-1]}" if folha else name


def _token_order(token: str) -> tuple[str, int]:
    match = re.match(r"\[([A-Z_]+)_(\d+)\]", token)
    return (match.group(1), int(match.group(2))) if match else (token, 0)


def _parse_parties(table: str) -> list[tuple[str, str, str, list[str], list[str]]]:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in table.splitlines()
        if line.strip().startswith("|") and not line.strip().startswith("|---")
    ]
    if not rows:
        return []
    roles = [cell.lower() for cell in rows[0]]
    section: str | None = None
    found: list[tuple[str, str, str, list[str], list[str]]] = []
    for row in rows[1:]:
        filled = [cell for cell in row if cell]
        if len(filled) == 1 and row[0] and row[0].isupper() and not _TOKEN.search(row[0]):
            section = row[0].lower()
            continue
        for index, cell in enumerate(row):
            if not cell:
                continue
            role = section or (roles[index] if index < len(roles) else "")
            party, kind = _party(cell)
            _role, representatives = _rest(cell, role)
            found.append((party, role, kind, representatives, _ids(cell)))
    return found


def _ids(cell: str) -> list[str]:
    head = re.split(r"\s+-\s+(?=Pessoa|Entidade)|\s+Procurador\(es\):", cell, maxsplit=1)[0]
    return list(dict.fromkeys(_ID_TOKEN.findall(head)))


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

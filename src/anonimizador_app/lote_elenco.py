"""ELENCO.md do lote (fila 3.x E5, E13): quem e quem, sem nomes.

Os papeis vem da tabela "Partes e Representantes" da capa de TODO processo do lote
(REQUERENTE/REQUERIDO, EXEQUENTE/EXECUTADO, AGRAVANTE/AGRAVADO, PERITO, UNIDADE EXTERNA...), ja
tokenizada. A legenda so escreve tokens e entes publicos ou instituicoes (Uniao, cartorio): parte
que ficou em claro no texto nao tem o nome copiado para ca. Depois dos papeis, os vinculos entre
tokens (o mesmo CNPJ/CPF nas capas; a denominacao anterior, quando um documento diz que o nome
mudou) e toda pessoa e empresa tokenizada do lote, com o numero de ocorrencias, os arquivos e o
papel que o cabecalho das pecas da ("EXECUTADO: [EMPRESA_2]") para quem nao esta em capa.

Os tokens nao sao unificados aqui: o vinculo e dito, nao decidido. A unica marca e o sufixo de grupo no
proprio texto (``[EMPRESA_3·G1]``, ``[EMPRESA_7·G1]``): so para empresas ligadas por vinculo DECLARADO (cadeia
de mudancas de nome, mesmo CNPJ nas capas); semelhanca de nome nunca cria grupo. A legenda explica cada Gn.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .lote_index import document_code, is_standalone, standalone_facts
from .token_syntax import strip_groups, with_group

CAST_NAME = "ELENCO.md"

_TOKEN = re.compile(r"\[([A-Z_]+)_\d+(?:·G\d+)?\]")
_PARTY_TOKEN = re.compile(r"\[(?:PESSOA|EMPRESA)_\d+(?:·G\d+)?\]")
_ID_TOKEN = re.compile(r"\[(?:CNPJ|CPF)_\d+(?:·G\d+)?\]")
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
    r"((?:\[(?:PESSOA|EMPRESA)_\d+(?:·G\d+)?\][ \t,;e]*)+)"
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
_COMPANY = r"\[EMPRESA_\d+(?:·G\d+)?\]"
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

# Contrato avulso (E14): socios e administradores, so quando o proprio texto os diz.
_TOKEN_ANY = r"\[(?:PESSOA|EMPRESA)_\d+(?:·G\d+)?\]"
_TOKEN_RUN = rf"{_TOKEN_ANY}(?:\s*(?:,|e/ou|e|ou|/)\s*{_TOKEN_ANY})*"
_ADMIN_CLAUSE = (
    re.compile(
        r"(?is)administra[çc][ãa]o\s+(?:da\s+)?(?:sociedade|empresa)[^.\[]{0,100}?"
        r"(?:exercid[ao]s?|cabe(?:rá)?|compete)\s+(?:pel[oa]s?|ao|aos|à)\s+"
        r"(?:(?:sócios?|sócias?|administrador(?:es|a)?|sócio[- ]administrador(?:es)?)\s+)*"
        rf"({_TOKEN_RUN})"
    ),
    re.compile(
        r"(?is)(?:nomead[oa]s?|designad[oa]s?|investid[oa]s?)\s+(?:como\s+|na\s+função\s+de\s+)?"
        rf"administrador(?:es|a)?[^.\[]{{0,60}}?({_TOKEN_RUN})"
    ),
)
_PREAMBLE_END = re.compile(
    r"(?i)únic[oa]s?\s+s[óo]ci[oa]s?|s[óo]ci[oa]s?\s+(?:componentes|integrantes)|\bresolvem?\b"
)
_QUALIFIED = re.compile(
    rf"({_TOKEN_ANY})\s*,\s*(?=(?:brasileir|portugu|nacionalidade|solteir|casad|divorciad|vi[úu]v|maior|"
    r"empres[áa]ri|natural|nascid|estrangeir|sociedade|pessoa\s+jur|inscrit|com\s+sede))",
    re.I,
)
_PREAMBLE_WINDOW = 3500


@dataclass(frozen=True, slots=True)
class CompanyGroup:
    """Empresas ligadas por vinculo declarado: ``members`` sao tokens sem sufixo, em ordem de numero."""

    number: int
    members: tuple[str, ...]
    reasons: tuple[str, ...]


_RENAME_KINDS = frozenset({"antiga denominação", "denominação posterior"})
_COMPANY_ONLY = re.compile(r"\[EMPRESA_\d+\]")


def company_groups(documents: Sequence[tuple[str, str]]) -> list[CompanyGroup]:
    """Grupos de empresas: componentes do grafo de vinculos declarados (cadeia de mudancas de nome e mesmo
    CNPJ nas capas), so entre tokens de empresa. Semelhanca de nome nunca liga ninguem. Gn numera por
    menor numero de token do grupo; o resultado nao depende de o texto ja ter o sufixo."""

    docs = [(name, strip_groups(text)) for name, text in documents]
    links = _links(docs, _cover_ids(docs))
    parent: dict[str, str] = {}

    def find(token: str) -> str:
        parent.setdefault(token, token)
        while parent[token] != token:
            parent[token] = parent[parent[token]]
            token = parent[token]
        return token

    reasons: dict[str, set[str]] = defaultdict(set)
    edges: list[tuple[str, str, str]] = []
    for first, kind, second, evidence in links:
        if not (_COMPANY_ONLY.fullmatch(first) and _COMPANY_ONLY.fullmatch(second)):
            continue
        if kind in _RENAME_KINDS:
            reason = "cadeia de mudanças de nome"
        elif kind == "mesmo CNPJ":
            identifier = _ID_TOKEN.search(evidence)
            reason = f"mesmo CNPJ {identifier.group(0)}" if identifier else "mesmo CNPJ"
        else:
            continue
        edges.append((first, second, reason))
        parent[find(first)] = find(second)
    for first, _second, reason in edges:
        reasons[find(first)].add(reason)
    components: dict[str, list[str]] = defaultdict(list)
    for token in parent:
        components[find(token)].append(token)
    ordered = sorted(
        (sorted(members, key=_token_order), root) for root, members in components.items() if len(members) > 1
    )
    ordered.sort(key=lambda item: _token_order(item[0][0]))
    return [
        CompanyGroup(number, tuple(members), tuple(sorted(reasons[root], key=_reason_order)))
        for number, (members, root) in enumerate(ordered, start=1)
    ]


def _reason_order(reason: str) -> tuple[int, str]:
    return (0 if reason.startswith("cadeia") else 1, reason)


def apply_groups(text: str, groups: Sequence[CompanyGroup]) -> str:
    """Poe o sufixo ``·Gn`` nos tokens de empresa dos grupos. Idempotente: so toca token sem sufixo."""

    numbers = {member: group.number for group in groups for member in group.members}
    if not numbers:
        return text
    return _COMPANY_ONLY.sub(lambda match: with_group(match.group(0), numbers.get(match.group(0))), text)


def apply_company_groups(markdown_paths: Sequence[Path]) -> list[CompanyGroup]:
    """Acha os grupos do lote e grava o sufixo nos Markdown (bytes exatos: so os tokens mudam).
    Sem grupo, nenhum arquivo e tocado."""

    paths = list(markdown_paths)
    texts = [path.read_bytes().decode("utf-8") for path in paths]
    groups = company_groups([(path.name, text) for path, text in zip(paths, texts)])
    if groups:
        for path, text in zip(paths, texts):
            grouped = apply_groups(text, groups)
            if grouped != text:
                path.write_bytes(grouped.encode("utf-8"))
    return groups


def _cover_label(name: str, before: str) -> str:
    process = _PROCESS_NUMBER.findall(before) or _PROCESS.findall(before)
    return process[-1] if process else name


def _cover_ids(documents: Sequence[tuple[str, str]]) -> dict[str, dict[str, set[str]]]:
    """CNPJ/CPF das tabelas de partes das capas: identificador -> token -> processos."""

    cover_ids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for name, text in documents:
        for table in _PARTIES.finditer(text):
            label = _cover_label(name, text[: table.start()])
            for party, _role, _kind, _reps, ids in _parse_parties(table.group(1)):
                for identifier in ids:
                    cover_ids[identifier][party].add(label)
    return cover_ids


def build_cast(documents: Sequence[tuple[str, str]]) -> str:
    """``documents``: (nome do .md, texto tokenizado), na ordem do lote. O sufixo de grupo ja gravado nos
    tokens nao muda o resultado: o ELENCO sai com o sufixo de todo membro de grupo."""

    groups = company_groups(documents)
    documents = [(name, strip_groups(text)) for name, text in documents]
    return apply_groups(_build_cast(documents, groups), groups)


def _build_cast(documents: Sequence[tuple[str, str]], groups: Sequence[CompanyGroup]) -> str:
    occurrences: Counter[str] = Counter()
    files: dict[str, set[str]] = defaultdict(set)
    for name, text in documents:
        for match in _PARTY_TOKEN.finditer(text):
            occurrences[match.group(0)] += 1
            files[match.group(0)].add(name)
    header_roles = _header_roles(documents)

    sections: list[str] = []
    listed: set[str] = set()
    cover_ids = _cover_ids(documents)
    for name, text in documents:
        for table in _PARTIES.finditer(text):
            before = text[: table.start()]
            label = _cover_label(name, before)
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
    contracts = _contract_rows(documents)
    links = _links(documents, cover_ids)
    if links:
        out += [
            "## Vínculos entre tokens",
            "",
            "Mais de um token para a mesma pessoa ou empresa, **só quando o próprio texto diz** "
            "(CNPJ ou CPF igual nas capas; documento que registra a mudança de nome). Os tokens "
            "não foram unificados: cada um mantém a numeração e a contagem dele.",
            "",
        ]
        if groups:
            out += _group_legend(groups, occurrences)
        out += [
            "| Token | Vínculo | Token | Evidência |",
            "|---|---|---|---|",
        ]
        out += [f"| {a} | {kind} | {b} | {evidence} |" for a, kind, b, evidence in links]
        out.append("")
    out += sections
    if contracts:
        out += _contract_section(contracts)
    rest = [token for token, _count in occurrences.most_common() if token not in listed]
    if rest:
        rows = []
        grouped = False
        for token in rest:
            kind = _LABELS.get(_TOKEN.match(token).group(1), "")
            names, used = _files_label(files[token])
            grouped = grouped or used
            roles = _role_list(header_roles.get(token))
            rows.append(f"| {token} | {kind} | {occurrences[token]} | {roles} | {names} |")
        out += [
            "## Demais pessoas e empresas do lote",
            "",
            "Papel não informado na capa; o contexto de cada ocorrência diz o papel. “Papel no "
            "cabeçalho das peças” é o que consta em linhas como “EXECUTADO: [token]” (número de linhas).",
            "",
        ]
        if grouped:
            out += [
                "Arquivo de documento avulso aparece pelo código do nome (CONTRSOCIAL3–9 = os arquivos "
                "terminados em _CONTRSOCIAL3 a _CONTRSOCIAL9; o `INDICE.md` dá o nome completo).",
                "",
            ]
        out += [
            "| Token | Tipo | Ocorrências | Papel no cabeçalho das peças | Arquivos |",
            "|---|---|---|---|---|",
        ]
        out += rows
    return "\n".join(out).rstrip() + "\n"


def _group_legend(groups: Sequence[CompanyGroup], occurrences: Counter[str]) -> list[str]:
    out = [
        "**Grupos.** No texto, o token de empresa ligado a outros leva o sufixo do grupo "
        "(`[EMPRESA_3·G1]`, `[EMPRESA_7·G1]`): são tokens **separados** (nome da época de cada ato), que o "
        "vínculo abaixo declara serem a mesma empresa. Semelhança de nome não forma grupo.",
        "",
    ]
    for group in groups:
        members = ", ".join(f"{member} ({occurrences.get(member, 0)})" for member in group.members)
        out.append(f"- G{group.number}: {'; '.join(group.reasons)}. Tokens (ocorrências no lote): {members}.")
    out.append("")
    return out


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
    groups = len(re.findall(r"(?m)^- G\d+: ", cast))
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
        "grupos": groups,
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
    match = re.match(r"\[([A-Z_]+)_(\d+)(?:·G\d+)?\]", token)
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


def _files_label(names: set[str]) -> tuple[str, bool]:
    """Lista de arquivos; os de documento avulso com o mesmo prefixo de codigo (3 ou mais) viram faixa."""

    coded: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for name in names:
        match = re.fullmatch(r"([^\W\d_]+)(\d+)", document_code(name))
        if match:
            coded[match.group(1)].append((int(match.group(2)), name))
    grouped = {prefix: items for prefix, items in coded.items() if len(items) >= 3}
    taken = {name for items in grouped.values() for _n, name in items}
    parts = [f"{prefix}{_ranges([n for n, _name in sorted(items)])}" for prefix, items in sorted(grouped.items())]
    return ", ".join(parts + sorted(name for name in names if name not in taken)), bool(grouped)


def _ranges(numbers: list[int]) -> str:
    runs: list[list[int]] = []
    for number in numbers:
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    return ", ".join(str(run[0]) if len(run) == 1 else f"{run[0]}–{run[-1]}" for run in runs)


def contract_roles(text: str) -> tuple[list[str], list[str]]:
    """(socios, administradores) de um contrato avulso, so por token e so pelo que o texto diz:
    partes qualificadas no preambulo (antes de "unicos socios"/"resolvem") e a clausula da administracao."""

    end = _PREAMBLE_END.search(text)
    preamble = text[: end.start()][-_PREAMBLE_WINDOW:] if end else ""
    partners = list(dict.fromkeys(_QUALIFIED.findall(preamble)))
    admins: list[str] = []
    for pattern in _ADMIN_CLAUSE:
        for match in pattern.finditer(text):
            admins += re.findall(_TOKEN_ANY, match.group(1))
    return partners, list(dict.fromkeys(admins))


def _contract_rows(documents: Sequence[tuple[str, str]]) -> list[tuple[str, str, str, list[str], list[str]]]:
    rows = []
    for name, text in documents:
        if not is_standalone(text):
            continue
        facts = standalone_facts(text, name)
        if not facts.code or not (facts.dates or facts.companies) or facts.issued:
            continue
        partners, admins = contract_roles(text)
        if not (partners or admins):
            continue
        rows.append((facts.code, " / ".join(facts.companies), "; ".join(facts.dates), partners, admins))
    rows.sort(key=lambda row: (_company_order(row[1]), _date_key(row[2]), row[0]))
    return rows


def _company_order(label: str) -> tuple[str, int]:
    return _token_order(label.split(" / ")[0]) if label else ("~", 0)


def _date_key(dates: str) -> str:
    first = dates.split("; ")[0]
    return f"{first[6:]}{first[3:5]}{first[:2]}" if len(first) == 10 else "9999"


def _contract_section(rows: list[tuple[str, str, str, list[str], list[str]]]) -> list[str]:
    out = [
        "## Sócios e administradores dos contratos avulsos",
        "",
        "Só tokens, pelo que cada contrato diz: “sócios” são as partes qualificadas no preâmbulo "
        "(antes de “únicos sócios” ou “resolvem”; podem ser sócio que entra ou que sai) e "
        "“administradores” os nomeados na cláusula da administração. Ordem: empresa e data do registro "
        "na Junta. Vazio = o texto não diz ou a leitura não reconheceu; não é afirmação de ausência.",
        "",
        "| Contrato | Empresa | Registro | Sócios (preâmbulo) | Administradores (cláusula) |",
        "|---|---|---|---|---|",
    ]
    for code, company, dates, partners, admins in rows:
        out.append(f"| {code} | {company} | {dates} | {', '.join(partners)} | {', '.join(admins)} |")
    out.append("")
    return out

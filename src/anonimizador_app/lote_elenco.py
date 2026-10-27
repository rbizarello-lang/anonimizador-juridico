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
As "alteracoes sucessivas do nome social" (duas alteracoes a menos de 900 caracteres uma da outra) so
CORROBORAM um vinculo explicito (declaracao que nomeia o nome antigo e o novo, ou o mesmo CNPJ): sozinhas
ficam como "possivel vinculo" no ELENCO, sem sufixo de grupo.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
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


# v0.4.15: token de CNPJ -> raiz de 8 digitos (do mapa da rodada; ``set_cnpj_roots``). Vazio: o token e a raiz.
_ACTIVE_CNPJ_ROOTS: dict[str, str] = {}


def set_cnpj_roots(mapping: object | None) -> None:
    """Raizes de CNPJ do mapa desta rodada, para os grupos ·Gn (``company_groups``) e o ELENCO."""

    _ACTIVE_CNPJ_ROOTS.clear()
    for record in getattr(mapping, "records", ()) or ():
        canonical = str(getattr(record, "canonical", ""))
        if getattr(record, "entity_type", "") == "BR_CNPJ" and len(canonical) >= 8:
            _ACTIVE_CNPJ_ROOTS[str(record.token)] = canonical[:8]


def company_groups(
    documents: Sequence[tuple[str, str]], cnpj_roots: Mapping[str, str] | None = None
) -> list[CompanyGroup]:
    """Grupos de empresas: componentes do grafo de vinculos declarados (cadeia de mudancas de nome e mesmo
    CNPJ nas capas), so entre tokens de empresa. Semelhanca de nome nunca liga ninguem. Gn numera por
    menor numero de token do grupo; o resultado nao depende de o texto ja ter o sufixo."""

    docs = [(name, strip_groups(text)) for name, text in documents]
    cover_ids = _cover_ids(docs)
    links = _links(docs, cover_ids)
    parent: dict[str, str] = {}
    # v0.4.15: raizes de CNPJ coladas a cada token de empresa (corpo e capas). Dois grupos com raizes conhecidas e
    # diferentes nunca se juntam: o "antiga denominacao" entre duas empresas de CNPJ diferente (ou o token que o LLM
    # fundiu) nao faz grupo. Sem o mapa, o proprio token de CNPJ e a raiz.
    known = _ACTIVE_CNPJ_ROOTS if cnpj_roots is None else cnpj_roots
    roots: dict[str, set[str]] = defaultdict(set)
    for identifier, tokens in _body_cnpj_owners(docs, shared_only=False).items():
        for token in tokens:
            roots[token].add(known.get(identifier, identifier))
    for identifier, parties in cover_ids.items():
        if identifier.startswith("[CNPJ"):
            for token in parties:
                roots[token].add(known.get(identifier, identifier))

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
        left, right = find(first), find(second)
        if left != right and roots[left] and roots[right] and not roots[left] & roots[right]:
            continue  # v0.4.15: CNPJ diferente de cada lado
        edges.append((first, second, reason))
        if left != right:
            parent[left] = right
            roots[right] |= roots.pop(left, set())
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
    # No Windows o Markdown sai com CRLF (``write_text``); as capas ("Partes e Representantes\n") so se
    # leem com LF. A deteccao usa o texto com LF; a gravacao parte dos bytes originais (so o token muda).
    groups = company_groups(
        [(path.name, text.replace("\r\n", "\n")) for path, text in zip(paths, texts)]
    )
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
    if covers:
        opening = (
            "Papéis tirados da tabela “Partes e Representantes” da capa de cada processo "
            f"({covers} capa(s) lida(s) neste lote). Os nomes não estão aqui: só os tokens do texto. "
            "O mesmo token é a mesma pessoa ou empresa em todos os arquivos. Parte que a capa traz sem "
            "token aparece como “parte sem token no texto”. Quem não está em capa leva, na última seção, "
            "o papel que o cabeçalho das peças lhe dá, quando há."
        )
    else:
        # v0.4.10: lote sem capa (autos sem a tabela de partes): nao falar de capa que nao existe.
        opening = (
            "Este lote não traz capa de processo (tabela “Partes e Representantes”): os papéis abaixo vêm do "
            "cabeçalho das peças, quando há. Os nomes não estão aqui: só os tokens do texto. O mesmo token é a "
            "mesma pessoa ou empresa em todos os arquivos."
        )
    out = ["# Elenco do lote (sem nomes)", "", opening, ""]
    contracts = _contract_rows(documents)
    links = _links(documents, cover_ids)
    if links:
        out += [
            "## Vínculos entre tokens",
            "",
            "Mais de um token para a mesma pessoa ou empresa, **só quando o próprio texto diz** "
            "(CNPJ ou CPF igual nas capas ou colado aos dois nomes no texto; documento que registra a mudança "
            "de nome). Os tokens não foram unificados: cada um mantém a numeração e a contagem dele. "
            "“Possível vínculo” é proximidade de duas alterações de nome social: pode ser a mesma empresa "
            "(forma curta ou denominação anterior), mas o texto não o declara.",
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
            "## Demais pessoas e empresas do lote" if covers else "## Pessoas e empresas do lote",
            "",
            ("Papel não informado na capa; o" if covers else "O")
            + " contexto de cada ocorrência diz o papel. “Papel no "
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
        "vínculo abaixo declara serem a mesma empresa. Semelhança de nome, sozinha, não cria grupo (veja “Pode ser a mesma empresa”).",
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
    step_pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, text in documents:
        for match in _RENAME_PAIR.finditer(text):
            renames[(match.group(1), match.group(2))].append(
                f"alteração contratual “nome inicial … alterado para …” ({_where(name, text, match.start())})"
            )
        steps = list(_RENAME_STEP.finditer(text))
        for earlier, later in zip(steps, steps[1:]):
            if later.start() - earlier.end() <= _STEP_GAP and earlier.group(1) != later.group(1):
                step_pairs[(earlier.group(1), later.group(1))].append(
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
    # As "alteracoes sucessivas" (duas alteracoes de nome social proximas) so CORROBORAM um vinculo explicito:
    # uma declaracao que nomeia o nome antigo e o novo, ou o mesmo CNPJ. Sozinhas nao criam grupo: duas
    # empresas distintas que alteraram o nome na mesma pagina nao sao a mesma empresa (I-N1). Isolada, a
    # sequencia fica no ELENCO como "possivel vinculo", que nao leva sufixo de grupo.
    same_id = [set(parties) for parties in cover_ids.values()]
    possible: dict[tuple[str, str], list[str]] = {}
    for pair, evidence in step_pairs.items():
        explicit = pair in renames or pair[::-1] in renames or any(set(pair) <= tokens for tokens in same_id)
        if explicit:
            renames[pair].extend(evidence)
        else:
            possible[pair] = evidence
    for (old, new), evidence in renames.items():
        shown = "; ".join(dict.fromkeys(evidence[:2]))
        add(new, "antiga denominação", old, shown)
        add(old, "denominação posterior", new, shown)
    body_ids = _body_cnpj_owners(documents)
    same_cnpj = [set(tokens) for tokens in body_ids.values()]
    for (first, second), evidence in possible.items():
        if any({first, second} <= tokens for tokens in same_cnpj):
            continue
        shown = (
            "; ".join(dict.fromkeys(evidence[:2]))
            + " (pode ser a mesma empresa: forma curta ou denominação anterior; o texto não declara)"
        )
        add(first, "possível vínculo", second, shown)

    # Mesmo CNPJ/CPF nas capas: a mesma pessoa, ainda que o nome tenha sido lido de outro jeito.
    for identifier, parties in cover_ids.items():
        tokens = sorted(parties, key=_token_order)
        for index, first in enumerate(tokens):
            for second in tokens[index + 1 :]:
                where = ", ".join(sorted(parties[first] | parties[second]))
                add(first, "mesmo " + ("CNPJ" if identifier.startswith("[CNPJ") else "CPF"), second,
                    f"{identifier} nas capas dos processos {where}")
    # v0.4.11: o mesmo token de CNPJ colado a dois tokens de empresa no corpo do texto (nao so nas capas) e a mesma
    # pessoa juridica: vinculo firme ("mesmo CNPJ"), que forma grupo. Nunca "nao forma grupo" para esse par.
    for identifier, tokens in sorted(body_ids.items(), key=lambda item: _token_order(item[0])):
        ordered = sorted(tokens, key=_token_order)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                add(first, "mesmo CNPJ", second, f"{identifier} colado aos dois no texto")
    return links


def _body_cnpj_owners(documents: Sequence[tuple[str, str]], *, shared_only: bool = True) -> dict[str, set[str]]:
    """Token de CNPJ -> tokens de empresa colados a ele no texto ("[EMPRESA_1], CNPJ [CNPJ_1]", "[CNPJ_1] | [EMPRESA_1]"),
    so quando o CNPJ e do nome imediatamente vizinho (nada alem de pontuacao e o rotulo no meio). ``shared_only``: so o
    CNPJ colado a 2+ tokens (v0.4.15: ``False`` da todos, para o veto de grupo por CNPJ diferente)."""

    owners: dict[str, set[str]] = defaultdict(set)
    for _name, text in documents:
        plain = strip_groups(text)
        for line in plain.splitlines():
            companies = list(_COMPANY_TOKEN_BODY.finditer(line))
            if not companies:
                continue
            for cnpj in _CNPJ_TOKEN_BODY.finditer(line):
                before = [match for match in companies if match.end() <= cnpj.start()]
                if before and _CNPJ_GAP_BODY.match(line[before[-1].end() : cnpj.start()]):
                    owners[cnpj.group(0)].add(before[-1].group(0))
                    continue
                after = [match for match in companies if match.start() >= cnpj.end()]
                if after and _CNPJ_GAP_BODY.match(line[cnpj.end() : after[0].start()]):
                    owners[cnpj.group(0)].add(after[0].group(0))
    return {identifier: tokens for identifier, tokens in owners.items() if len(tokens) >= 2 or not shared_only}


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


# ----------------------------------------------------------------------------------------------------------------
# H9-n: "Pode ser a mesma pessoa" tambem no modo local (no modo LLM ja existia, H9-j). O INSTRUCOES_IA.md manda a IA
# redatora consultar esta secao. So tokens e o tipo de ligacao; nenhum nome sai daqui.

SAME_PERSON_TITLE = "## Pode ser a mesma pessoa (os tokens não foram unificados)"
SAME_PERSON_EMPTY = "Nenhum caso detectado."
_NAME_PARTICLES = frozenset({"de", "da", "do", "dos", "das", "e"})
_PERSON_TOKEN = re.compile(r"\[PESSOA_\d+(?:·G\d+)?\]")
_CPF_TOKEN = re.compile(r"\[CPF_\d+(?:·G\d+)?\]")
_CPF_REACH = 90  # caracteres entre o fim do token de pessoa e o CPF, na mesma linha
# v0.4.11: entre o nome e o seu CPF so pontuacao e o rotulo ("CPF", "CPF/MF", "inscrito(a) no CPF sob o nº"); nunca outro
# nome, "e", nem o plural "CPFs".
_CPF_GAP = re.compile(
    r"(?i)^[\s,;:(\-–—|]*(?:(?:portador(?:a)?\s+do\s+|inscrit[oa]\s+no\s+|com\s+)?(?:cpf|c\.p\.f\.?)(?:\s*/\s*mf)?"
    r"(?:\s+sob\s+o)?\s*(?:n[º°o.]{0,2})?)?[\s:,\-–—|]*$"
)
_CNPJ_TOKEN_BODY = re.compile(r"\[CNPJ_\d+(?:·G\d+)?\]")
_COMPANY_TOKEN_BODY = re.compile(r"\[EMPRESA_\d+(?:·G\d+)?\]")
_CNPJ_GAP_BODY = re.compile(
    # v0.4.15: o ponto do "Ltda." que ficou fora do token ("[EMPRESA_1]. (CNPJ n. [CNPJ_1])") tambem cola.
    r"(?i)^\.?[\s,;:(\-–—|]*(?:(?:inscrit[ao]\s+no\s+|sob\s+o\s+|com\s+)?(?:cnpj|c\.n\.p\.j\.?)(?:\s*/\s*mf)?"
    r"(?:\s+sob\s+o)?\s*(?:n[º°o.]{0,2})?)?[\s:,\-–—|]*$"
)


def _token_key(token: str) -> tuple[str, int]:
    label, _, number = token.strip("[]").rpartition("_")
    return label, int(number) if number.isdigit() else 0


def partial_name_rows(
    people: Sequence[tuple[str, str]], documents: Sequence[tuple[str, str]] = ()
) -> list[tuple[str, list[str], str]]:
    """(token, nome) de pessoa -> linhas (token, [tokens que o contem], ligacao): o nome do token da esquerda (todas as
    palavras, sem particula) cabe no nome de outro token de pessoa: prenome, sobrenome ou parte do nome.

    v0.4.10: nunca sugere o par que difere so por sufixo de geracao (pai x filho, avo x neto) nem o par que o proprio
    texto compara como grafias diferentes ("por lapso constou", "leia-se", "homonimia")."""

    from anonimizador_juridico.pseudonymizer import fold_lexical

    from .name_generation import GENERATION_WORDS, conflicting_generation

    names = dict(people)
    compared = compared_token_pairs(documents)
    entries = []
    for token, name in people:
        words = [word for word in fold_lexical(name).split() if word not in _NAME_PARTICLES]
        if words:
            entries.append((token, words))
    rows: list[tuple[str, list[str], str]] = []
    for token, words in entries:
        hosts = [
            other
            for other, other_words in entries
            if other != token
            and len(other_words) > len(words)
            and set(words) <= set(other_words)
            and not (set(other_words) - set(words)) <= GENERATION_WORDS
            and not conflicting_generation(names[other], names[token])
            and frozenset((token, other)) not in compared
        ]
        if not hosts:
            continue
        if len(words) == 1:
            first = all(next(w for t, w in entries if t == host)[0] == words[0] for host in hosts)
            how = "prenome" if first else "sobrenome"
        else:
            how = "parte do nome"
        rows.append((token, sorted(hosts, key=_token_key), how))
    return rows


# Trecho em que o texto compara duas grafias ou afasta a identidade: os dois tokens sao pessoas (ou grafias) distintas.
_COMPARE_CUE = re.compile(
    r"(?i)\b(?:lapso|grafia|leia-se|onde\s+se\s+l[eê]|homon[ií]mi|n[aã]o\s+se\s+confunde|erro\s+material"
    r"|fez\s+constar|fizeram\s+constar)"
)
_COMPARE_REACH = 250


def compared_token_pairs(documents: Sequence[tuple[str, str]]) -> set[frozenset[str]]:
    """Pares de tokens de pessoa que aparecem perto de um marcador de comparacao de grafia ou de homonimia."""

    pairs: set[frozenset[str]] = set()
    for _name, text in documents:
        plain = strip_groups(text)
        for cue in _COMPARE_CUE.finditer(plain):
            window = plain[max(0, cue.start() - _COMPARE_REACH) : cue.end() + _COMPARE_REACH]
            tokens = sorted({match.group(0) for match in _PERSON_TOKEN.finditer(window)})
            for index, first in enumerate(tokens):
                for second in tokens[index + 1 :]:
                    pairs.add(frozenset((first, second)))
    return pairs


def same_cpf_rows(documents: Sequence[tuple[str, str]]) -> list[tuple[str, list[str], str]]:
    """Dois ou mais tokens de pessoa colados ao MESMO token de CPF (na mesma linha, logo antes do CPF)."""

    owners: dict[str, set[str]] = defaultdict(set)
    for _name, text in documents:
        for line in strip_groups(text).splitlines():
            people = list(_PERSON_TOKEN.finditer(line))
            if not people:
                continue
            for cpf in _CPF_TOKEN.finditer(line):
                before = [match for match in people if match.end() <= cpf.start()]
                # v0.4.11: so o CPF COLADO ao nome (virgula, "CPF", "inscrito no CPF sob o nº"): "[PESSOA_1] e
                # [PESSOA_2], CPFs [CPF_1] e [CPF_2]" nao diz de quem e cada CPF, e ligava conjuges distintos.
                if (
                    before
                    and cpf.start() - before[-1].end() <= _CPF_REACH
                    and _CPF_GAP.match(line[before[-1].end() : cpf.start()])
                ):
                    owners[cpf.group(0)].add(before[-1].group(0))
    rows: list[tuple[str, list[str], str]] = []
    for cpf, tokens in sorted(owners.items(), key=lambda item: _token_key(item[0])):
        if len(tokens) < 2:
            continue
        ordered = sorted(tokens, key=_token_key)
        for token in ordered:
            rows.append((token, [other for other in ordered if other != token], f"mesmo CPF {cpf} ao lado"))
    return rows


def same_person_lines(rows: Sequence[tuple[str, list[str], str]]) -> list[str]:
    lines = [
        SAME_PERSON_TITLE,
        "",
        "O nome do token da esquerda é só parte do nome do(s) token(s) da direita (um prenome, um sobrenome ou "
        "outra forma curta), ou os dois aparecem ao lado do mesmo CPF. Na dúvida o app separa; quem lê decide pelo "
        "contexto. Mais de um candidato = sobrenome ou prenome comum a várias pessoas: não presuma que é a mesma.",
        "",
    ]
    if not rows:
        return [*lines, SAME_PERSON_EMPTY]
    return [
        *lines,
        "| Token | Pode ser | Ligação |",
        "|---|---|---|",
        *(f"| {token} | {' ou '.join(others)} | {how} |" for token, others, how in rows),
    ]


def append_same_person_section(
    export_dir: Path, people: Sequence[tuple[str, str]], markdown_paths: Sequence[Path]
) -> Path:
    """Acrescenta a secao ao ``ELENCO.md`` (modo local). ``people``: (token, nome) das pessoas do mapa."""

    documents = [(path.name, path.read_text(encoding="utf-8")) for path in markdown_paths]
    rows = partial_name_rows(people, documents)
    seen = {(token, tuple(others)) for token, others, _how in rows}
    rows += [row for row in same_cpf_rows(documents) if (row[0], tuple(row[1])) not in seen]
    rows.sort(key=lambda row: _token_key(row[0]))
    target = Path(export_dir) / CAST_NAME
    existing = target.read_text(encoding="utf-8").rstrip("\n") if target.is_file() else ""
    body = "\n".join(same_person_lines(rows)) + "\n"
    target.write_text((existing + "\n\n" if existing else "") + body, encoding="utf-8")
    return target


# --- H9-o: empresa com mais de uma denominacao no mesmo token ---------------------------------------------------------
# Razoes sociais diferentes unidas num token so (mudanca de nome, "Outras grafias", UNIR da revisao externa) fazem o
# texto ler "[EMPRESA_1] alterou a denominacao para [EMPRESA_1]". O token no texto NAO muda (a reversao pelo mapa.json
# troca cada token por uma grafia so; um token novo por denominacao quebraria a volta): o ELENCO diz quantas
# denominacoes distintas o token cobre, com rotulos neutros e sem nome nenhum.
DENOMINATIONS_TITLE = "## Empresas com mais de uma denominação (o mesmo token)"
_LEGAL_TAIL_DENOM = re.compile(
    r"(?i)(?:[\s,.\-]+(?:ltda|limitada|eireli|s\.?\s*/?\s*a|me|epp|em\s+recupera[çc][ãa]o\s+judicial))+[\s.]*$"
)


def _denomination_body(value: str) -> list[str]:
    from anonimizador_juridico.pseudonymizer import fold_lexical

    body = _LEGAL_TAIL_DENOM.sub("", " ".join(str(value).split()))
    return [word for word in re.findall(r"[^\W_]+", fold_lexical(body)) if word not in _NAME_PARTICLES]


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def _same_denomination(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return True
    if set(left) <= set(right) or set(right) <= set(left):
        return True  # forma curta ("Exemplo" e "Exemplo Comercio de Madeiras")
    joined = (" ".join(left), " ".join(right))
    return _edit_distance(*joined) <= max(1, min(len(item) for item in joined) // 10)  # OCR, acento, abreviatura


def denomination_rows(records: Sequence[object]) -> list[tuple[str, list[int]]]:
    """(token, ocorrencias por denominacao) das empresas cujo token cobre 2+ razoes sociais distintas. A ordem das
    denominacoes e a das grafias no mapa (a primeira trocada no lote vem antes)."""

    rows: list[tuple[str, list[int]]] = []
    for record in records:
        if getattr(record, "entity_type", "") != "ORGANIZATION":
            continue
        counts = dict(getattr(record, "counts", {}) or {})
        clusters: list[tuple[list[list[str]], int]] = []
        for original in getattr(record, "originals", ()) or ():
            words = _denomination_body(original)
            if not words:
                continue
            found = next((index for index, (members, _n) in enumerate(clusters)
                          if any(_same_denomination(words, member) for member in members)), None)
            weight = int(counts.get(original, 1) or 1)
            if found is None:
                clusters.append(([words], weight))
            else:
                members, total = clusters[found]
                members.append(words)
                clusters[found] = (members, total + weight)
        if len(clusters) >= 2:
            rows.append((str(record.token), [total for _members, total in clusters]))  # type: ignore[attr-defined]
    rows.sort(key=lambda row: _token_key(row[0]))
    return rows


def denomination_lines(rows: Sequence[tuple[str, list[int]]]) -> list[str]:
    lines = [
        DENOMINATIONS_TITLE,
        "",
        "O token cobre razões sociais diferentes da mesma empresa (mudança de nome, grafias unidas na aba Caso ou pela "
        "revisão). Por isso o texto pode ler \"[EMPRESA_1] alterou a denominação para [EMPRESA_1]\": são duas "
        "denominações do mesmo token. Os nomes não vêm aqui; a ordem é a da primeira ocorrência no lote.",
        "",
        "| Token | Denominações distintas no processo | Ocorrências por denominação |",
        "|---|---|---|",
    ]
    for token, totals in rows:
        labels = ", ".join(
            f"denominação {index}" + (" (a primeira no texto)" if index == 1 else "")
            for index in range(1, len(totals) + 1)
        )
        lines.append(f"| {token} | {len(totals)}: {labels} | {', '.join(str(total) for total in totals)} |")
    return lines


def append_denominations_section(export_dir: Path, records: Sequence[object]) -> Path | None:
    """H9-o: acrescenta ao ``ELENCO.md`` as empresas com 2+ denominacoes no mesmo token (sem nomes). Sem caso: nada."""

    rows = denomination_rows(records)
    if not rows:
        return None
    target = Path(export_dir) / CAST_NAME
    existing = target.read_text(encoding="utf-8").rstrip("\n") if target.is_file() else ""
    body = "\n".join(denomination_lines(rows)) + "\n"
    target.write_text((existing + "\n\n" if existing else "") + body, encoding="utf-8")
    return target


# --- v0.4.11: empresa que pode ser a mesma (forma curta ou denominacao anterior nao unificada) ------------------------
SAME_COMPANY_TITLE = "## Pode ser a mesma empresa (os tokens não foram unificados)"


def same_company_rows(records: Sequence[object], grouped: Sequence[CompanyGroup] = ()) -> list[tuple[str, list[str]]]:
    """(token, [tokens]) de empresa: alguma grafia do token da esquerda e o COMECO (palavras inteiras) de uma grafia do
    token da direita, e o app nao os uniu (radical comum a mais de uma empresa, sem CNPJ nem declaracao). Tokens do
    mesmo grupo Gn ja tem vinculo firme e nao entram."""

    in_group = {member: group.number for group in grouped for member in group.members}
    forms: dict[str, list[list[str]]] = {}
    for record in records:
        if getattr(record, "entity_type", "") != "ORGANIZATION":
            continue
        bodies = [_denomination_body(original) for original in getattr(record, "originals", ()) or ()]
        forms[str(record.token)] = [body for body in bodies if body]  # type: ignore[attr-defined]
    rows: list[tuple[str, list[str]]] = []
    for token, mine in forms.items():
        hosts = []
        for other, theirs in forms.items():
            if other == token or (token in in_group and in_group.get(other) == in_group[token]):
                continue
            if any(
                len(short) < len(long) and long[: len(short)] == short and len(short[0]) >= 4
                for short in mine
                for long in theirs
            ):
                hosts.append(other)
        if hosts:
            rows.append((token, sorted(hosts, key=_token_key)))
    rows.sort(key=lambda row: _token_key(row[0]))
    return rows


def same_company_lines(rows: Sequence[tuple[str, list[str]]]) -> list[str]:
    return [
        SAME_COMPANY_TITLE,
        "",
        "Pode ser a mesma empresa (forma curta ou denominação anterior): o nome do token da esquerda é o começo do "
        "nome do(s) token(s) da direita, mas o texto não traz CNPJ nem declaração que una os dois (ou mais de uma "
        "empresa começa assim). Mais de um candidato: não presuma qual é.",
        "",
        "| Token | Pode ser | Ligação |",
        "|---|---|---|",
        *(f"| {token} | {' ou '.join(others)} | forma curta ou denominação anterior |" for token, others in rows),
    ]


def append_same_company_section(
    export_dir: Path, records: Sequence[object], grouped: Sequence[CompanyGroup] = ()
) -> Path | None:
    rows = same_company_rows(records, grouped)
    if not rows:
        return None
    target = Path(export_dir) / CAST_NAME
    existing = target.read_text(encoding="utf-8").rstrip("\n") if target.is_file() else ""
    body = "\n".join(same_company_lines(rows)) + "\n"
    target.write_text((existing + "\n\n" if existing else "") + body, encoding="utf-8")
    return target


# --- v0.4.11: sobrenome de familia ([SOBRENOME_n]) --------------------------------------------------------------------
FAMILY_TITLE = "## Sobrenomes de família"


def family_rows(records: Sequence[object]) -> list[tuple[str, list[str]]]:
    """(token de sobrenome, tokens de pessoa cujo nome traz esse sobrenome)."""

    from anonimizador_juridico.pseudonymizer import fold_lexical

    people = [
        (str(record.token), set(fold_lexical(" ".join(getattr(record, "originals", ()) or ())).split()))  # type: ignore[attr-defined]
        for record in records
        if getattr(record, "entity_type", "") == "PERSON"
    ]
    rows: list[tuple[str, list[str]]] = []
    for record in records:
        if getattr(record, "entity_type", "") != "FAMILY_NAME":
            continue
        canonical = str(getattr(record, "canonical", ""))
        word = canonical.split(":", 1)[-1].strip()
        owners = sorted((token for token, words in people if word and word in words), key=_token_key)
        rows.append((str(record.token), owners))  # type: ignore[attr-defined]
    rows.sort(key=lambda row: _token_key(row[0]))
    return rows


def append_family_section(export_dir: Path, records: Sequence[object]) -> Path | None:
    rows = family_rows(records)
    if not rows:
        return None
    lines = [
        FAMILY_TITLE,
        "",
        "O token [SOBRENOME_n] é um sobrenome de família (\"família [SOBRENOME_1]\", \"patronímico [SOBRENOME_1]\"), não "
        "uma pessoa. A coluna da direita diz quais pessoas do lote trazem esse sobrenome no nome.",
        "",
        "| Token | Pessoas com este sobrenome |",
        "|---|---|",
        *(f"| {token} | {', '.join(owners) if owners else '-'} |" for token, owners in rows),
    ]
    target = Path(export_dir) / CAST_NAME
    existing = target.read_text(encoding="utf-8").rstrip("\n") if target.is_file() else ""
    target.write_text((existing + "\n\n" if existing else "") + "\n".join(lines) + "\n", encoding="utf-8")
    return target


# --- v0.4.15: prenome solto de 2+ pessoas ([PRENOME_n]) ---------------------------------------------------------------
FIRST_NAME_TITLE = "## Prenomes de mais de uma pessoa"


def first_name_rows(records: Sequence[object]) -> list[tuple[str, list[str]]]:
    """(token de prenome, tokens de pessoa cujo nome comeca por esse prenome)."""

    from anonimizador_juridico.pseudonymizer import fold_lexical

    people = []
    for record in records:
        if getattr(record, "entity_type", "") != "PERSON":
            continue
        firsts = {
            words[0]
            for words in (fold_lexical(value).split() for value in getattr(record, "originals", ()) or ())
            if len(words) >= 2
        }
        people.append((str(record.token), firsts))  # type: ignore[attr-defined]
    rows: list[tuple[str, list[str]]] = []
    for record in records:
        if getattr(record, "entity_type", "") != "FIRST_NAME":
            continue
        word = str(getattr(record, "canonical", "")).split(":", 1)[-1].strip()
        owners = sorted((token for token, firsts in people if word and word in firsts), key=_token_key)
        rows.append((str(record.token), owners))  # type: ignore[attr-defined]
    rows.sort(key=lambda row: _token_key(row[0]))
    return rows


def append_first_name_section(export_dir: Path, records: Sequence[object]) -> Path | None:
    rows = first_name_rows(records)
    if not rows:
        return None
    lines = [
        FIRST_NAME_TITLE,
        "",
        "O token [PRENOME_n] é um prenome escrito sozinho que mais de uma pessoa do lote tem: não diz qual delas é. A "
        "coluna da direita diz quais pessoas do lote têm esse prenome; o contexto de cada ocorrência decide.",
        "",
        "| Token | Pessoas com este prenome |",
        "|---|---|",
        *(f"| {token} | {', '.join(owners) if owners else '-'} |" for token, owners in rows),
    ]
    target = Path(export_dir) / CAST_NAME
    existing = target.read_text(encoding="utf-8").rstrip("\n") if target.is_file() else ""
    target.write_text((existing + "\n\n" if existing else "") + "\n".join(lines) + "\n", encoding="utf-8")
    return target

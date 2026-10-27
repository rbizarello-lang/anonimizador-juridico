"""Denominacao compartilhada (v0.4.13): a MESMA razao social usada, em epocas diferentes, por duas empresas distintas.

No grupo familiar a denominacao antiga de uma empresa e adotada depois por outra ("Alfa Ltda" foi o nome da empresa do
CNPJ A ate 2002 e, de 2002 em diante, o da empresa do CNPJ B). A troca pela grafia da um token so a todas as ocorrencias
da forma, e o leitor nao acompanha a cadeia societaria: o trecho "a B (na epoca denominada Alfa Ltda) recebeu poderes"
e o trecho "a A, na epoca denominada Alfa Ltda, teve sede ali" levam o mesmo token (questao D04 do sintetico 3, errada
em todos os bracos da rodada 4).

Regra (a menor que separa sem mexer no resto), sobre os spans finais de qualquer origem (lista, motor, LLM):

1. Prova por ocorrencia (so CNPJ valido; matriz e filial, mesma raiz de 8 digitos, sao a mesma empresa):
   - o CNPJ colado depois da forma ("Alfa Ltda. (CNPJ n. A)", "ALFA LTDA | A", a linha de baixo numa tabela);
   - a ponte "X (CNPJ n. A), na epoca denominada Alfa Ltda" / "CNPJ n. A, ja foi uma sociedade de nome Alfa Ltda";
   - a qualificacao "Alfa Ltda, pessoa juridica..., com sede..., inscrita no CNPJ sob o n. A" (mesma frase, nenhum
     outro nome de empresa no meio).
2. A forma (palavras de conteudo, por chave do elenco) e COMPARTILHADA quando tem prova de 2+ raizes diferentes e cada
   raiz aparece 2+ vezes no lote. Sem isso nada muda: forma de uma empresa so nunca e tocada.
3. Ocorrencia sem prova propria:
   a. o texto a liga a outro nome de UMA das empresas com expressao de denominacao no meio, ate 90 caracteres: "a SIGLA
      (na epoca denominada Alfa Ltda)", "Alfa Ltda (denominacao anterior da SIGLA)", "a X tambem ja adotou a denominacao
      Alfa Ltda". O outro nome e o de um span cujas formas tem uma raiz so, ou um nome escrito antes de um CNPJ (vale a
      sigla que ficou em claro). Os dois lados com nome de empresas diferentes = ambigua. Nome de outra empresa ao lado
      SEM a expressao (enumeracao "X, Y e Alfa Ltda") nao e prova;
   b. senao, as ocorrencias da mesma forma ja decididas NA MESMA FOLHA, se todas de uma raiz so;
   c. senao, ambigua.
4. Tokens: a raiz com mais ocorrencias fica com a chave (o token) de antes; cada outra raiz ganha chave propria
   (``chave#cnpj:raiz``); a ocorrencia ambigua ganha uma terceira (``chave#ambigua``). Na duvida, o token proprio, nunca
   a fusao. O ELENCO diz quais tokens sao a mesma denominacao e de qual CNPJ (``append_shared_names_section``).

Nada aqui acrescenta ou retira span: so troca a chave. Dado generico; nenhum dado de caso.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import canonicalize

from .lote_elenco import CAST_NAME
from .roster_identity import _cnpj_roots, content_words

AMBIGUOUS_SUFFIX = "#ambigua"
ROOT_SUFFIX = "#cnpj:"

# Entre o fim do nome e o CNPJ: pontuacao, separador de tabela, quebra de linha e o rotulo ("(CNPJ n.", "inscrita no CNPJ
# sob o n.").
_GAP_TO_CNPJ = re.compile(
    r"(?i)[\s|,;:.()\[\]\-–—\"“”]*(?:(?:inscrit[ao]\s+no\s+|sob\s+o\s+|com\s+)?(?:cnpj|c\.n\.p\.j\.?|cgc)(?:\s*/\s*mf)?"
    r"\s*(?:sob\s+(?:o\s+)?)?(?:n(?:[º°o.r]{1,3})?\.?)?)?[\s|,;:()\-–—]*$"
)
# Do CNPJ ate a forma: "(CNPJ n. A), na epoca denominada", "CNPJ n. A, ja foi uma sociedade empresaria de nome".
_BRIDGE = re.compile(
    r"(?i)^[\s)\]]*(?:\d{1,3}\s*)?[,;]?\s*(?:(?:na|[àa])\s+[ée]poca\s+(?:uma\s+)?(?:[^\W\d_]+\s+){0,2}?denominad[ao]"
    r"|ent[aã]o\s+denominad[ao]|anteriormente\s+denominad[ao]|antigamente\s+denominad[ao]|antes\s+denominad[ao]"
    r"|j[aá]\s+foi\s+(?:uma\s+)?(?:[^\W\d_]+\s+){0,2}?(?:de\s+)?nome"
    r"|(?:que\s+)?(?:adotou|teve|usou)\s+a\s+denomina[cç][aã]o(?:\s+social)?(?:\s+de)?)\s*:?\s*[\"“]?$"
)
# Rotulo da qualificacao logo antes do CNPJ ("inscrita no CNPJ sob o n.", "inscrita no CGC sob nr.").
_QUALIFIED_LABEL = re.compile(
    r"(?i)inscrit[ao]\s+no\s+(?:cnpj|c\.n\.p\.j\.?|cgc)(?:\s*/\s*mf)?\s*(?:sob\s+(?:o\s+)?)?(?:n(?:[º°o.r]{1,3})?\.?)?"
    r"\s*[:.]?\s*$"
)
_SENTENCE_BREAK = re.compile(r"\n\s*\n|[.;]\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç])")
# Expressao de denominacao entre a forma e o outro nome da empresa (regra 3a).
_CUE = re.compile(
    r"(?i)denomina|de\s+nome|raz[aã]o\s+social|nome\s+(?:empresarial|social)|\bantiga\b|\banterior\b|\batual\b"
    r"|\bex-|\bhoje\b"
)
_ENUMERATION = re.compile(r"(?i)\s*(?:,|\be\b|\bou\b)\s*(?:(?:d[ao]s?|[ao]s?|[àa]s?)\s+)?")
_BRIDGE_REACH = 120
_NAME_REACH = 160
_QUALIFIED_REACH = 350
_NEAR = 90
_MIN_ROOT_SEEN = 2
_CHUNK_WORD = re.compile(r"\S+")
_CHUNK_CONNECTIVE = frozenset({"de", "da", "do", "das", "dos", "e", "em"})
_CHUNK_ARTICLE = frozenset({"a", "o", "as", "os", "à", "ao"})
_PAGE = re.compile(r"<!--\s*pagina-origem:\s*(\d+)\s*-->")


@dataclass(slots=True)
class SharedForm:
    """Uma forma compartilhada: a chave original, as raizes de CNPJ e as chaves novas (so para ELENCO e AUDITORIA)."""

    key: str
    form: str
    roots: tuple[str, ...]
    main_root: str
    keys: dict[str, str] = field(default_factory=dict)  # raiz -> chave
    ambiguous_key: str = ""
    counts: Counter = field(default_factory=Counter)  # cnpj, contexto, folha, sem_contexto


def _key(text: str, span: EntitySpan) -> str:
    return span.canonical_key or canonicalize(span.entity_type, text[span.start : span.end])


def _form(text: str, span: EntitySpan) -> str:
    return " ".join(content_words("empresa", text[span.start : span.end]))


def _name_end(before: str) -> int:
    """Onde termina o nome no trecho antes do CNPJ (o rotulo e a pontuacao saem)."""

    match = _GAP_TO_CNPJ.search(before)
    return match.start() if match is not None else len(before)


def _written_name(before: str) -> str:
    """O nome escrito logo antes do CNPJ (palavras com maiuscula ou numero, e os conectivos de dentro), sem artigo."""

    words = _CHUNK_WORD.findall(before)
    taken: list[str] = []
    for word in reversed(words[-9:]):
        bare = word.strip(",;:()[]\"“”")
        if not bare:
            break
        if bare[0].isupper() or bare[0].isdigit() or bare.casefold() in _CHUNK_CONNECTIVE:
            taken.append(bare)
            continue
        break
    taken.reverse()
    while taken and (taken[0].casefold() in _CHUNK_CONNECTIVE or taken[0].casefold() in _CHUNK_ARTICLE):
        taken.pop(0)
    while taken and taken[-1].casefold() in _CHUNK_CONNECTIVE:
        taken.pop()
    name = " ".join(taken)
    return name if sum(char.isalpha() for char in name) >= 3 else ""


def _name_pattern(name: str) -> re.Pattern[str]:
    words = [re.escape(word) for word in name.split()]
    return re.compile(r"(?i)(?<![^\W_])" + r"\s+".join(words) + r"(?![^\W_])")


def _pages(text: str) -> list[int]:
    return [match.start() for match in _PAGE.finditer(text)]


def _page_index(marks: Sequence[int], position: int) -> int:
    from bisect import bisect_right

    return bisect_right(marks, position)


def _evidence(
    text: str, spans: Sequence[EntitySpan], cnpjs: Sequence[tuple[int, int, str]], written: dict[str, Counter[str]]
) -> dict[int, str]:
    """Prova de CNPJ de cada span de empresa (regra 1): indice do span -> raiz. Acumula em ``written`` o nome escrito
    logo antes de cada CNPJ (as ancoras de contexto da regra 3a)."""

    found: dict[int, str] = {}
    orgs = [(index, span) for index, span in enumerate(spans) if span.entity_type == "ORGANIZATION"]
    if not orgs:
        return found
    by_end: dict[int, int] = {}
    for index, span in orgs:
        by_end.setdefault(span.end, index)
    starts = sorted((span.start, index) for index, span in orgs)
    ends = sorted((span.end, index) for index, span in orgs)
    for start, end, root in cnpjs:
        before = text[max(0, start - _NAME_REACH) : start]
        cut = _name_end(before)
        position = start - len(before) + cut
        index = by_end.get(position)
        if index is None and position > 0 and text[position - 1] == ".":
            index = by_end.get(position - 1)
        # O span que leva o ponto do "Ltda." (o rotulo comeca nele): o fim do span esta logo depois.
        probe = position
        while index is None and probe < start and text[probe] in ".\"”'":
            probe += 1
            index = by_end.get(probe)
        if index is not None:
            found.setdefault(index, root)
        name = _written_name(before[:cut])
        if name:
            written[name][root] += 1
        for span_start, other in starts:
            if span_start < end:
                continue
            if span_start > end + _BRIDGE_REACH:
                break
            if _BRIDGE.match(text[end:span_start]):
                found.setdefault(other, root)
                break
        if index is None and _QUALIFIED_LABEL.search(text[max(0, start - 60) : start]):
            # "Alfa Ltda, pessoa juridica ..., inscrita no CNPJ sob o n. A": o ultimo nome de empresa antes, na mesma
            # frase, sem outro nome de empresa no meio.
            previous = [(span_end, other) for span_end, other in ends if start - _QUALIFIED_REACH <= span_end <= start]
            if previous:
                span_end, other = previous[-1]
                if not _SENTENCE_BREAK.search(text, span_end, start):
                    found.setdefault(other, root)
    return found


def split_shared_names(
    docs: Sequence[tuple[str, Sequence[EntitySpan]]],
) -> tuple[list[list[EntitySpan]], list[SharedForm]]:
    """Separa as ocorrencias da denominacao compartilhada por empresa (ver o modulo). Devolve os spans de cada texto (so a
    ``canonical_key`` das ocorrencias separadas muda) e as formas compartilhadas achadas."""

    spans = [list(items) for _text, items in docs]
    texts = [text for text, _items in docs]
    cnpjs = [_cnpj_roots(text) for text in texts]
    seen_roots: Counter[str] = Counter(root for found in cnpjs for _s, _e, root in found)
    written: dict[str, Counter[str]] = defaultdict(Counter)
    evidence: dict[tuple[int, int], str] = {}
    for number, text in enumerate(texts):
        for index, root in _evidence(text, spans[number], cnpjs[number], written).items():
            evidence[(number, index)] = root
    if not evidence:
        return spans, []

    # Regra 2: formas com prova de 2+ raizes, cada raiz vista 2+ vezes no lote.
    form_roots: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for (number, index), root in evidence.items():
        span = spans[number][index]
        form_roots[(_key(texts[number], span), _form(texts[number], span))][root] += 1
    shared = {
        pair: {root for root in roots if seen_roots[root] >= _MIN_ROOT_SEEN}
        for pair, roots in form_roots.items()
        if pair[1] and len([root for root in roots if seen_roots[root] >= _MIN_ROOT_SEEN]) >= 2
    }
    if not shared:
        return spans, []

    # Ancoras (regra 3a): chave de empresa cujas formas (fora as compartilhadas) tem uma raiz so; nome escrito antes de
    # CNPJ de uma raiz so.
    key_roots: dict[str, set[str]] = defaultdict(set)
    for (key, form), roots in form_roots.items():
        if (key, form) not in shared:
            key_roots[key].update(roots)
    shared_forms = {form for _key_, form in shared}
    patterns = [
        (_name_pattern(name), next(iter(roots)))
        for name, roots in written.items()
        if len(roots) == 1 and " ".join(content_words("empresa", name)) not in shared_forms
    ]

    plan: dict[tuple[str, str], dict[tuple[int, int], str]] = {pair: {} for pair in shared}
    for number, text in enumerate(texts):
        pending: list[tuple[int, tuple[str, str]]] = []
        for index, span in enumerate(spans[number]):
            if span.entity_type != "ORGANIZATION":
                continue
            pair = (_key(text, span), _form(text, span))
            if pair not in shared:
                continue
            root = evidence.get((number, index))
            if root in shared[pair]:
                plan[pair][(number, index)] = "cnpj:" + root
                continue
            decided = _by_context(text, span, spans[number], shared[pair], key_roots, patterns, shared)
            if decided:
                plan[pair][(number, index)] = "contexto:" + decided
            else:
                pending.append((index, pair))
        # Regra 3b: as ocorrencias ja decididas da mesma forma na mesma folha, se de uma raiz so.
        marks = _pages(text)
        for index, pair in pending:
            page = _page_index(marks, spans[number][index].start)
            roots = {
                value.split(":", 1)[1]
                for (doc, other), value in plan[pair].items()
                if doc == number and value != "ambigua" and not value.startswith("folha:")
                and _page_index(marks, spans[number][other].start) == page
            }
            plan[pair][(number, index)] = ("folha:" + next(iter(roots))) if len(roots) == 1 else "ambigua"

    results: list[SharedForm] = []
    for pair, decisions in plan.items():
        key, form = pair
        votes = Counter(value.split(":", 1)[1] for value in decisions.values() if value != "ambigua")
        if len(votes) < 2 and "ambigua" not in decisions.values():
            continue  # todas as ocorrencias ficaram com uma raiz so: nada a separar
        roots = tuple(sorted(shared[pair]))
        main = votes.most_common(1)[0][0] if votes else ""
        item = SharedForm(key=key, form=form, roots=roots, main_root=main)
        for root in roots:
            item.keys[root] = key if root == main else f"{key}{ROOT_SUFFIX}{root}"
        for (number, index), value in decisions.items():
            span = spans[number][index]
            if value == "ambigua":
                item.ambiguous_key = f"{key}{AMBIGUOUS_SUFFIX}"
                new_key = item.ambiguous_key
                item.counts["sem_contexto"] += 1
            else:
                method, root = value.split(":", 1)
                new_key = item.keys[root]
                item.counts[method] += 1
            if new_key != _key(texts[number], span):
                spans[number][index] = replace(span, canonical_key=new_key)
        results.append(item)
    return spans, results


def _by_context(
    text: str,
    span: EntitySpan,
    others: Sequence[EntitySpan],
    candidates: set[str],
    key_roots: dict[str, set[str]],
    patterns: Sequence[tuple[re.Pattern[str], str]],
    shared: dict[tuple[str, str], set[str]],
) -> str:
    """Regra 3a: a raiz do outro nome da empresa ligado a ocorrencia por expressao de denominacao, ate ``_NEAR``
    caracteres, sem outro nome de empresa no meio. Uma raiz so decide; duas, ou nenhuma, = "" (ambigua)."""

    lo, hi = max(0, span.start - _NEAR), min(len(text), span.end + _NEAR)
    names: list[tuple[int, int, str]] = []  # (inicio, fim, raiz ou "" para nome de empresa sem raiz conhecida)
    for other in others:
        if other is span or other.entity_type != "ORGANIZATION" or other.end <= lo or other.start >= hi:
            continue
        if other.start < span.end and span.start < other.end:
            continue
        key = _key(text, other)
        roots = key_roots.get(key, set()) if (key, _form(text, other)) not in shared else set()
        names.append((other.start, other.end, next(iter(roots)) if len(roots) == 1 else ""))
    window = text[lo:hi]
    for pattern, root in patterns:
        for match in pattern.finditer(window):
            start, end = lo + match.start(), lo + match.end()
            if start < span.end and span.start < end:
                continue
            if any(start < other_end and other_start < end for other_start, other_end, _root in names):
                continue  # o nome escrito ja e um span (ou esta dentro de um)
            names.append((start, end, root))
    found: set[str] = set()
    names.sort()
    for number, (start, end, root) in enumerate(names):
        gap_start, gap_end = (end, span.start) if end <= span.start else (span.end, start)
        gap = text[gap_start:gap_end]
        if not _CUE.search(gap):
            continue
        if any(gap_start <= other_start and other_end <= gap_end for other_start, other_end, _r in names):
            continue  # outro nome de empresa no meio: enumeracao, nao denominacao
        linked = [root]
        # "denominacao anterior da X e da Y": o nome que vem em seguida, so com "e"/virgula no meio, tambem foi ligado.
        chain = names[number + 1 :] if start >= span.end else list(reversed(names[:number]))
        edge = (end, start)
        for other_start, other_end, other_root in chain:
            between = text[edge[0] : other_start] if start >= span.end else text[other_end : edge[1]]
            if not _ENUMERATION.fullmatch(between):
                break
            linked.append(other_root)
            edge = (other_end, other_start)
        if any(not item or item not in candidates for item in linked):
            if any(item in candidates for item in linked):
                return ""  # um dos nomes ligados nao e de nenhuma das empresas (ou nao se sabe): na duvida, ambigua
            continue
        found.update(linked)
    return next(iter(found)) if len(found) == 1 else ""


def audit_lines(found: Sequence[SharedForm]) -> list[str]:
    """AUDITORIA (so contagens)."""

    if not found:
        return ["Denominacao usada por 2+ empresas (CNPJ diferentes): nenhuma."]
    counts: Counter[str] = Counter()
    for item in found:
        counts.update(item.counts)
    return [
        f"Denominacao usada por 2+ empresas (CNPJ diferentes), ocorrencias separadas por empresa: {len(found)} forma(s); "
        f"pelo CNPJ: {counts['cnpj']}; por outro nome da empresa ligado por 'denominada/denominacao': "
        f"{counts['contexto']}; pela mesma folha: {counts['folha']}; sem contexto que decida (token proprio, "
        f"ambigua): {counts['sem_contexto']}."
    ]


def append_shared_names_section(export_dir: Path, found: Sequence[SharedForm], records: Sequence[object]) -> Path | None:
    """ELENCO.md: os tokens da mesma denominacao, de qual CNPJ cada um e o token da ocorrencia ambigua. So tokens."""

    if not found:
        return None
    tokens = {(record.entity_type, record.canonical): record.token for record in records}
    cnpj_token: dict[str, str] = {}
    for record in records:
        if record.entity_type == "BR_CNPJ" and len(record.canonical) == 14:
            cnpj_token.setdefault(record.canonical[:8], record.token)
    lines = [
        "## Mesma denominação usada por empresas diferentes",
        "",
        "A razão social abaixo foi usada, em épocas diferentes, por mais de uma empresa (CNPJ diferentes). O app separou "
        "as ocorrências pela empresa de cada trecho (CNPJ colado, ou outro nome da empresa ligado por \"na época "
        "denominada\"/\"denominação anterior\"). Os tokens da mesma linha são a MESMA denominação, mas NÃO a mesma "
        "empresa; o token \"sem contexto\" pode ser qualquer uma delas: confira a folha.",
        "",
    ]
    for item in found:
        parts = []
        for root in item.roots:
            token = tokens.get(("ORGANIZATION", item.keys.get(root, "")), "")
            if token:
                parts.append(f"{token} = a denominação quando usada pela empresa do {cnpj_token.get(root, 'CNPJ não trocado')}")
        ambiguous = tokens.get(("ORGANIZATION", item.ambiguous_key), "") if item.ambiguous_key else ""
        if ambiguous:
            parts.append(f"{ambiguous} = a mesma denominação sem contexto (qualquer uma delas)")
        if len(parts) >= 2:
            lines.append("- " + "; ".join(parts) + ".")
    if not any(line.startswith("- ") for line in lines):
        return None
    target = Path(export_dir) / CAST_NAME
    existing = target.read_text(encoding="utf-8").rstrip("\n") if target.is_file() else ""
    target.write_text((existing + "\n\n" if existing else "") + "\n".join(lines) + "\n", encoding="utf-8")
    return target

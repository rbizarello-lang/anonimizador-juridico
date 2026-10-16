"""Fusao de identidade no elenco (H9-h, restrita): varias entradas da MESMA pessoa ou empresa viram uma so.

Politica "na duvida, separado" (decisao do condutor, 16/10/2026). A primeira versao (prefixo, sufixo, iniciais, palavra
do meio omitida, similaridade global) produziu no lote real fusoes indevidas que cada correcao pontual reabria: nomes
justapostos numa celula ou num bloco de assinaturas faziam de ponte entre duas pessoas, razoes sociais que diferem so
na sigla inicial foram fundidas, o nome do meio de uma magistrada casou com a ponta da variante de uma parte, um
prenome fundiu com uma firma individual. A fusao agora so aceita IDENTIDADE da forma inteira:

- pessoa: formas IGUAIS depois de normalizar (caixa, acentos, espacos, hifen de quebra de linha, pontuacao, titulo); ou
  grafia de OCR com o MESMO numero de palavras (2+), cada palavra igual ou a mesma salvo uma letra de OCR (nunca o par
  Adriano/Adriana), com ao menos uma palavra exata; ou o mesmo nome canonico do LLM (o nome de uma entrada e variante
  da outra). Todo elo passa pelo NOME de uma das entradas: a mesma variante repetida em duas entradas nao liga nenhuma. Nada de prefixo, sufixo, iniciais, palavra do meio omitida nem forma "forte" contida em outra;
- empresa: razoes sociais iguais depois de normalizar caixa, acentos, pontuacao, sufixo societario e as abreviaturas
  imp/exp/ind/com/cia; ou OCR com o mesmo numero de palavras, cada uma igual ou >= 0,9 e com ao menos uma exata; ou o
  mesmo nome canonico do LLM. A marca curta segue ligada pela H9-g (``VariantPropagator``), fora daqui;
- pessoa e empresa nunca se fundem. A MESMA forma escrita como pessoa e como empresa vira uma entrada EMPRESA;
- forma que junta duas entidades nunca serve para fundir: pessoa com "e", "/", "&", virgula ou ponto e virgula, ou toda
  em minuscula; empresa com "/", "&" ou ";" (fora o "S/A"), com " e " entre duas razoes sociais, e qualquer forma que
  contenha, inteiros e sem sobreposicao, os nomes de duas entradas do elenco (nomes justapostos);
- a decisao de unir dois grupos compara as formas ORIGINAIS de cada membro (as da entrada antes de qualquer fusao
  desta chamada), e todo membro de um grupo tem de casar com todo membro do outro: a fusao nao e transitiva por forma
  acumulada, nem por corrente de grafias (A~B, B~C, A distinto de C).

O caminho da "fusao fraca" (prefixo de 2 palavras com prova no texto e veto de parentesco) saiu: sem prefixo, nao ha
mais o que provar. Entrada fixada pelo usuario ou da lista de alvos nao entra.

Acao da entrada fundida (``merged_action``): "manter" vence se algum membro e cargo publico confirmado (juizo do lote ou
papel de magistrado, servidor, procurador, orgao) ou se todos ja eram "manter"; senao, tokenizar.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from difflib import SequenceMatcher

from anonimizador_juridico.pseudonymizer import fold_lexical

from .batch_propagation import _CONNECTIVES, _close
from .roster import KEEP, TOKENIZE, Roster, RosterEntry, entity_core, has_token

_WORD = re.compile(r"[^\W\d_]+")
_LINE_HYPHEN = re.compile(r"(?<=\w)-\s+(?=\w)")
_PUBLIC_ROLES = frozenset({"magistrado", "servidor_publico", "procurador_publico", "orgao_publico"})
_PRIVATE_ROLES = frozenset({"socio", "parte", "advogado"})

_ABBREVIATIONS = {"imp": "importacao", "exp": "exportacao", "ind": "industria", "com": "comercio", "cia": "companhia"}
_RATIO_PERSON = 0.85
_RATIO_COMPANY = 0.9


def _words(kind: str, value: str) -> tuple[str, ...]:
    """Palavras dobradas da forma inteira (sem titulo nem sufixo societario; conectivo de dentro fica, o do fim sai).
    Razao social: "Imp. e Exp." e "Importacao e Exportacao" sao a mesma (R3)."""

    joined = _LINE_HYPHEN.sub("", value)
    words = _WORD.findall(fold_lexical(entity_core(kind, joined)))
    if kind == "empresa":
        words = [_ABBREVIATIONS.get(word, word) for word in words]
    while words and words[-1] in _CONNECTIVES:
        words.pop()
    return tuple(words)


def content_words(kind: str, value: str) -> list[str]:
    """Palavras de conteudo dobradas (sem titulo, sufixo societario nem conectivo)."""

    return [word for word in _words(kind, value) if word not in _CONNECTIVES]


def _written(value: str) -> tuple[str, ...]:
    """Forma como esta escrita (com sufixo societario), so dobrada: compara pessoa com empresa (item 3)."""

    return tuple(_WORD.findall(fold_lexical(_LINE_HYPHEN.sub("", value))))


def _word_close(left: str, right: str, kind: str, first: bool) -> bool:
    if left == right:
        return True
    person = kind == "pessoa"
    if not _close(left, right, person=person, first=first and person):
        return False
    return SequenceMatcher(None, left, right).ratio() >= (_RATIO_PERSON if person else _RATIO_COMPANY)


def _ocr_match(left: tuple[str, ...], right: tuple[str, ...], kind: str) -> bool:
    """Mesmo numero de palavras (2+), cada uma igual ou a mesma salvo OCR, com ao menos uma palavra exata."""

    if len(left) < 2 or len(left) != len(right) or left == right:
        return False
    if not any(a == b for a, b in zip(left, right)):
        return False
    return all(_word_close(a, b, kind, number == 0) for number, (a, b) in enumerate(zip(left, right)))


_BRIDGE = re.compile(r"\s[eE&]\s|[/&,;]")
_COMPANY_BRIDGE_CHARS = re.compile(r"[/&;]")
_SA = re.compile(r"\bs\s*/\s*a\b", re.IGNORECASE)
_SOCIETARY = re.compile(r"(?i)\b(?:ltda|eireli|epp|me|s\.?\s*a)\b\.?")
_CORPORATE_TAIL = re.compile(r"(?i)[\s,.\-]+(?:ltda|eireli|epp)\.?\s*$|\bs\s*/\s*a\b|\bs\.a\.?\s*$")
_CORPORATE_UPPER = re.compile(r"[\s,]+(?:ME|EPP)\.?\s*$")
_CORPORATE_WORDS = frozenset(
    "agroflorestal agropecuaria agroindustrial comercio industria industrias importacao exportacao participacoes holding "
    "transportes construtora incorporadora distribuidora laticinios cooperativa associacao fundacao empreendimentos "
    "madeireira siderurgica".split()
)


def is_bridge_form(kind: str, value: str) -> bool:
    """Variante que nao pode ligar identidades (R1): pessoa com "e", "/", "&", virgula ou ponto e virgula no meio
    (duas pessoas numa grafia) ou escrita toda em minuscula ("fatica": palavra de texto, nao nome). Empresa: "/", "&"
    ou ";" (o "S/A" do sufixo nao conta) e " e " entre duas razoes sociais ("Alfa Ltda e Beta Ltda")."""

    if kind == "empresa":
        bare = _SA.sub(" ", value)
        if _COMPANY_BRIDGE_CHARS.search(bare):
            return True
        parts = re.split(r"\s[eE]\s", bare)
        return len(parts) > 1 and sum(1 for part in parts if _SOCIETARY.search(part)) >= 2
    if kind != "pessoa":
        return False
    return bool(_BRIDGE.search(value)) or not any(char.isupper() for char in value)


def is_corporate(value: str) -> bool:
    """Nome com sufixo societario (Ltda, S/A, EIRELI, ME, EPP) ou palavra de razao social (R2): e empresa."""

    if _CORPORATE_TAIL.search(value) or _CORPORATE_UPPER.search(value):
        return True
    return any(word in _CORPORATE_WORDS for word in _WORD.findall(fold_lexical(value)))


def _contains_two_names(words: tuple[str, ...], names: Sequence[tuple[str, ...]]) -> bool:
    """A forma contem, inteiros e sem sobreposicao, os nomes (2+ palavras) de duas entradas do elenco?"""

    spans: set[tuple[int, int]] = set()
    for name in names:
        size = len(name)
        if size < 2 or name == words:
            continue
        for start in range(len(words) - size + 1):
            if words[start : start + size] == name:
                spans.add((start, start + size))
    ordered = sorted(spans)
    return any(
        first[1] <= second[0] for position, first in enumerate(ordered) for second in ordered[position + 1 :]
    )


class _Cluster:
    """Uma entrada do elenco com as formas ORIGINAIS dela (nome e variantes) e o tipo (R2: sufixo societario)."""

    __slots__ = ("key", "kind", "forms", "name_form", "retyped", "written", "values")

    def __init__(self, entry: RosterEntry, names: dict[str, list[tuple[str, ...]]]) -> None:
        self.key = entry.key
        self.kind = entry.kind
        self.retyped = False
        if entry.kind == "pessoa" and any(is_corporate(value) for value in (entry.name, *entry.variants)):
            self.kind = "empresa"
            self.retyped = True
        self.values = [
            value for value in (entry.name, *entry.variants) if not has_token(value) and not is_bridge_form(self.kind, value)
        ]
        self.forms: set[tuple[str, ...]] = set()
        self.written: set[tuple[str, ...]] = set()
        self.name_form: tuple[str, ...] = ()
        self._build(entry, names)

    def _build(self, entry: RosterEntry, names: dict[str, list[tuple[str, ...]]]) -> None:
        pool = names.get(self.kind, [])
        self.forms = set()
        self.written = set()
        for value in self.values:
            words = _words(self.kind, value)
            if not words or _contains_two_names(words, pool):
                continue
            self.forms.add(words)
            self.written.add(_written(value))
            if value == entry.name:
                self.name_form = words

    def retype_company(self, entry: RosterEntry, names: dict[str, list[tuple[str, ...]]]) -> None:
        self.kind = "empresa"
        self.retyped = True
        self.values = [value for value in self.values if not is_bridge_form("empresa", value)]
        self._build(entry, names)


def _eligible(entry: RosterEntry) -> bool:
    return (
        entry.kind in {"pessoa", "empresa"}
        and not entry.pinned
        and entry.source != "lista"
        and bool(entry.name.strip())
    )


def _relation(left: _Cluster, right: _Cluster) -> str:
    """"igual" (os dois nomes iguais depois de normalizar), "canonico" (o nome de uma e variante da outra: o LLM deu o
    mesmo nome canonico), "ocr" (grafia de OCR de um nome contra uma forma da outra) ou "" (separadas). Todo elo passa
    pelo NOME de uma das entradas: a mesma variante repetida em duas entradas nao liga nenhuma das duas."""

    if left.kind != right.kind or not left.name_form or not right.name_form:
        return ""
    if left.name_form == right.name_form:
        return "igual"
    if left.name_form in right.forms or right.name_form in left.forms:
        return "canonico"
    if any(_ocr_match(left.name_form, form, left.kind) for form in right.forms) or any(
        _ocr_match(form, right.name_form, left.kind) for form in left.forms
    ):
        return "ocr"
    return ""


def fuse_identities(roster: Roster, texts: Sequence[str] | None = None) -> dict[str, str]:
    """Funde no ``roster`` as entradas da mesma entidade; devolve ``{chave removida: chave que ficou}``.

    ``texts`` (os textos do lote) ficou so por compatibilidade com quem chama: a politica restrita nao le o texto."""

    del texts
    entries = [entry for entry in roster.entries if _eligible(entry)]
    names: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for entry in roster.entries:
        if entry.kind in {"pessoa", "empresa"} and not has_token(entry.name):
            kind = "empresa" if entry.kind == "pessoa" and is_corporate(entry.name) else entry.kind
            names[kind].append(_words(kind, entry.name))
    clusters = [_Cluster(entry, names) for entry in entries]
    by_key = {entry.key: entry for entry in entries}
    # Item 3: a mesma forma escrita como pessoa e como empresa vira uma entrada EMPRESA.
    company_written = {form for cluster in clusters if cluster.kind == "empresa" for form in cluster.written}
    company_written |= {
        _written(value)
        for entry in roster.entries
        if entry.kind == "empresa" and not _eligible(entry)
        for value in (entry.name, *entry.variants)
    }
    for cluster in clusters:
        if cluster.kind == "pessoa" and cluster.written & company_written:
            cluster.retype_company(by_key[cluster.key], names)
    clusters = [cluster for cluster in clusters if cluster.forms]
    # Arestas: nome canonico e igualdade primeiro, OCR depois (grafia de OCR nao disputa com a identidade exata).
    matches: dict[tuple[int, int], str] = {}
    edges: dict[str, list[tuple[int, int]]] = {"canonico": [], "igual": [], "ocr": []}
    for left in range(len(clusters)):
        for right in range(left + 1, len(clusters)):
            relation = _relation(clusters[left], clusters[right])
            if relation:
                matches[(left, right)] = relation
                edges[relation].append((left, right))
    parent = list(range(len(clusters)))
    members: dict[int, list[int]] = {number: [number] for number in range(len(clusters))}

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def join(first: int, second: int, relation: str) -> None:
        root_a, root_b = find(first), find(second)
        if root_a == root_b:
            return
        for a in members[root_a]:
            for b in members[root_b]:
                if (min(a, b), max(a, b)) not in matches:
                    return
        keep, drop = min(root_a, root_b), max(root_a, root_b)
        parent[drop] = keep
        members[keep].extend(members.pop(drop))
        roster.identity_reasons[relation] = roster.identity_reasons.get(relation, 0) + 1

    for relation in ("canonico", "igual"):
        for left, right in edges[relation]:
            join(left, right, relation)
    # OCR: so quando a grafia nao e ambigua. Os vizinhos de OCR de um grupo (fora o outro) tem de ser os mesmos do outro
    # grupo (o triangulo de grafias entra; a corrente A~B, B~C com A distinto de C fica toda separada).
    root0 = {number: find(number) for number in range(len(clusters))}
    neighbours: dict[int, set[int]] = defaultdict(set)
    for left, right in edges["ocr"]:
        if root0[left] != root0[right]:
            neighbours[root0[left]].add(root0[right])
            neighbours[root0[right]].add(root0[left])
    for left, right in edges["ocr"]:
        root_a, root_b = root0[left], root0[right]
        if root_a != root_b and neighbours[root_a] - {root_b} == neighbours[root_b] - {root_a}:
            join(left, right, "ocr")
    grouped: dict[int, list[int]] = defaultdict(list)
    for number in range(len(clusters)):
        grouped[find(number)].append(number)
    removed: dict[str, str] = {}
    for group in grouped.values():
        group_clusters = [clusters[number] for number in group]
        keys = [cluster.key for cluster in group_clusters]
        retyped = any(cluster.retyped for cluster in group_clusters)
        kind = group_clusters[0].kind
        if len(keys) < 2:
            if retyped:
                roster.retype(keys[0], kind)
            continue
        survivor = _survivor(roster, keys, kind)
        absorbed = [key for key in keys if key != survivor]
        roster.merge(survivor, absorbed)
        if retyped:
            roster.retype(survivor, kind)
        removed.update({key: survivor for key in absorbed})
    return removed


def _survivor(roster: Roster, keys: Sequence[str], kind: str = "") -> str:
    """A entrada de nome mais completo (mais palavras de conteudo, depois mais variantes) fica."""

    def rank(key: str) -> tuple[int, int, int, int, str]:
        entry = roster.get(key)
        assert entry is not None
        return (0 if not kind or entry.kind == kind else 1, -len(content_words(entry.kind, entry.name)), -len(entry.variants), -len(entry.name), key)

    return min(keys, key=rank)


def merged_action(entries: Sequence[RosterEntry], locked: Sequence[bool]) -> str:
    """"manter" so com (a) membro travado pelo juizo do lote, (b) todos ja "manter", ou (c) papel publico em algum
    membro e nenhum papel privado especifico (socio, parte, advogado) em nenhum; "terceiro" e "desconhecido" nao
    vetam. Um socio com uma grafia rotulada de procurador fica tokenizado, como a ``policy_action`` faria."""

    if any(locked):
        return KEEP
    if all(entry.action == KEEP for entry in entries):
        return KEEP
    roles = {role for entry in entries for role in entry.roles}
    if roles & _PUBLIC_ROLES and not roles & _PRIVATE_ROLES:
        if any(entry.action == KEEP and _PUBLIC_ROLES & set(entry.roles) for entry in entries):
            return KEEP
    return TOKENIZE

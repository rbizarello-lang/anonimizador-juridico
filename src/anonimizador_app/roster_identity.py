"""Fusao de identidade no elenco (H9-h): varias entradas da MESMA pessoa ou empresa viram uma so.

Medido no lote IDPJ real depois da H9-g: o socio principal aparecia com 11 tokens no lote e 3 na mesma folha, e o
prenome dele ficava em claro 44 vezes porque a propagacao recusava por ambiguidade (261 recusas): o nome estava
partido em varias entradas do elenco ("Fulano Beltrano de Tal", "FULANO BELTRANO DE", "Fulano de Tala", "Fulano").
Um servidor publico tinha a entrada principal "manter" e duas grafias "tokenizar".

A fusao junta, num elenco ja montado, as entradas que o texto prova serem a mesma entidade:

- pessoa: nome (ou variante) de 2+ palavras de conteudo que e prefixo, sufixo, iniciais ou grafia (similaridade >= 0,85,
  so a troca de uma letra por OCR, nunca o par Adriano/Adriana) de outro de 2+ palavras; nome de uma palavra so se ela
  e a primeira ou a ultima palavra de UMA unica entrada de 2+ palavras;
- empresa: razao social (sem o sufixo societario) que e prefixo da outra ou grafia dela com similaridade >= 0,85. A
  marca de uma palavra segue sendo ligada pela H9-g (``VariantPropagator``).

Regra de ouro: so funde quando ha UM candidato. Dois nomes que dividem so o sobrenome ou so o prenome nunca se
fundem, e o nome curto que cabe em duas entradas fica como esta (na duvida, separado). Entrada fixada pelo usuario
ou da lista de alvos nao entra. Acao da entrada fundida: "manter" vence se algum membro e cargo publico confirmado
(juizo do lote ou papel de magistrado, servidor, procurador, orgao) ou se todos ja eram "manter"; senao, tokenizar.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from difflib import SequenceMatcher

from anonimizador_juridico.pseudonymizer import fold_lexical

from .batch_propagation import _CONNECTIVES, _GENERATION, _close
from .roster import KEEP, TOKENIZE, Roster, RosterEntry, entity_core, has_token

_WORD = re.compile(r"[^\W\d_]+")
_RATIO = 0.85
_PUBLIC_ROLES = frozenset({"magistrado", "servidor_publico", "procurador_publico", "orgao_publico"})
_PRIVATE_ROLES = frozenset({"socio", "parte", "advogado"})
_MAX_PASSES = 6


def content_words(kind: str, value: str) -> list[str]:
    """Palavras de conteudo dobradas (sem titulo, sufixo societario nem conectivo)."""

    core = fold_lexical(entity_core(kind, value))
    return [word for word in _WORD.findall(core) if word not in _CONNECTIVES]


def _folded(kind: str, value: str) -> str:
    """Forma dobrada com os conectivos de dentro (a similaridade conta o nome como foi escrito)."""

    words = _WORD.findall(fold_lexical(entity_core(kind, value)))
    while words and words[-1] in _CONNECTIVES:
        words.pop()
    return " ".join(words)


def _generation(words: Sequence[str]) -> frozenset[str]:
    return frozenset(word for word in words if word in _GENERATION)


def _word_match(left: str, right: str, *, first: bool, person: bool) -> str:
    """"exata", "inicial", "grafia" ou "" para um par de palavras na mesma posicao."""

    if left == right:
        return "exata"
    if person and (len(left) == 1 and right.startswith(left) or len(right) == 1 and left.startswith(right)):
        return "inicial"
    if _close(left, right, person=person, first=first and person):
        return "grafia"
    return ""


def _aligned(short: Sequence[str], long: Sequence[str], person: bool) -> tuple[bool, bool]:
    """(casa, usou grafia): primeira e ultima palavra batem e as do meio da curta aparecem, na ordem, na longa."""

    first = _word_match(short[0], long[0], first=True, person=person)
    last = _word_match(short[-1], long[-1], first=False, person=person)
    if not first or not last:
        return False, False
    kinds = [first, last]
    position = 1
    for word in short[1:-1]:
        found = ""
        while position < len(long) - 1 and not found:
            found = _word_match(word, long[position], first=False, person=person)
            position += 1
        if not found:
            return False, False
        kinds.append(found)
    if "exata" not in kinds:
        return False, False
    return True, "grafia" in kinds


def person_forms_match(short_words: list[str], long_words: list[str], short_text: str, long_text: str) -> bool:
    """A forma ``short`` (de 2+ palavras de conteudo, nao maior que ``long``) e a mesma pessoa que ``long``?"""

    if len(short_words) < 2 or len(short_words) > len(long_words):
        return False
    if _generation(short_words) != _generation(long_words):
        return False
    if short_words == long_words:
        return True
    size = len(short_words)
    if long_words[:size] == short_words or long_words[-size:] == short_words:
        return True
    matched, spelled = _aligned(short_words, long_words, True)
    if not matched:
        return False
    return not spelled or SequenceMatcher(None, short_text, long_text).ratio() >= _RATIO


def company_forms_match(short_words: list[str], long_words: list[str], short_text: str, long_text: str) -> bool:
    """Razao social curta e prefixo da longa, ou a mesma com uma letra trocada por OCR (>= 0,85)."""

    if len(short_words) < 2 or len(short_words) > len(long_words):
        return False
    if short_words == long_words:
        return True
    if long_words[: len(short_words)] == short_words:
        return True
    if len(short_words) != len(long_words):
        return False
    diffs = [(a, b) for a, b in zip(short_words, long_words) if a != b]
    if not diffs or not all(_close(a, b, person=False) for a, b in diffs):
        return False
    return SequenceMatcher(None, short_text, long_text).ratio() >= _RATIO


class _Cluster:
    __slots__ = ("keys", "kind", "forms", "size")

    def __init__(self, entry: RosterEntry) -> None:
        self.keys = [entry.key]
        self.kind = entry.kind
        self.forms: list[tuple[list[str], str]] = []
        self.size = 0
        for value in (entry.name, *entry.variants):
            if has_token(value):
                continue
            words = content_words(entry.kind, value)
            if words:
                self.forms.append((words, _folded(entry.kind, value)))
        self.size = max((len(words) for words, _text in self.forms), default=0)

    def merge(self, other: "_Cluster") -> None:
        self.keys.extend(other.keys)
        self.forms.extend(other.forms)
        self.size = max(self.size, other.size)

    def words(self) -> set[str]:
        return {word for form, _text in self.forms for word in form}


def _buckets(words: Sequence[str]) -> set[str]:
    out: set[str] = set()
    for word in words:
        out.add(word)
        if len(word) >= 3:
            out.add("^" + word[:2])
            out.add("$" + word[-2:])
    return out


def _related(short: _Cluster, long: _Cluster) -> bool:
    """``short`` cabe em ``long`` como a mesma entidade (tamanho de ``short`` <= tamanho de ``long``)."""

    person = short.kind == "pessoa"
    match = person_forms_match if person else company_forms_match
    if short.size >= 2:
        for short_words, short_text in short.forms:
            if len(short_words) < 2:
                continue
            for long_words, long_text in long.forms:
                if match(short_words, long_words, short_text, long_text):
                    return True
        return False
    if not person:
        return False
    # Pessoa de uma palavra so: a primeira ou a ultima palavra de uma entrada de 2+ palavras.
    word = short.forms[0][0][0] if short.forms else ""
    if len(word) < 3:
        return False
    for long_words, _text in long.forms:
        if len(long_words) < 2:
            continue
        for edge, first in ((long_words[0], True), (long_words[-1], False)):
            if _word_match(word, edge, first=first, person=True) in {"exata", "grafia"}:
                return True
    return False


def _eligible(entry: RosterEntry) -> bool:
    return (
        entry.kind in {"pessoa", "empresa"}
        and not entry.pinned
        and entry.source != "lista"
        and bool(entry.name.strip())
    )


def fuse_identities(roster: Roster) -> dict[str, str]:
    """Funde no ``roster`` as entradas da mesma entidade; devolve ``{chave removida: chave que ficou}``."""

    clusters = [_Cluster(entry) for entry in roster.entries if _eligible(entry)]
    clusters = [cluster for cluster in clusters if cluster.size >= 1]
    for _ in range(_MAX_PASSES):
        index: dict[tuple[str, str], list[int]] = defaultdict(list)
        for number, cluster in enumerate(clusters):
            for bucket in _buckets(sorted(cluster.words())):
                index[(cluster.kind, bucket)].append(number)
        unions: list[tuple[int, int]] = []
        for number, cluster in enumerate(clusters):
            near = {
                other
                for bucket in _buckets(sorted(cluster.words()))
                for other in index.get((cluster.kind, bucket), ())
                if other != number
            }
            candidates = [
                other
                for other in sorted(near)
                if clusters[other].size >= cluster.size and _related(cluster, clusters[other])
            ]
            if len(candidates) == 1:
                unions.append((number, candidates[0]))
        if not unions:
            break
        parent = list(range(len(clusters)))

        def find(item: int) -> int:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        for left, right in unions:
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                parent[max(root_left, root_right)] = min(root_left, root_right)
        grouped: dict[int, list[int]] = defaultdict(list)
        for number in range(len(clusters)):
            grouped[find(number)].append(number)
        merged: list[_Cluster] = []
        for root in sorted(grouped):
            head = clusters[root]
            for number in grouped[root]:
                if number != root:
                    head.merge(clusters[number])
            merged.append(head)
        clusters = merged
    removed: dict[str, str] = {}
    for cluster in clusters:
        if len(cluster.keys) < 2:
            continue
        survivor = _survivor(roster, cluster.keys)
        absorbed = [key for key in cluster.keys if key != survivor]
        roster.merge(survivor, absorbed)
        removed.update({key: survivor for key in absorbed})
    return removed


def _survivor(roster: Roster, keys: Sequence[str]) -> str:
    """A entrada de nome mais completo (mais palavras de conteudo, depois mais variantes) fica."""

    def rank(key: str) -> tuple[int, int, int, str]:
        entry = roster.get(key)
        assert entry is not None
        return (-len(content_words(entry.kind, entry.name)), -len(entry.variants), -len(entry.name), key)

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

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
import unicodedata
from bisect import bisect_right
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


_ABBREVIATIONS = {"imp": "importacao", "exp": "exportacao", "ind": "industria", "com": "comercio", "cia": "companhia"}
_RATIO_COMPANY = 0.9


def _expand(kind: str, words: list[str]) -> list[str]:
    """Razao social: "Imp. e Exp." e "Importacao e Exportacao" sao a mesma razao social (R3)."""

    return [_ABBREVIATIONS.get(word, word) for word in words] if kind == "empresa" else words


def content_words(kind: str, value: str) -> list[str]:
    """Palavras de conteudo dobradas (sem titulo, sufixo societario nem conectivo)."""

    core = fold_lexical(entity_core(kind, value))
    return _expand(kind, [word for word in _WORD.findall(core) if word not in _CONNECTIVES])


def _folded(kind: str, value: str) -> str:
    """Forma dobrada com os conectivos de dentro (a similaridade conta o nome como foi escrito)."""

    words = _expand(kind, _WORD.findall(fold_lexical(entity_core(kind, value))))
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


def person_match_kind(short_words: list[str], long_words: list[str], short_text: str, long_text: str) -> str:
    """"" (nao e a mesma pessoa), "forte" ou "fraca". Fraca = a curta tem so 2 palavras de conteudo e casa por
    prefixo, sufixo, iniciais ou palavra do meio omitida (nao por igualdade nem por grafia de OCR): mae e filha, pai
    e filho com o mesmo prenome e um sobrenome comum casam assim, e isso exige prova no texto (``_Evidence``)."""

    if len(short_words) < 2 or len(short_words) > len(long_words):
        return ""
    if _generation(short_words) != _generation(long_words):
        return ""
    if short_words == long_words:
        return "forte"
    size = len(short_words)
    weak = "fraca" if size == 2 else "forte"
    if long_words[:size] == short_words or long_words[-size:] == short_words:
        return weak
    matched, spelled = _aligned(short_words, long_words, True)
    if not matched:
        return ""
    if not spelled:
        return weak
    return "forte" if SequenceMatcher(None, short_text, long_text).ratio() >= _RATIO else ""


def person_forms_match(short_words: list[str], long_words: list[str], short_text: str, long_text: str) -> bool:
    """A forma ``short`` (de 2+ palavras de conteudo, nao maior que ``long``) e a mesma pessoa que ``long``?"""

    return bool(person_match_kind(short_words, long_words, short_text, long_text))


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
    if SequenceMatcher(None, short_text, long_text).ratio() >= _RATIO_COMPANY:
        return True
    diffs = [(a, b) for a, b in zip(short_words, long_words) if a != b]
    if not diffs or not all(_close(a, b, person=False) for a, b in diffs):
        return False
    return SequenceMatcher(None, short_text, long_text).ratio() >= _RATIO


_BRIDGE = re.compile(r"\s[eE&]\s|[/&,;]")
_CORPORATE_TAIL = re.compile(r"(?i)[\s,.\-]+(?:ltda|eireli|epp)\.?\s*$|\bs\s*/\s*a\b|\bs\.a\.?\s*$")
_CORPORATE_UPPER = re.compile(r"[\s,]+(?:ME|EPP)\.?\s*$")
_CORPORATE_WORDS = frozenset(
    "agroflorestal agropecuaria agroindustrial comercio industria industrias importacao exportacao participacoes holding "
    "transportes construtora incorporadora distribuidora laticinios cooperativa associacao fundacao empreendimentos "
    "madeireira siderurgica".split()
)


def is_bridge_form(kind: str, value: str) -> bool:
    """Variante que nao pode ligar identidades (R1): pessoa com "e", "/", "&", virgula ou ponto e virgula no meio
    (duas pessoas numa grafia) ou escrita toda em minuscula ("fatica": palavra de texto, nao nome)."""

    if kind != "pessoa":
        return False
    return bool(_BRIDGE.search(value)) or not any(char.isupper() for char in value)


def is_corporate(value: str) -> bool:
    """Nome com sufixo societario (Ltda, S/A, EIRELI, ME, EPP) ou palavra de razao social (R2): e empresa."""

    if _CORPORATE_TAIL.search(value) or _CORPORATE_UPPER.search(value):
        return True
    return any(word in _CORPORATE_WORDS for word in _WORD.findall(fold_lexical(value)))


class _Cluster:
    __slots__ = ("keys", "kind", "forms", "size", "retyped")

    def __init__(self, entry: RosterEntry) -> None:
        self.keys = [entry.key]
        self.kind = entry.kind
        self.retyped = False
        self.forms: list[tuple[list[str], str]] = []
        self.size = 0
        if entry.kind == "pessoa" and any(is_corporate(value) for value in (entry.name, *entry.variants)):
            self.kind = "empresa"
            self.retyped = True
        for value in (entry.name, *entry.variants):
            if has_token(value) or is_bridge_form(self.kind, value):
                continue
            words = content_words(self.kind, value)
            if words:
                self.forms.append((words, _folded(self.kind, value)))
        self.size = max((len(words) for words, _text in self.forms), default=0)

    def merge(self, other: "_Cluster") -> None:
        self.retyped = self.retyped or other.retyped
        self.keys.extend(other.keys)
        self.forms.extend(other.forms)
        self.size = max(self.size, other.size)

    def words(self) -> set[str]:
        return {word for form, _text in self.forms for word in form}


def _share_edge(left: _Cluster, right: _Cluster) -> bool:
    """Duas entradas de 2+ palavras so se juntam se alguma forma de uma tem a primeira ou a ultima palavra em comum
    com alguma forma da outra (R1: nunca "Fulano Silva" com "Beltrano Souza" por uma ponte)."""

    if left.size < 2 or right.size < 2:
        return True
    for left_words, _a in left.forms:
        if len(left_words) < 2:
            continue
        for right_words, _b in right.forms:
            if len(right_words) < 2:
                continue
            if left_words[0] == right_words[0] or left_words[-1] == right_words[-1]:
                return True
            if _word_match(left_words[0], right_words[0], first=True, person=left.kind == "pessoa") in {"exata", "grafia"}:
                return True
            if _word_match(left_words[-1], right_words[-1], first=False, person=left.kind == "pessoa") in {"exata", "grafia"}:
                return True
    return False


def _same_company(cluster: _Cluster, candidates: Sequence[int], related: dict, clusters: Sequence[_Cluster]) -> bool:
    """R3: varias entradas candidatas da MESMA razao social (a abreviada, a de OCR, a completa) nao sao ambiguidade:
    todas casam forte com a curta e entre si. Duas marcas diferentes (Comercio x Textil) continuam ambiguas."""

    if any(related[other][0] != "forte" for other in candidates):
        return False
    for position, left in enumerate(candidates):
        for right in candidates[position + 1 :]:
            small, big = sorted((left, right), key=lambda item: clusters[item].size)
            if not (_relation(clusters[small], clusters[big])[0] or _relation(clusters[big], clusters[small])[0]):
                return False
    return True


def _buckets(words: Sequence[str]) -> set[str]:
    out: set[str] = set()
    for word in words:
        out.add(word)
        if len(word) >= 3:
            out.add("^" + word[:2])
            out.add("$" + word[-2:])
    return out


def _relation(short: _Cluster, long: _Cluster) -> tuple[str, list[tuple[str, str]]]:
    """("", []) se ``short`` nao cabe em ``long`` como a mesma entidade (tamanho de ``short`` <= tamanho de
    ``long``); ("forte", []) ou ("fraca", pares) se cabe. Os pares sao os (texto curto, texto longo) so fracos."""

    person = short.kind == "pessoa"
    if short.size >= 2:
        strong = False
        weak: list[tuple[str, str]] = []
        for short_words, short_text in short.forms:
            if len(short_words) < 2:
                continue
            for long_words, long_text in long.forms:
                if person:
                    kind = person_match_kind(short_words, long_words, short_text, long_text)
                else:
                    kind = "forte" if company_forms_match(short_words, long_words, short_text, long_text) else ""
                if kind == "forte":
                    strong = True
                elif kind == "fraca":
                    weak.append((short_text, long_text))
        if strong:
            return "forte", []
        return ("fraca", weak) if weak else ("", [])
    if not person:
        return "", []
    # Pessoa de uma palavra so: a primeira ou a ultima palavra de uma entrada de 2+ palavras.
    word = short.forms[0][0][0] if short.forms else ""
    if len(word) < 3:
        return "", []
    for long_words, _text in long.forms:
        if len(long_words) < 2:
            continue
        for edge, first in ((long_words[0], True), (long_words[-1], False)):
            if _word_match(word, edge, first=first, person=True) in {"exata", "grafia"}:
                return "forte", []
    return "", []


_LETTER = r"[^\W\d_]"
_KINSHIP = re.compile(
    r"(?<!" + _LETTER + r")(?:mae|filh[oa]|pai|irma|irmao|esposa|marido|conjuge|companheir[oa]|neto|neta|avo|tio|tia"
    r"|sobrinh[oa]|genro|nora)s?(?!" + _LETTER + r")"
)
_KINSHIP_WINDOW = 60
_SENTENCE_END = re.compile(r"[.!?;\n]")
_APOSTROPHES = "'\u2019`\u00b4\u02bc\u2032"


def _fold_keep_layout(text: str) -> str:
    """Dobra como ``fold_lexical`` mas sem colapsar espacos nem quebras (a frase importa)."""

    stripped = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
    return "".join(char for char in stripped.casefold() if char not in _APOSTROPHES)


class _Evidence:
    """Prova no texto para a fusao fraca (``person_match_kind`` == "fraca"): parentesco perto e a forma curta
    sozinha. Dobra os textos so quando a primeira fusao fraca aparece."""

    def __init__(self, texts: Sequence[str]) -> None:
        self._raw = list(texts)
        self._folded: list[str] | None = None
        self._ends: dict[int, list[int]] = {}
        self._found: dict[str, list[tuple[int, int, int]]] = {}

    def _docs(self) -> list[str]:
        if self._folded is None:
            self._folded = [_fold_keep_layout(text) for text in self._raw]
        return self._folded

    def occurrences(self, form: str) -> list[tuple[int, int, int]]:
        """(documento, inicio, fim) de cada ocorrencia da forma (palavras inteiras, dobradas)."""

        found = self._found.get(form)
        if found is None:
            words = form.split()
            found = []
            if words:
                pattern = re.compile(
                    r"(?<!" + _LETTER + r")" + r"[\s\-]+".join(re.escape(word) for word in words) + r"(?!" + _LETTER + r")"
                )
                for number, doc in enumerate(self._docs()):
                    found.extend((number, match.start(), match.end()) for match in pattern.finditer(doc))
            self._found[form] = found
        return found

    def _sentence(self, number: int, position: int) -> int:
        ends = self._ends.get(number)
        if ends is None:
            ends = [match.start() for match in _SENTENCE_END.finditer(self._docs()[number])]
            self._ends[number] = ends
        return bisect_right(ends, position - 1)

    def kinship_near(self, forms: Sequence[str]) -> bool:
        """Termo de parentesco a ate 60 caracteres (fora da propria ocorrencia) de qualquer ocorrencia das formas."""

        docs = self._docs()
        for form in forms:
            for number, start, end in self.occurrences(form):
                doc = docs[number]
                before = doc[max(0, start - _KINSHIP_WINDOW) : start]
                after = doc[end : end + _KINSHIP_WINDOW]
                if _KINSHIP.search(before) or _KINSHIP.search(after):
                    return True
        return False

    def short_stands_alone(self, short_forms: Sequence[str], long_forms: Sequence[str]) -> bool:
        """A forma curta aparece ao menos uma vez fora de qualquer ocorrencia da longa, e nunca na mesma frase que
        uma ocorrencia separada da longa."""

        longs = [item for form in long_forms for item in self.occurrences(form)]
        alone = 0
        for form in short_forms:
            for number, start, end in self.occurrences(form):
                if any(n == number and ls < end and start < le for n, ls, le in longs):
                    continue
                sentence = self._sentence(number, start)
                if any(n == number and self._sentence(n, ls) == sentence for n, ls, _le in longs):
                    return False
                alone += 1
        return alone > 0

    def verdict(self, long: "_Cluster", pairs: Sequence[tuple[str, str]]) -> str:
        """"" (pode fundir), "parentesco" ou "prova"."""

        short_forms = sorted({text for text, _long in pairs})
        long_forms = sorted({text for _short, text in pairs} | {text for words, text in long.forms if len(words) > 2})
        if self.kinship_near([*short_forms, *long_forms]):
            return "parentesco"
        if not self.short_stands_alone(short_forms, long_forms):
            return "prova"
        return ""


def _eligible(entry: RosterEntry) -> bool:
    return (
        entry.kind in {"pessoa", "empresa"}
        and not entry.pinned
        and entry.source != "lista"
        and bool(entry.name.strip())
    )


def fuse_identities(roster: Roster, texts: Sequence[str] | None = None) -> dict[str, str]:
    """Funde no ``roster`` as entradas da mesma entidade; devolve ``{chave removida: chave que ficou}``.

    ``texts`` (os textos do lote) liga as salvaguardas da fusao so por prefixo/sufixo de 2 palavras: sem parentesco
    perto e com a forma curta sozinha no texto. Sem ``texts`` elas ficam desligadas."""

    evidence = _Evidence(texts) if texts is not None else None
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
            related = {
                other: _relation(cluster, clusters[other])
                for other in sorted(near)
                if clusters[other].size >= cluster.size
            }
            candidates = [other for other, (kind, _pairs) in related.items() if kind]
            if len(candidates) > 1 and cluster.kind == "empresa" and _same_company(cluster, candidates, related, clusters):
                unions.extend((number, other) for other in candidates)
            elif len(candidates) == 1:
                other = candidates[0]
                kind, pairs = related[other]
                if kind == "fraca":
                    pair_id = (cluster.keys[0], clusters[other].keys[0])
                    verdict = evidence.verdict(clusters[other], pairs) if evidence is not None else ""
                    if verdict == "parentesco":
                        roster.kinship_refused.add(pair_id)
                        continue
                    if verdict == "prova":
                        roster.proof_refused.add(pair_id)
                        continue
                    roster.prefix2_merges.add(pair_id)
                unions.append((number, other))
        if not unions:
            break
        parent = list(range(len(clusters)))
        members: dict[int, list[int]] = {number: [number] for number in range(len(clusters))}

        def find(item: int) -> int:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        for left, right in unions:
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                if not all(_share_edge(clusters[a], clusters[b]) for a in members[root_left] for b in members[root_right]):
                    continue
                keep, drop = min(root_left, root_right), max(root_left, root_right)
                parent[drop] = keep
                members[keep].extend(members.pop(drop))
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
            if cluster.retyped:
                roster.retype(cluster.keys[0], cluster.kind)
            continue
        survivor = _survivor(roster, cluster.keys, cluster.kind)
        absorbed = [key for key in cluster.keys if key != survivor]
        roster.merge(survivor, absorbed)
        if cluster.retyped:
            roster.retype(survivor, cluster.kind)
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

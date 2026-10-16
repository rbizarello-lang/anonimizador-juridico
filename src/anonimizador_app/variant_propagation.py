"""Propagacao de variante aceita no elenco (H9-f; decisao do Operador, revisa o Veredito 2.0).

Ate o H9-e a variante de uma palavra (marca curta da empresa, prenome isolado, sobrenome em "familia X") so
recebia token onde o modelo a apontou: o resto do lote ficava em claro, embora o elenco tivesse a entidade
completa (medido: marca curta em claro 16 a 41 vezes no arquivo A; "marca de uma palavra propagada: 0").

Esta camada leva a variante a TODAS as ocorrencias do lote, com o mesmo token da entrada completa, quando:

- a variante esta ligada a uma entrada COMPLETA do elenco (2+ palavras de conteudo) que o LLM trouxe, ou que o
  motor local achou em 2+ mencoes;
- tem limite de palavra, esta escrita como nome proprio (inicial maiuscula) e nao cai dentro de nome ja achado;
- nao e termo protegido, nem nome de municipio ou estado, nem rotulo de formulario, nem palavra do dicionario
  ``data/palavras_comuns.txt`` (essas so propagam se a entrada completa tiver 3+ mencoes; "Uniao" e ente
  publico nunca);
- pertence a uma entrada so (a mesma palavra em duas entradas e ambigua e fica como estava).

Por que nao reabre o P13 (super-redacao do Veredito 2.0): a palavra nao vem de lista acumulada nem de
particula de razao social; vem de uma entrada completa DESTE lote, com o mesmo token dela, e passa pelas
mesmas travas de geografia, termo protegido e dicionario. O que a regra nao prova fica em claro.

Formas:

- marca curta de empresa: variante de uma palavra igual a primeira palavra de conteudo da razao social, sem
  outra palavra de nome proprio logo depois;
- prenome isolado: primeira palavra do nome da pessoa, so com pista de pessoa a ate 40 caracteres antes
  ("Sr.", "Sra.", "socio", "conjuge", "filho", "o executado", "requerido"...);
- sobrenome em "familia X" / "familias X e Y": so o sobrenome vira token, a palavra "familia" fica;
- grafia de OCR: variante de uma palavra com similaridade >= 0,85 com a marca ou o prenome (com as mesmas
  travas da forma que ela imita). A forma em caixa alta e a mesma palavra: a busca ignora a caixa.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import fold_lexical

from .batch_propagation import _LABEL_WORDS, _brand_hits, _capitalized
from .geo_names import geo_context, is_geo_word
from .llm_hygiene import _label_vocabulary
from .propagation import FoldedText, fold_value, fold_with_index, is_protected, protected_forms, spans_for_roster
from .roster import TOKENIZE, Roster, RosterEntry, entity_core

RECOGNIZER = "VarianteElenco"
_SCORE = 0.9
COMMON_FILE = Path(__file__).with_name("data") / "palavras_comuns.txt"
_WORDS = re.compile(r"[^\W\d_]+")
_CONNECTIVES = frozenset({"de", "da", "do", "das", "dos", "e"})
_LOCAL_SOURCE = "motor_local"
# H9-g: palavra generica que abre a razao social e nao e a marca ("Comercio Alfa Ltda" -> marca "Alfa").
_GENERIC_LEAD = frozenset({"comercio", "industria", "transportes"})
_MIN_FULL_MENTIONS_LOCAL = 2
_MIN_FULL_MENTIONS_COMMON = 3
_OCR_RATIO = 0.85
_CUE_WINDOW = 40
# Ente publico e orgao: nunca propaga, nem com 3+ mencoes.
_NEVER = frozenset(
    "uniao fazenda estado municipio republica federal justica tribunal juizo vara comarca foro cartorio "
    "ministerio secretaria prefeitura delegacia procuradoria defensoria".split()
)
# Pista de pessoa antes do prenome isolado.
_PERSON_CUE = re.compile(
    r"(?i)\b(?:sr|sra|srta|dr|dra)\b\.?|\bs[oó]ci[oa]\b|\bc[oô]njuge\b|\bcompanheir[oa]|\bfilh[oa]|\besposa\b|"
    r"\bmarido\b|\b[oa]\s+executad[oa]\b|\brequerid[oa]"
)
_FAMILY = re.compile(
    r"\b(?i:fam[ií]lias?)\s+(?P<names>[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:\s*(?:,|\be\b)\s*[A-ZÀ-ÖØ-Þ][^\W\d_]*)*)"
)
# H9-g: pista de pessoa para o sobrenome sozinho (alem das do prenome: "familia" e CPF).
_SURNAME_CUE = re.compile(r"(?i)\bfam[ií]lia|\bcpf\b")
_PREVIOUS_WORD = re.compile(r"([^\W\d_]+)[ \t]+\Z")
# H9-g: palavra do dicionario em uso comum ("Conselho Nacional de Justica", "Valor de Real"): nome proprio
# composto de orgao ou instituicao, nao a marca nem o sobrenome.
_CAPITALIZED_BEFORE_PREPOSITION = re.compile(r"[A-ZÀ-ÖØ-Þ][^\W\d_]+[ \t]+(?:de|da|do|dos|das)[ \t]+\Z")
_NOT_A_NAME_BEFORE = frozenset(
    "o a os as um uma uns umas ao aos na no nas nos em de da do das dos e ou pela pelo pelas pelos por para "
    "com sem sob sobre ate apos entre contra perante segundo conforme sr sra srta dr dra".split()
)
_NAME_WORD = re.compile(r"[A-ZÀ-ÖØ-Þ][^\W\d_]*")
_NEXT_WORD = re.compile(r"\s+([^\W\d_]+)")
# H9-h: razao social escrita por extenso ("SANTOS AGROFLORESTAL LTDA"): depois da palavra vem, em ate 3 palavras de
# nome proprio (com de/da/do/e no meio), o sufixo societario. Ali o sobrenome e da empresa, nao da pessoa.
_NEXT_TOKEN = re.compile(r"[ \t]+([^\s,;:()\[\]|]+)")
_SOCIETARY_SUFFIXES = frozenset({"ltda", "s/a", "s.a", "sa", "me", "epp", "eireli", "cia"})


# H9-g: extensao do token por adjacencia. Cargo publico: nunca vira parte do nome ("[PESSOA_2] Juiza Federal").
_ROLE_WORDS = frozenset(
    "juiz juiza desembargador desembargadora procurador procuradora promotor promotora defensor defensora "
    "ministro ministra delegado delegada escrivao escriva oficial analista tecnico tecnica tabeliao tabelia "
    "registrador registradora servidor servidora secretario secretaria diretor diretora sr sra srta dr dra".split()
)
# Entre duas palavras do nome: espaco, ou hifen de quebra de linha do OCR ("De-\nDomenico").
_GAP = re.compile(r"[ \t]+|-[ \t]*\n[ \t]*")
_WORD = re.compile(r"[^\W\d_]+")
_BREAK = re.compile(r"-[ \t]*\n[ \t]*")
_WORD_BEFORE_GAP = re.compile(
    r"(?P<joined>[^\W\d_]+-[ \t]*\n[ \t]*[^\W\d_]+|[^\W\d_]+)(?P<gap>[ \t]+|-[ \t]*\n[ \t]*)\Z"
)
_SIMPLE_WORD_BEFORE_GAP = re.compile(r"(?P<joined>[^\W\d_]+)(?P<gap>[ \t]+|-[ \t]*\n[ \t]*)\Z")


@lru_cache(maxsize=1)
def common_words() -> frozenset[str]:
    """Palavras do dicionario ``data/palavras_comuns.txt``, dobradas (sem acento, minusculas)."""

    words: set[str] = set()
    for raw in COMMON_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            words.add(fold_lexical(line))
    return frozenset(words)


def _content_words(entry: RosterEntry) -> list[str]:
    words = _WORDS.findall(entity_core(entry.kind, entry.name))
    return [word for word in words if word not in _CONNECTIVES and len(word) >= 3]


def _single_word(kind: str, value: str) -> str:
    """A unica palavra de conteudo da variante (sem sufixo societario nem titulo), ou "" se ha mais de uma."""

    words = [word for word in _WORDS.findall(entity_core(kind, value)) if word not in _CONNECTIVES]
    return words[0] if len(words) == 1 else ""


class VariantPropagator:
    """Uma por rodada: ``prepare`` conta as mencoes das entradas completas em todos os arquivos do lote,
    ``spans`` acha as variantes de um arquivo. ``stats`` acumula as contagens da AUDITORIA."""

    def __init__(self, roster: Roster, exclusions: Iterable[str] = ()) -> None:
        self._roster = roster
        self._protected = protected_forms(tuple(exclusions))
        self.stats: Counter[str] = Counter()
        self.mentions: Counter[str] = Counter()
        # palavra dobrada -> {chave da entrada: (entrada, categoria)}
        self._words: dict[str, dict[str, tuple[RosterEntry, str]]] = defaultdict(dict)
        # sobrenome dobrado -> entradas de pessoa que o trazem
        self._surnames: dict[str, list[RosterEntry]] = defaultdict(list)
        self._people_words: set[str] = set()
        self._prepared = False
        # chave da entrada de marca curta -> chave da razao social a que foi ligada (H9-g)
        self.links: dict[str, str] = {}
        self._owners: tuple[int, dict[str, set[str]]] | None = None

    # --- preparo ----------------------------------------------------------------------------------
    def prepare(self, files: Iterable[tuple[str, FoldedText | None, Sequence[tuple[int, int, str]]]]) -> None:
        """``files``: (texto, texto dobrado ou None, ancoras) de cada arquivo do lote."""

        full = [
            entry
            for entry in self._roster.entries
            if entry.action == TOKENIZE
            and entry.kind in {"pessoa", "empresa"}
            and entry.source != "lista"
            and len(_content_words(entry)) >= 2
        ]
        for text, folded, anchored in files:
            if not full:
                break
            for span in spans_for_roster(
                text, full, anchored=anchored, folded=folded or fold_with_index(text)
            ):
                self.mentions[span.canonical_key] += 1
        registered: list[RosterEntry] = []
        for entry in full:
            if entry.source == _LOCAL_SOURCE and self.mentions[entry.key] < _MIN_FULL_MENTIONS_LOCAL:
                continue
            self._register(entry)
            registered.append(entry)
        self._link_short_brands(registered)
        self._prepared = True

    def _link_short_brands(self, registered: Sequence[RosterEntry]) -> None:
        """H9-g: a entrada ``empresa`` de uma palavra ("Alfa") e ligada a UMA razao social completa cuja primeira
        palavra de conteudo (fora de Comercio, Industria, Transportes e artigos) e a mesma. Vira variante dela:
        propaga pelas mesmas salvaguardas e leva o token da razao social (``links``). Duas razoes sociais
        candidatas: nao liga; a palavra fica ambigua e cada ocorrencia e contada como ambiguidade."""

        leads: dict[str, list[RosterEntry]] = defaultdict(list)
        for entry in registered:
            if entry.kind != "empresa":
                continue
            words = [word for word in _content_words(entry) if word not in _GENERIC_LEAD]
            if words:
                leads[words[0]].append(entry)
        taken = {entry.key for entry in registered}
        for short in self._roster.entries:
            if short.kind != "empresa" or short.action != TOKENIZE or short.key in taken:
                continue
            word = _single_word(short.kind, short.name)
            candidates = leads.get(word) if len(word) >= 4 else None
            if not candidates:
                continue
            for target in candidates:
                self._words[word][target.key] = (target, "marca")
            if len(candidates) == 1:
                self.links[short.key] = candidates[0].key

    def relink(self, spans: Sequence[EntitySpan]) -> list[EntitySpan]:
        """Spans da marca curta ligada levam a chave (e o token) da razao social."""

        if not self.links:
            return list(spans)
        return [
            replace(span, canonical_key=self.links[span.canonical_key])
            if span.canonical_key in self.links
            else span
            for span in spans
        ]

    def _register(self, entry: RosterEntry) -> None:
        content = _content_words(entry)
        if entry.kind == "pessoa":
            self._people_words.update(content)
            self._words[content[0]][entry.key] = (entry, "prenome")
            for word in content[1:]:
                if len(word) >= 4:
                    self._surnames[word].append(entry)
            targets = (("prenome", content[0]),)
        else:
            targets = (("marca", content[0]),)
        for value in entry.variants:
            word = _single_word(entry.kind, value)
            if not word or len(word) < 4:
                continue
            for category, base in targets:
                if word == base:
                    if category == "marca":
                        self._words[word][entry.key] = (entry, "marca")
                elif (
                    len(word) >= 5
                    and len(base) >= 5
                    and SequenceMatcher(None, word, base).ratio() >= _OCR_RATIO
                ):
                    self._words[word][entry.key] = (entry, "ocr")

    # --- busca ------------------------------------------------------------------------------------
    def spans(self, text: str, taken: Sequence[EntitySpan], folded: FoldedText | None = None) -> list[EntitySpan]:
        """Variantes do elenco neste texto, so fora do que ``taken`` ja cobre."""

        if not self._prepared or not (self._words or self._surnames):
            return []
        source = folded or fold_with_index(text)
        occupied = [(span.start, span.end) for span in taken]
        found: list[EntitySpan] = []
        for word in sorted(self._words):
            owners = self._words[word]
            for start, end in _brand_hits(text, source, word, occupied, self._protected, None):
                if not _capitalized(text[start:end]):
                    continue
                span = self._decide(text, word, owners, start, end)
                if span is not None:
                    found.append(span)
                    occupied.append((start, end))
        found.extend(self._family_spans(text, occupied))
        found.extend(self._surname_spans(text, source, occupied))
        return found

    def _decide(
        self, text: str, word: str, owners: dict[str, tuple[RosterEntry, str]], start: int, end: int
    ) -> EntitySpan | None:
        if len(owners) > 1:
            self.stats["ambiguo"] += 1
            return None
        entry, category = next(iter(owners.values()))
        reason = self._refusal(word, entry) or self._common_use(text, word, start)
        if reason:
            self.stats[reason] += 1
            return None
        if category in {"prenome", "ocr"} and entry.kind == "pessoa":
            if not _PERSON_CUE.search(_cue_window(text, start)):
                return None
        if self._followed_by_name(text, end):
            self.stats["contexto"] += 1
            return None
        if entry.kind == "empresa" and word in self._people_words:
            self.stats["ambiguo"] += 1
            return None
        self.stats[category] += 1
        return self._span(entry, start, end)

    def _family_spans(self, text: str, occupied: list[tuple[int, int]]) -> list[EntitySpan]:
        found: list[EntitySpan] = []
        for match in _FAMILY.finditer(text):
            base = match.start("names")
            for name in _NAME_WORD.finditer(match.group("names")):
                start, end = base + name.start(), base + name.end()
                if any(start < other_end and other_start < end for other_start, other_end in occupied):
                    continue
                word = fold_lexical(name.group(0))
                owners = self._surnames.get(word)
                if not owners:
                    continue
                if self._shared_surname(word, owners) or company_name_follows(text, end):
                    # H9-h: homonimo de sobrenome (outra entrada do elenco, pessoa ou empresa, mantida ou nao) e
                    # razao social nao recebem o token da familia.
                    self.stats["ambiguo"] += 1
                    occupied.append((start, end))
                    continue
                # Sobrenome comum a mais de uma entrada da familia: vai para a de mais mencoes (empate: chave).
                entry = max(owners, key=lambda item: (self.mentions[item.key], item.key))
                reason = self._refusal(word, entry, surname=True) or self._common_use(text, word, start)
                if reason:
                    self.stats[reason] += 1
                    occupied.append((start, end))  # a busca por pista de pessoa nao conta a mesma palavra de novo
                    continue
                self.stats["sobrenome"] += 1
                found.append(self._span(entry, start, end))
                occupied.append((start, end))
        return found

    def _surname_spans(
        self, text: str, source: FoldedText, occupied: list[tuple[int, int]]
    ) -> list[EntitySpan]:
        """Sobrenome sozinho com pista de pessoa a ate 40 caracteres ("o socio Fagundes", "Sr. Fagundes",
        "CPF de Fagundes"). A palavra logo antes nao pode ser outro nome proprio, nem a seguinte (e outra
        pessoa). Nome de municipio so propaga aqui se a entrada tem 3+ mencoes (``_refusal``)."""

        found: list[EntitySpan] = []
        for word in sorted(self._surnames):
            owners = {entry.key: entry for entry in self._surnames[word]}
            for start, end in _brand_hits(text, source, word, occupied, self._protected, None):
                if not _capitalized(text[start:end]):
                    continue
                window = _cue_window(text, start)
                if not (_PERSON_CUE.search(window) or _SURNAME_CUE.search(window)):
                    continue
                if len(owners) > 1 or self._shared_surname(word, list(owners.values())):
                    self.stats["ambiguo"] += 1
                    continue
                if company_name_follows(text, end):
                    self.stats["contexto"] += 1
                    continue
                entry = next(iter(owners.values()))
                reason = self._refusal(word, entry, surname=True) or self._common_use(text, word, start)
                if reason:
                    self.stats[reason] += 1
                    continue
                if _after_other_name(text, start) or self._followed_by_name(text, end):
                    self.stats["contexto"] += 1
                    continue
                self.stats["sobrenome"] += 1
                found.append(self._span(entry, start, end))
                occupied.append((start, end))
        return found

    def _shared_surname(self, word: str, owners: Sequence[RosterEntry]) -> bool:
        """H9-h: alguma OUTRA entrada do elenco (pessoa ou empresa, inclusive "manter") traz esta palavra no nome?
        Entao o sobrenome sozinho nao prova de quem e, e nao propaga (homonimo)."""

        mine = {entry.key for entry in owners}
        return bool(self._word_owners().get(word, set()) - mine)

    def _refusal(self, word: str, entry: RosterEntry, surname: bool = False) -> str:
        """Motivo para a palavra nao propagar, ou "" se pode. ``surname``: ha pista de pessoa colada no
        sobrenome; ai o nome de municipio propaga se a entrada tem 3+ mencoes (H9-g: "Fagundes", "Santos")."""

        if word in _NEVER or is_protected(word, self._protected):
            return "protegido"
        if is_geo_word(word) and not (surname and self.mentions[entry.key] >= _MIN_FULL_MENTIONS_COMMON):
            return "geografia"
        if word in _label_words():
            return "dicionario"
        if word in common_words() and self.mentions[entry.key] < _MIN_FULL_MENTIONS_COMMON:
            return "dicionario"
        return ""

    @staticmethod
    def _common_use(text: str, word: str, start: int) -> str:
        """Palavra do dicionario em uso comum nao propaga, mesmo com 3+ mencoes da entrada completa, quando a
        palavra anterior e capitalizada ("Conselho Nacional") ou vem "de/da/do/dos/das" + nome capitalizado
        logo antes ("Receita Federal do Brasil", "Valor de Real"). Devolve o motivo ("dicionario") ou ""."""

        if word not in common_words():
            return ""
        before = text[max(0, start - 60) : start]
        if _CAPITALIZED_BEFORE_PREPOSITION.search(before):
            return "dicionario"
        previous = _PREVIOUS_WORD.search(before)
        if previous is not None:
            name = previous.group(1)
            if len(name) >= 2 and name[:1].isupper() and fold_lexical(name) not in _NOT_A_NAME_BEFORE:
                return "dicionario"
        return ""

    @staticmethod
    def _followed_by_name(text: str, end: int) -> bool:
        """A palavra abre outro nome proprio ("Alfa Textil", "Joel Souza"): nao e a variante isolada."""

        match = _NEXT_WORD.match(text, end)
        if match is None:
            return False
        following = match.group(1)
        return following[:1].isupper() and following.casefold() not in _CONNECTIVES and not _is_suffix(following)

    @staticmethod
    def _span(entry: RosterEntry, start: int, end: int) -> EntitySpan:
        return EntitySpan(
            start=start,
            end=end,
            entity_type=entry.entity_type,
            score=_SCORE,
            recognizer=RECOGNIZER,
            canonical_key=entry.key,
            alvo=entry.name,
        )

    # --- extensao por adjacencia (H9-g) ------------------------------------------------------------
    def extend_adjacent(self, text: str, entities: Sequence[EntitySpan]) -> list[EntitySpan]:
        """Estende o span de pessoa sobre a palavra de nome colada nele (``[PESSOA_2] Domenico``,
        ``Maria [PESSOA_4]``), com o mesmo token.

        A adjacencia ao proprio token e a evidencia: nao consulta dicionario, geografia nem termo
        protegido de varias palavras. A palavra so entra se for parte do nome completo da MESMA entrada do
        elenco (ou grafia com similaridade >= 0,85 de uma parte), separada so por espaco ou por hifen de
        quebra de linha, com conectivos de/da/do/dos/das/e no meio. Nunca entra palavra de OUTRA entrada,
        palavra protegida inteira (Uniao, Fazenda) nem cargo (Juiz, Procurador)."""

        owners = self._word_owners()
        result = list(entities)
        for index, span in enumerate(result):
            if span.entity_type != "PERSON" or not span.canonical_key:
                continue
            entry = self._roster.get(span.canonical_key)
            if entry is None or entry.kind != "pessoa" or entry.action != TOKENIZE:
                continue
            parts = self._parts(entry)
            if not parts:
                continue
            others = [(other.start, other.end) for j, other in enumerate(result) if j != index]
            start = self._reach_left(text, span.start, parts, owners, others, entry.key)
            end = self._reach_right(text, span.end, parts, owners, others, entry.key)
            if (start, end) != (span.start, span.end):
                result[index] = replace(span, start=start, end=end)
                self.stats["adjacencia"] += 1
        return result

    @staticmethod
    def _parts(entry: RosterEntry) -> frozenset[str]:
        words: set[str] = set()
        for value in (entry.name, *entry.variants):
            words.update(
                word
                for word in _WORDS.findall(entity_core(entry.kind, value))
                if word not in _CONNECTIVES and len(word) >= 2
            )
        return frozenset(words)

    def _word_owners(self) -> dict[str, set[str]]:
        """Palavra dobrada -> chaves das entradas (pessoa ou empresa) cujo nome a traz."""

        if self._owners is not None and self._owners[0] == len(self._roster):
            return self._owners[1]
        owners: dict[str, set[str]] = defaultdict(set)
        for entry in self._roster.entries:
            if entry.kind not in {"pessoa", "empresa"}:
                continue
            for word in self._parts(entry):
                owners[word].add(entry.key)
        self._owners = (len(self._roster), owners)
        return owners

    def _qualifies(self, word: str, parts: frozenset[str], owners: dict[str, set[str]]) -> bool:
        if not word[:1].isupper():
            return False
        folded = fold_lexical(word)
        if len(folded) < 2 or folded in _ROLE_WORDS or folded in _NEVER or folded in _CONNECTIVES:
            return False
        if is_protected(word, self._protected):
            return False
        if folded in parts:
            return True
        if folded in owners:
            return False  # palavra de OUTRA entrada
        return len(folded) >= 5 and any(
            len(part) >= 5 and SequenceMatcher(None, folded, part).ratio() >= _OCR_RATIO for part in parts
        )

    def _kind_of(self, word: str, parts: frozenset[str], owners: dict[str, set[str]]) -> str:
        if fold_lexical(word) in _CONNECTIVES:
            return "conectivo"
        return "nome" if self._qualifies(word, parts, owners) else ""

    def _blocked(self, text: str, start: int, end: int, parts: frozenset[str], owners: dict[str, set[str]], key: str) -> bool:
        """H9-h: a palavra de nome que a extensao ia levar, mas que nao pode: pertence ao nome de OUTRA entrada
        (pessoa ou empresa), e municipio fora do nome da propria entrada ou esta em contexto de endereco/foro
        (bairro, rodape), ou e palavra em caixa alta de razao social ("AGROFLORESTAL LTDA")."""

        word = _BREAK.sub("", text[start:end])
        folded = fold_lexical(word)
        if owners.get(folded, set()) - {key}:
            return True
        if folded not in parts:
            if is_geo_word(folded) or geo_context(text, start, end):
                return True
        elif is_geo_word(folded) and geo_context(text, start, end):
            return True
        return word.isupper() and len(word) >= 2 and company_name_follows(text, end, max_words=0)

    def _reach_right(self, text, end, parts, owners, others, key="") -> int:
        reach = end
        cursor = end
        for _ in range(8):
            gap = _GAP.match(text, cursor)
            if gap is None:
                break
            kind, word_end = "", cursor
            for word, candidate_end in _word_after(text, gap.end()):
                kind = self._kind_of(word, parts, owners)
                if kind:
                    word_end = candidate_end
                    break
            if not kind:
                break
            if kind == "nome" and self._blocked(text, gap.end(), word_end, parts, owners, key):
                break
            cursor = word_end
            if kind == "nome":
                if _overlaps(reach, cursor, others):
                    break
                reach = cursor
        return reach

    def _reach_left(self, text, start, parts, owners, others, key="") -> int:
        reach = start
        cursor = start
        for _ in range(8):
            window = text[max(0, cursor - 80) : cursor]
            kind, word_start = "", cursor
            for pattern in (_WORD_BEFORE_GAP, _SIMPLE_WORD_BEFORE_GAP):
                match = pattern.search(window)
                if match is None:
                    continue
                kind = self._kind_of(match.group("joined"), parts, owners)
                if kind:
                    word_start = cursor - len(window) + match.start("joined")
                    break
            if not kind:
                break
            if kind == "nome" and self._blocked(text, word_start, word_start + len(match.group("joined")), parts, owners, key):
                break
            cursor = word_start
            if kind == "nome":
                if _overlaps(cursor, reach, others):
                    break
                reach = cursor
        return reach

    # --- auditoria --------------------------------------------------------------------------------
    @property
    def propagated(self) -> int:
        return sum(self.stats[name] for name in ("marca", "prenome", "sobrenome", "ocr"))

    def audit_lines(self) -> list[str]:
        return [self.audit_line(), f"Tokens estendidos por adjacencia: {self.stats['adjacencia']}"]

    def audit_line(self) -> str:
        return (
            f"Variantes propagadas pelo elenco: {self.propagated} ocorrencias "
            f"(marca curta {self.stats['marca']}, prenome {self.stats['prenome']}, "
            f"sobrenome {self.stats['sobrenome']}, OCR {self.stats['ocr']}); "
            f"recusadas por dicionario {self.stats['dicionario']}, geografia {self.stats['geografia']}, "
            f"protegido {self.stats['protegido']}, ambiguidade {self.stats['ambiguo']}, "
            f"contexto {self.stats['contexto']}"
        )


def _word_after(text: str, position: int) -> list[tuple[str, int]]:
    """Palavra que comeca em ``position``: a forma com hifen de quebra de linha juntada (Dome-, quebra, nico)
    primeiro e depois a primeira parte sozinha (o "De" de De-, quebra, Domenico)."""

    first = _WORD.match(text, position)
    if first is None:
        return []
    joined, joined_end = first.group(0), first.end()
    while True:
        gap = _BREAK.match(text, joined_end)
        nxt = _WORD.match(text, gap.end()) if gap else None
        if nxt is None:
            break
        joined += nxt.group(0)
        joined_end = nxt.end()
    forms = []
    if joined_end != first.end():
        forms.append((joined, joined_end))
    forms.append((first.group(0), first.end()))
    return forms


def company_name_follows(text: str, end: int, max_words: int = 3) -> bool:
    """H9-h: a palavra que termina em ``end`` esta dentro de uma razao social (nomes proprios e o sufixo societario)."""

    cursor = end
    for _ in range(max_words + 1):
        match = _NEXT_TOKEN.match(text, cursor)
        if match is None:
            return False
        token = match.group(1)
        bare = token.rstrip(".").casefold()
        if bare in _SOCIETARY_SUFFIXES:
            return True
        if fold_lexical(token) in _CONNECTIVES or token[:1].isupper():
            cursor = match.end()
            continue
        return False
    return False


def _after_other_name(text: str, start: int) -> bool:
    """A palavra logo antes e outro nome proprio ("socio Pedro Fagundes"): o sobrenome e de outra pessoa."""

    match = _PREVIOUS_WORD.search(text[max(0, start - 40) : start])
    if match is None:
        return False
    previous = match.group(1)
    if not previous[:1].isupper() or len(previous) < 2:
        return False
    return not (
        fold_lexical(previous) in _CONNECTIVES
        or _PERSON_CUE.search(previous)
        or _SURNAME_CUE.search(previous)
    )


def _overlaps(start: int, end: int, others: Sequence[tuple[int, int]]) -> bool:
    return any(start < other_end and other_start < end for other_start, other_end in others)


# Fim de frase (nao o ponto de "Sr." nem de "Dra.") ou quebra de linha: a pista nao atravessa.
_SENTENCE_BREAK = re.compile(r"(?i)(?<!\bsr)(?<!\bsra)(?<!\bsrta)(?<!\bdr)(?<!\bdra)[.;!?]\s+|\n")


def _cue_window(text: str, start: int) -> str:
    """Os ate 40 caracteres antes do nome, so da mesma frase."""

    return _SENTENCE_BREAK.split(text[max(0, start - _CUE_WINDOW) : start])[-1]


def _is_suffix(word: str) -> bool:
    return word.casefold().rstrip(".") in {"ltda", "me", "epp", "eireli", "sa", "s", "cia"}


@lru_cache(maxsize=1)
def _label_words() -> frozenset[str]:
    return frozenset(_LABEL_WORDS) | frozenset(_label_vocabulary())

"""Propagacao do lote (deteccao 2.1): o que ja foi detectado vale para todas as ocorrencias.

Medido no lote eproc de 25/09/2026: o mesmo nome era trocado numa folha e ficava
em claro noutra (quebra de linha, lista, tabela: o GLiNER decide no limiar 0,75
olhando so o contexto local), e cada grafia virava um token proprio (um socio sob
8 tokens; a razao social curta e a longa sob tokens diferentes).

1. Elenco do lote: as grafias de pessoa e empresa detectadas em todos os arquivos
   se juntam por entidade: mesma forma dobrada; empresa sem sufixo societario;
   prefixo com 2+ palavras que so aponta para uma entidade; pessoa com o mesmo
   primeiro e ultimo nome (ultimo com ate 1 letra de diferenca, por OCR).
2. Cada grafia com 2+ palavras de conteudo e buscada no texto inteiro (maiuscula,
   acento, quebra de linha) e passa pelas mesmas travas da deteccao (juiz,
   procurador, precedente, termo protegido, higiene). So preenche lacuna: o que
   ja foi detectado nao muda. Entidade vista em contexto de juiz ou procurador em
   qualquer arquivo do lote nao se propaga (item H). Rotulo tomado por nome
   ("Tipo de Devedor") tambem nao.
3. Todo span da entidade leva a mesma chave: um token por entidade no lote.

IDs ja mapeados (placa, chassi, RENAVAM, e-mail) e a raiz de CNPJ privado ja
mapeado se propagam pelo valor exato, com o token do proprio ID. Nao e regex sem
rotulo (P10): e o valor que a deteccao ja achou com rotulo.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import canonicalize, fold_lexical

from .fidelity import PUBLIC_CNPJ_ROOTS, drop_protected_spans
from .propagation import FoldedText, fold_with_index, is_protected, protected_forms, spans_for_roster
from .roster import TOKENIZE, RosterEntry, entity_core, has_token
from .span_hygiene import sanitize_spans

RECOGNIZER = "PropagacaoLote"
_SCORE = 0.9
_NAME_KINDS = {"PERSON": "pessoa", "ORGANIZATION": "empresa"}
_ID_TYPES = frozenset({"BR_PLACA", "BR_CHASSI", "BR_RENAVAM", "EMAIL_ADDRESS"})
_CONTENT = re.compile(r"[^\W\d_]{3,}")
_CONNECTIVES = frozenset({"de", "da", "do", "das", "dos", "e"})
# Palavras de rotulo de tabela/formulario: "pessoa" feita so delas e rotulo, nao nome.
_LABEL_WORDS = frozenset(
    {
        "tipo", "devedor", "credor", "nome", "empregado", "colaborador", "razao", "social",
        "data", "numero", "processo", "valor", "codigo", "situacao", "conta", "total",
        "fonte", "admissao", "descricao", "empresa", "endereco", "requerente", "requerido",
        "executado", "exequente", "autor", "reu", "parte", "polo", "principal", "cpf",
        "cnpj", "cargo", "funcao", "mes", "ano", "inscricao", "natureza", "documento",
        # Estrutura juridica: "pessoa juridica de direito privado" nao e empresa de ninguem.
        "pessoa", "juridica", "fisica", "natural", "direito", "privado", "publico",
        "sociedade", "empresaria", "empresario", "limitada", "ltda", "individual",
        "microempresa", "pequeno", "porte", "responsabilidade", "eireli", "unipessoal",
    }
)
# Agente publico, julgado uma vez por entidade: o cargo colado ao nome (antes ou depois).
# Mais estrito que a regra por ocorrencia da deteccao ("juiz" em ate 160 caracteres),
# que pegaria o socio na abertura da peticao ("Senhor Juiz ... em face de FULANO").
_ROLE_BEFORE = re.compile(
    r"(?is)(?:ju[ií]z(?:a)?(?:\s+federal)?(?:\s+substitut[oa])?"
    r"(?:\s+(?:inclus[aã]o|solicitante|respons[aá]vel))?"
    r"|magistrad[oa]|desembargador(?:a)?(?:\s+federal)?|relator(?:a)?|minist(?:ro|ra)"
    r"|documento\s+eletr[oô]nico\s+assinado\s+por"
    r"|assinad[oa]\s+(?:digitalmente\s+|eletronicamente\s+)?por"
    r"|diretor(?:a)?\s+de\s+secretaria|servidor(?:a)?|oficial(?:a)?\s+de\s+justi[cç]a"
    r"|usu[aá]rio|protocolad[oa]\s+por"
    r"|procurador(?:a)?\s+da\s+fazenda\s+nacional|procurador(?:a)?\s+federal)"
    r"\s*(?:\([^)]{0,12}\))?\s*[:|\-–—(]?\s*$"
)
_ROLE_AFTER = re.compile(
    r"(?i)^\s*[,–—\-(]?\s*(?:ju[ií]z(?:a)?\s+federal|ju[ií]z(?:a)?\s+substitut[oa]|"
    r"desembargador(?:a)?|diretor(?:a)?\s+de\s+secretaria|"
    r"procurador(?:a)?\s+da\s+fazenda\s+nacional|procurador(?:a)?\s+federal|"
    r"oficial(?:a)?\s+de\s+justi[cç]a|servidor(?:a)?|secret[aá]ri[oa][-\s]+geral|"
    r"registrador(?:a)?|tabeli[aã]o?|escrevente)\b"
)


# Tipo errado (lote de 25/09): a rua com nome de pessoa virava [PESSOA_n] em 38 enderecos;
# banco do SISBAJUD virava [EMPRESA_n] (e a corretora ao lado, na mesma lista, ficava em claro).
_STREET_SHARE = 0.6
_STREET_BEFORE = re.compile(
    r"(?i)(?:endere[çc]o|logradouro)\s*:?\s*$|"
    r"(?:\brua|\br\.|\bav\.|\bavenida|\balameda|\btravessa|\bpra[çc]a|\brodovia|\bestrada)\s*$"
)
_HOUSE_NUMBER_AFTER = re.compile(r"(?i)\s*,?\s*(?:nr?\.|n[º°o]\.?|n[uú]mero)\s*\d")
_COMPE_BEFORE = re.compile(r"^\s*\d{3,5}\s*-\s*$")
_BANK_HEADER_AFTER = re.compile(r"[^\n]{0,25}\n\s*Data/hora")


@dataclass(slots=True)
class _Entity:
    entity_type: str
    kind: str
    cores: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    public_agent: bool = False
    # Juizo de tipo, por entidade (fila 3.x, tokens do tipo errado).
    occurrences: int = 0
    street_hits: int = 0
    street_label: bool = False
    financial: bool = False

    @property
    def is_street(self) -> bool:
        """Rua com nome de pessoa: quase toda ocorrencia em posicao de logradouro."""

        return (
            self.entity_type == "PERSON"
            and self.street_label
            and self.street_hits >= _STREET_SHARE * max(1, self.occurrences)
        )

    @property
    def representative(self) -> str:
        """Mais palavras; empate: a grafia mais frequente (o erro de OCR e raro), depois a mais longa."""

        counts: dict[str, int] = defaultdict(int)
        for name in self.names:
            counts[fold_lexical(name)] += 1
        return max(self.names, key=lambda name: (len(name.split()), counts[fold_lexical(name)], len(name)))

    @property
    def key(self) -> str:
        return fold_lexical(self.representative)


class BatchRoster:
    """Elenco do lote: coleta na fase 1 (deteccao), aplica na fase 2 (troca)."""

    def __init__(self, extra_keep: Iterable[str] = ()) -> None:
        self._extra_keep = tuple(str(item) for item in extra_keep)
        self._protected = protected_forms(self._extra_keep)
        self._names: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._ids: dict[tuple[str, str], str] = {}
        self._cnpjs: dict[str, str] = {}
        self._id_records: list[SimpleNamespace] = []
        self._entities: list[_Entity] | None = None
        self._by_core: dict[tuple[str, str], _Entity] = {}
        # Empresa vista em posicao de instituicao financeira (SISBAJUD) na fase 1.
        self._financial: set[str] = set()

    # --- fase 1 -------------------------------------------------------------
    def add(self, text: str, spans: Iterable[EntitySpan]) -> None:
        for span in spans:
            value = span.value_from(text)
            kind = _NAME_KINDS.get(span.entity_type)
            if kind is not None:
                if has_token(value) or is_protected(value, self._protected):
                    continue
                core = entity_core(kind, value)
                if _CONTENT.search(core):
                    self._names[(span.entity_type, core)].append(value)
                    if span.entity_type == "ORGANIZATION" and _financial_position(text, span.start, span.end):
                        self._financial.add(core)
                continue
            canonical = span.canonical_key or canonicalize(span.entity_type, value)
            if not canonical:
                continue
            if span.entity_type in _ID_TYPES:
                self._ids.setdefault((span.entity_type, canonical), value)
            elif span.entity_type == "BR_CNPJ":
                root = canonical[:8]
                if len(canonical) == 14 and root.isdigit() and root not in PUBLIC_CNPJ_ROOTS:
                    # Matriz (0001) vence filial na hora de dar o token a raiz.
                    if root not in self._cnpjs or canonical[8:12] == "0001":
                        self._cnpjs[root] = canonical
            if span.entity_type in {"BR_CPF", "BR_CNPJ"}:
                self._id_records.append(
                    SimpleNamespace(entity_type=span.entity_type, canonical=canonical)
                )
        self._entities = None

    @property
    def records(self) -> tuple[SimpleNamespace, ...]:
        """CPF/CNPJ ja vistos no lote, no formato que ``propagate_mapped_ids`` le do mapa."""

        return tuple(self._id_records)

    def review(self, texts: Iterable[str]) -> None:
        """Antes da troca: entidade vista em contexto de juiz ou procurador nao se propaga."""

        entries = self._entries(include_public=True)
        if not entries:
            return
        by_key = {entity.key: entity for entity in self._ensure()}
        for text in texts:
            for span in spans_for_roster(text, entries, extra_keep=self._extra_keep):
                entity = by_key.get(span.canonical_key)
                if entity is None:
                    continue
                entity.occurrences += 1
                if entity.entity_type == "ORGANIZATION":
                    entity.financial = entity.financial or _financial_position(text, span.start, span.end)
                    continue
                if entity.entity_type != "PERSON":
                    continue
                labelled, street = _street_position(text, span.start, span.end)
                entity.street_label = entity.street_label or labelled
                entity.street_hits += street
                if not entity.public_agent and _public_role(text, span.start, span.end):
                    entity.public_agent = True

    # --- elenco -------------------------------------------------------------
    def entities(self) -> list[_Entity]:
        return list(self._ensure())

    def key_for(self, entity_type: str, value: str) -> str:
        kind = _NAME_KINDS.get(entity_type)
        if kind is None:
            return ""
        entity = self._by_core.get((entity_type, entity_core(kind, value))) if self._ensure() else None
        return entity.key if entity is not None else ""

    def _ensure(self) -> list[_Entity]:
        if self._entities is None:
            self._entities = self._build()
            self._by_core = {
                (entity.entity_type, core): entity
                for entity in self._entities
                for core in entity.cores
            }
        return self._entities

    def _build(self) -> list[_Entity]:
        entities: list[_Entity] = []
        for entity_type, kind in _NAME_KINDS.items():
            cores = {core: names for (etype, core), names in self._names.items() if etype == entity_type}
            groups: list[_Entity] = []
            # Cabeca de grupo: mais palavras, depois a grafia mais vista (o nome certo antes do OCR).
            order = sorted(cores, key=lambda item: (-len(item.split()), -len(cores[item]), -len(item), item))
            for core in order:
                matches = [group for group in groups if any(_same(entity_type, core, head) for head in group.cores)]
                target = _pick(entity_type, matches)
                if target is None:
                    target = _Entity(entity_type, kind)
                    groups.append(target)
                target.cores.append(core)
                target.names.extend(cores[core])
                target.financial = target.financial or core in self._financial
            entities.extend(groups)
        return entities

    def _entries(self, *, include_public: bool = False) -> list[RosterEntry]:
        entries: list[RosterEntry] = []
        for entity in self._ensure():
            if entity.public_agent and not include_public:
                continue
            if _is_label(entity.representative):
                continue
            entries.append(
                RosterEntry(
                    key=entity.key,
                    kind=entity.kind,
                    name=entity.representative,
                    variants=sorted(set(entity.names)),
                    action=TOKENIZE,
                    source="lote",
                )
            )
        return entries

    # --- fase 2 -------------------------------------------------------------
    def apply(
        self,
        text: str,
        spans: Sequence[EntitySpan],
        *,
        source_name: str = "",
        folded: FoldedText | None = None,
    ) -> list[EntitySpan]:
        """Mesma chave para toda grafia da entidade + ocorrencias que a deteccao nao pegou."""

        unified = [self._unify(text, span) for span in spans]
        found = [
            replace(span, recognizer=RECOGNIZER, score=_SCORE)
            for span in spans_for_roster(
                text, self._entries(), extra_keep=self._extra_keep, folded=folded or fold_with_index(text)
            )
        ]
        found.extend(self._id_spans(text))
        found = drop_protected_spans(text, found, self._extra_keep, source_name=source_name)
        found = sanitize_spans(text, found)
        merged = sorted([*unified, *_gaps(found, unified)], key=lambda span: (span.start, span.end))
        return self._retype(merged)

    def _retype(self, spans: list[EntitySpan]) -> list[EntitySpan]:
        """Rua com nome de pessoa vira endereco; banco do SISBAJUD fica em claro."""

        flagged = {
            entity.key: entity for entity in self._ensure() if entity.is_street or entity.financial
        }
        if not flagged:
            return spans
        result: list[EntitySpan] = []
        for span in spans:
            entity = flagged.get(span.canonical_key)
            if entity is None:
                result.append(span)
            elif entity.is_street:
                result.append(replace(span, entity_type="LOCATION"))
        return result

    def _unify(self, text: str, span: EntitySpan) -> EntitySpan:
        if span.canonical_key or span.entity_type not in _NAME_KINDS:
            return span
        key = self.key_for(span.entity_type, span.value_from(text))
        return replace(span, canonical_key=key) if key else span

    def _id_spans(self, text: str) -> list[EntitySpan]:
        spans: list[EntitySpan] = []
        for (entity_type, canonical), _value in self._ids.items():
            pattern = _id_pattern(entity_type, canonical)
            if pattern is None:
                continue
            for match in pattern.finditer(text):
                spans.append(_id_span(match.start(), match.end(), entity_type, canonical))
        for root, canonical in self._cnpjs.items():
            pattern = re.compile(
                rf"(?<![\d./-]){root[:2]}\.?{root[2:5]}\.?{root[5:]}(?!\d|[./-]\d)"
            )
            for match in pattern.finditer(text):
                spans.append(_id_span(match.start(), match.end(), "BR_CNPJ", canonical))
        return spans


def _id_span(start: int, end: int, entity_type: str, canonical: str) -> EntitySpan:
    return EntitySpan(
        start=start,
        end=end,
        entity_type=entity_type,
        score=_SCORE,
        recognizer=RECOGNIZER,
        canonical_key=canonical,
    )


def _id_pattern(entity_type: str, canonical: str) -> re.Pattern[str] | None:
    if entity_type == "BR_PLACA" and re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}", canonical):
        body = rf"{canonical[:3]}[-\s]?{canonical[3:]}"
        return re.compile(rf"(?<![A-Za-z0-9])(?:[A-Za-z]{{2}}\s*/\s*)?{body}(?![A-Za-z0-9])", re.IGNORECASE)
    if entity_type == "BR_CHASSI" and len(canonical) == 17:
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(canonical)}(?![A-Za-z0-9])", re.IGNORECASE)
    if entity_type == "BR_RENAVAM" and canonical.isdigit() and len(canonical) >= 9:
        return re.compile(rf"(?<![\d.-]){canonical}(?!\d)")
    if entity_type == "EMAIL_ADDRESS" and "@" in canonical:
        return re.compile(rf"(?<![\w.+-]){re.escape(canonical)}(?![\w-])", re.IGNORECASE)
    return None


def _gaps(found: Sequence[EntitySpan], taken: Sequence[EntitySpan]) -> list[EntitySpan]:
    """So o que nao encosta em nenhum span ja detectado; entre si, o mais longo."""

    occupied = sorted((span.start, span.end) for span in taken)
    starts = [start for start, _end in occupied]
    from bisect import bisect_left

    chosen: list[EntitySpan] = []
    for span in sorted(found, key=lambda item: (-(item.end - item.start), item.start)):
        position = bisect_left(starts, span.end)
        if position and occupied[position - 1][1] > span.start:
            continue
        if any(span.start < other.end and other.start < span.end for other in chosen):
            continue
        chosen.append(span)
    return chosen


def _street_position(text: str, start: int, end: int) -> tuple[bool, bool]:
    """(rotulo de logradouro logo antes, ocorrencia em posicao de endereco)."""

    labelled = bool(_STREET_BEFORE.search(text[max(0, start - 30) : start]))
    return labelled, labelled or bool(_HOUSE_NUMBER_AFTER.match(text, end))


def _financial_position(text: str, start: int, end: int) -> bool:
    """Empresa no lugar de instituicao do SISBAJUD: "05422 - NOME" na lista da ordem, ou
    cabecalho da resposta seguido de "Data/hora"."""

    line_start = text.rfind("\n", 0, start) + 1
    if _COMPE_BEFORE.match(text[line_start:start]):
        return True
    return bool(_BANK_HEADER_AFTER.match(text, end))


def _public_role(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 60) : start]
    after = text[end : end + 60]
    return bool(_ROLE_BEFORE.search(before) or _ROLE_AFTER.match(after))


def _is_label(value: str) -> bool:
    words = [word for word in re.findall(r"[^\W\d_]+", fold_lexical(value)) if word not in _CONNECTIVES]
    return bool(words) and all(word in _LABEL_WORDS for word in words)


def _pick(entity_type: str, matches: list[_Entity]) -> _Entity | None:
    """Uma so entidade combina: e ela. Varias: pessoa fica de fora ("Jose Carlos" pode ser
    qualquer um); empresa vai para a que domina o lote (3x mais grafias que a seguinte)."""

    if len(matches) == 1:
        return matches[0]
    if len(matches) < 2 or entity_type != "ORGANIZATION":
        return None
    ranked = sorted(matches, key=lambda group: len(group.names), reverse=True)
    return ranked[0] if len(ranked[0].names) >= 3 * len(ranked[1].names) else None


def _close(left: str, right: str) -> bool:
    """Mesma palavra salvo OCR/digitacao: 1 letra (4-7 letras) ou 2 letras (8+)."""

    if left == right:
        return True
    shortest = min(len(left), len(right))
    if shortest < 4:
        return False
    limit = 2 if shortest >= 8 else 1
    if abs(len(left) - len(right)) > limit:
        return False
    previous = list(range(len(right) + 1))
    for row, a in enumerate(left, start=1):
        current = [row]
        for column, b in enumerate(right, start=1):
            current.append(min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + (a != b)))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def _same(entity_type: str, short: str, long: str) -> bool:
    """A grafia curta e a mesma entidade da longa? (a longa ja e cabeca de grupo)"""

    if short == long or short.replace(" ", "") == long.replace(" ", ""):
        return True
    small, large = short.split(), long.split()
    content = [word for word in small if word not in _CONNECTIVES]
    if len(content) < 2:
        return False
    if large[: len(small)] == small:
        return True
    if len(small) == len(large):
        diffs = [(a, b) for a, b in zip(small, large) if a != b]
        return len(diffs) == 1 and _close(*diffs[0])
    if entity_type != "PERSON" or small[0] != large[0] or not _close(small[-1], large[-1]):
        return False
    # Primeiro e ultimo nome batem; os do meio da curta aparecem, na ordem, na longa.
    middle = iter(large[1:-1])
    return all(any(word == other for other in middle) for word in small[1:-1])

"""Propagacao do lote (deteccao 2.1): o que ja foi detectado vale para todas as ocorrencias.

Medido no lote eproc de 25/09/2026: o mesmo nome era trocado numa folha e ficava
em claro noutra (quebra de linha, lista, tabela: o GLiNER decide no limiar 0,75
olhando so o contexto local), e cada grafia virava um token proprio (um socio sob
8 tokens; a razao social curta e a longa sob tokens diferentes).

1. Elenco do lote: as grafias de pessoa e empresa detectadas em todos os arquivos
   se juntam por entidade: mesma forma dobrada (sem acento, caixa, pontuacao solta,
   hifen de quebra de linha: "ME-TAIS" = "METAIS"); empresa sem sufixo societario;
   prefixo com 2+ palavras que so aponta para uma entidade; pessoa com o mesmo
   primeiro e ultimo nome (palavra com 1 letra de diferenca no meio, por OCR; nunca
   a ultima letra: "Adriano" e "Adriana" sao pessoas diferentes; nunca "Filho",
   "Junior", "Neto"). Na duvida, separado (item E13).
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
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import canonicalize, fold_lexical

from .fidelity import PUBLIC_CNPJ_ROOTS, drop_protected_spans
from .geo_names import geo_context, is_geo_word
from .propagation import (
    FoldedText,
    _find_all,
    fold_value,
    fold_with_index,
    is_protected,
    protected_forms,
    spans_for_roster,
)
from .roster import TOKENIZE, RosterEntry, entity_core, has_token
from .span_hygiene import ROLE_BEFORE_NAME, sanitize_spans

RECOGNIZER = "PropagacaoLote"
_SCORE = 0.9
_NAME_KINDS = {"PERSON": "pessoa", "ORGANIZATION": "empresa"}
_ID_TYPES = frozenset({"BR_PLACA", "BR_CHASSI", "BR_RENAVAM", "EMAIL_ADDRESS"})
_CONTENT = re.compile(r"[^\W\d_]{3,}")
_CONNECTIVES = frozenset({"de", "da", "do", "das", "dos", "e"})
# Geracao no fim do nome: "Fulano Silva" e "Fulano Silva Filho" sao pessoas diferentes (E13).
_GENERATION = frozenset(
    {
        "filho", "filha", "junior", "jr", "neto", "neta", "sobrinho", "sobrinha",
        "segundo", "segunda", "terceiro", "terceira",
    }
)
_STRAY = re.compile(r"[^\w\s'’`´./,-]|_")
_PUNCTUATION = re.compile(r"[^\w\s]|_")
_HYPHEN_JOIN = re.compile(r"(?<=[^\W\d_])[-‐‑‒]\s*(?=[^\W\d_])")
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
    # "executada X": papel + nome de uma palavra so (E11). A higiene deixa o papel fora do token;
    # sem esta forma o nome curto deixaria de ser achado onde so o papel o ancora.
    role_phrases: list[str] = field(default_factory=list)
    # Captura de duas pessoas coladas: tem token proprio e nao recebe forma curta (E13).
    glued: bool = False

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
        self._role_phrases: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._ids: dict[tuple[str, str], str] = {}
        self._cnpjs: dict[str, str] = {}
        self._id_records: list[SimpleNamespace] = []
        self._entities: list[_Entity] | None = None
        self._by_core: dict[tuple[str, str], _Entity] = {}
        # Empresa vista em posicao de instituicao financeira (SISBAJUD) na fase 1.
        self._financial: set[str] = set()
        # Marca de uma palavra (E13): a palavra inicial dos nomes de empresa que o lote usa sozinha.
        self._brands: dict[str, str] = {}
        # Nome de 2+ palavras que a deteccao ja achou, por texto: dentro dele a marca nao esta "sozinha".
        self._named: dict[int, list[tuple[int, int]]] = {}
        self.brand_stats: dict[str, tuple[int, ...]] = {}
        # Palavra-marca que e nome de municipio ou de estado (B3): nunca vira marca; guarda as ocorrencias.
        self.brand_geo_words: dict[str, int] = {}
        # Ocorrencias da marca em contexto de foro/endereco ou dentro de termo protegido: sem token (B3).
        self.brand_context_rejected: dict[str, int] = {}
        # Ocorrencias da marca que receberam token na troca, por palavra.
        self.brand_applied: dict[str, int] = {}

    # --- fase 1 -------------------------------------------------------------
    def add(self, text: str, spans: Iterable[EntitySpan]) -> None:
        named = self._named.setdefault(hash(text), [])
        for span in spans:
            value = span.value_from(text)
            kind = _NAME_KINDS.get(span.entity_type)
            if kind is not None:
                if has_token(value) or is_protected(value, self._protected):
                    continue
                core = norm_core(kind, value)
                if len(_CONTENT.findall(core)) >= 2:
                    named.append((span.start, span.end))
                if _CONTENT.search(core):
                    self._names[(span.entity_type, core)].append(value)
                    if len(_CONTENT.findall(core)) < 2:
                        role = ROLE_BEFORE_NAME.search(text, max(0, span.start - 24), span.start)
                        if role is not None:
                            self._role_phrases[(span.entity_type, core)].append(
                                f"{role.group(1)} {value}"
                            )
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
        """Antes da troca: entidade vista em contexto de juiz ou procurador nao se propaga;
        palavra de marca usada sozinha no lote e contada (item E13)."""

        entries = self._entries(include_public=True)
        if not entries:
            return
        by_key = {entity.key: entity for entity in self._ensure()}
        candidates = self._brand_candidates()
        bare: dict[str, list[int]] = {word: [0, 0, 0, 0, 0] for word in candidates}
        for text in texts:
            folded = fold_with_index(text)
            spans = spans_for_roster(text, entries, extra_keep=self._extra_keep, folded=folded)
            for span in spans:
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
            if candidates:
                taken = [(span.start, span.end) for span in spans] + self._named.get(hash(text), [])
                for word in candidates:
                    for start, end in _find_all(folded.text, word):
                        written = text[slice(*folded.to_original(start, end))]
                        bare[word][2] += written.islower()
                        bare[word][3] += 1
                    refused: list[tuple[int, int]] = []
                    for start, end in _brand_hits(text, folded, word, taken, self._protected, refused):
                        bare[word][0] += 1
                        bare[word][1] += _capitalized(text[start:end])
                    bare[word][4] += len(refused)
        self._brands = self._approve_brands(candidates, bare)

    def _brand_candidates(self) -> dict[str, list[_Entity]]:
        """Palavra inicial de nome de empresa (2+ palavras de conteudo) que pode ser marca."""

        persons = {
            word
            for entity in self._ensure()
            if entity.entity_type == "PERSON"
            for core in entity.cores
            for word in core.split()
        }
        candidates: dict[str, list[_Entity]] = defaultdict(list)
        for entity in self._ensure():
            if entity.entity_type != "ORGANIZATION" or entity.financial:
                continue
            for core in entity.cores:
                words = [word for word in core.split() if word not in _CONNECTIVES]
                if len(words) < 2 or core.split()[0] in _CONNECTIVES:
                    continue
                word = words[0]
                if (
                    len(word) < _BRAND_MIN_LETTERS
                    or not word.isalpha()
                    or word in persons
                    or word in _ACTIVITY
                    or word in _LABEL_WORDS
                    or word in _GENERIC_BRAND
                    or is_protected(word, self._protected)
                ):
                    continue
                if entity not in candidates[word]:
                    candidates[word].append(entity)
        return dict(candidates)

    def _approve_brands(
        self, candidates: dict[str, list[_Entity]], bare: dict[str, list[int]]
    ) -> dict[str, str]:
        """Marca = palavra que o lote escreve sempre como nome proprio: nunca em minuscula (palavra
        comum, "aco", aparece em minuscula no meio da frase) e sozinha em quantidade que cabe numa
        sigla da empresa, nao num uso geral. Soma das mencoes do nome completo: o minimo."""

        approved: dict[str, str] = {}
        self.brand_stats = {}
        self.brand_geo_words = {}
        for word, owners in candidates.items():
            alone, capitalized, lower, total, refused = bare.get(word, (0, 0, 0, 0, 0))
            full = sum(entity.occurrences for entity in owners)
            self.brand_stats[word] = (len(owners), full, alone, capitalized, lower, total, refused)
            if is_geo_word(word):
                # Nome de municipio ou de estado (lista do IBGE): "Joinville Metais" nao faz do foro uma empresa.
                self.brand_geo_words[word] = alone + refused
                continue
            if alone == 0 or full < _BRAND_MIN_FULL or alone > _BRAND_MAX_RATIO * full:
                continue
            if lower > _BRAND_MAX_LOWER or lower * 50 > total:
                continue
            # Uma so empresa do lote comeca por essa palavra: a marca e dela. Mais de uma (irmas do
            # mesmo grupo, nome antigo e novo): a marca tem token proprio, sem decidir de quem e.
            approved[word] = owners[0].key if len(owners) == 1 else word
        return approved

    def brand_report(self, mapping: object, *, private: bool = False) -> list[str]:
        """Linhas da AUDITORIA sobre a marca de uma palavra (B3).

        Exportavel (``private=False``): so o token e as contagens, nunca a palavra. Privada: a palavra
        junto do token, para o Operador conferir o que foi aprovado e o que foi recusado."""

        def token_of(word: str) -> str:
            key = self._brands[word]
            finder = getattr(mapping, "token_for", None)
            return (finder("ORGANIZATION", key) if finder else "") or "(sem token: nenhuma ocorrencia trocada)"

        lines = [
            f"Marca de uma palavra propagada (palavra inicial do nome de empresa usada sozinha): "
            f"{len(self._brands)}"
        ]
        for word in sorted(self._brands, key=lambda item: (token_of(item), item)):
            used = self.brand_applied.get(word, 0)
            refused = self.brand_context_rejected.get(word, 0)
            owners, full, _alone, _capitalized, _lower, _total, _refused = self.brand_stats.get(
                word, (0, 0, 0, 0, 0, 0, 0)
            )
            shown = f'"{word}" -> ' if private else ""
            lines.append(
                f"  {shown}{token_of(word)}: {used} ocorrencia(s) com token, {refused} recusada(s) por contexto "
                f"de foro/endereco ou termo protegido; nome completo em {full} mencao(oes), {owners} empresa(s)"
            )
        geo_hits = sum(self.brand_geo_words.values())
        lines.append(
            f"Palavra recusada por ser nome de municipio ou de estado (lista do IBGE): "
            f"{len(self.brand_geo_words)} palavra(s), {geo_hits} ocorrencia(s) sem token"
        )
        if private:
            lines += [f'  "{word}": {hits} ocorrencia(s)' for word, hits in sorted(self.brand_geo_words.items())]
        return lines

    # --- elenco -------------------------------------------------------------
    def entities(self) -> list[_Entity]:
        return list(self._ensure())

    def key_for(self, entity_type: str, value: str) -> str:
        kind = _NAME_KINDS.get(entity_type)
        if kind is None:
            return ""
        core = norm_core(kind, value)
        if entity_type == "ORGANIZATION" and core in self._brands:
            return self._brands[core]  # a marca sozinha, achada pela deteccao, leva o mesmo token da propagada
        entity = self._by_core.get((entity_type, core)) if self._ensure() else None
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
            known = frozenset(cores)
            support = {core: len(names) for core, names in cores.items()}
            groups: list[_Entity] = []
            # Cabeca de grupo: mais palavras, depois a grafia mais vista (o nome certo antes do OCR).
            order = sorted(cores, key=lambda item: (-len(item.split()), -len(cores[item]), -len(item), item))
            for core in order:
                matches = [
                    group
                    for group in groups
                    if not group.glued and any(_same(entity_type, core, head, support) for head in group.cores)
                ]
                if entity_type == "PERSON" and _generation_sibling(core, known):
                    matches = []  # "Fulano Silva" com "Fulano Silva Filho" no lote: pai ou filho?
                target = _pick(entity_type, matches, core)
                if target is None:
                    target = _Entity(entity_type, kind)
                    target.glued = entity_type == "PERSON" and _glued(core, known)
                    groups.append(target)
                target.cores.append(core)
                target.names.extend(cores[core])
                target.role_phrases.extend(self._role_phrases.get((entity_type, core), ()))
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
                    variants=sorted({*entity.names, *entity.role_phrases}),
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
        if self._brands:
            # Depois da trava de termo generico: palavra unica de empresa e, ali, "generica"; a marca
            # foi julgada no elenco (palavra inicial do nome completo de empresa do lote).
            found.extend(self._brand_spans(text, [*found, *unified], folded))
        found = sanitize_spans(text, found)
        merged = sorted([*unified, *_gaps(found, unified)], key=lambda span: (span.start, span.end))
        return self._retype(merged)

    def lote_spans(
        self,
        text: str,
        taken: Sequence[EntitySpan],
        *,
        source_name: str = "",
        folded: FoldedText | None = None,
    ) -> list[EntitySpan]:
        """O que este elenco acrescenta a uma troca montada por outro elenco (pipeline unico, H5): os IDs ja
        vistos no lote, em qualquer grafia (``_id_spans``), e a marca de uma palavra aprovada (E13). So fora
        do que ``taken`` ja cobre, com as mesmas travas de ``apply``."""

        source = folded or fold_with_index(text)
        found = drop_protected_spans(text, self._id_spans(text), self._extra_keep, source_name=source_name)
        if self._brands:
            found.extend(self._brand_spans(text, [*found, *taken], source))
        found = sanitize_spans(text, found)
        return _gaps(found, taken)

    def _brand_spans(self, text: str, taken: Sequence[EntitySpan], folded: FoldedText | None) -> list[EntitySpan]:
        """A marca sozinha, escrita como nome proprio, tem token proprio (nao se funde a empresa nenhuma)."""

        source = folded or fold_with_index(text)
        occupied = [(span.start, span.end) for span in taken]
        spans: list[EntitySpan] = []
        for word, key in self._brands.items():
            refused: list[tuple[int, int]] = []
            for start, end in _brand_hits(text, source, word, occupied, self._protected, refused):
                if _capitalized(text[start:end]):
                    self.brand_applied[word] = self.brand_applied.get(word, 0) + 1
                    spans.append(
                        EntitySpan(
                            start=start,
                            end=end,
                            entity_type="ORGANIZATION",
                            score=_SCORE,
                            recognizer=RECOGNIZER,
                            canonical_key=key,
                        )
                    )
            if refused:
                self.brand_context_rejected[word] = self.brand_context_rejected.get(word, 0) + len(refused)
        return spans

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


def _merged(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Intervalos ordenados e sem sobreposicao (uniao)."""

    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _brand_hits(
    text: str,
    folded: FoldedText,
    word: str,
    occupied: Iterable[tuple[int, int]],
    protected: frozenset[str] = frozenset(),
    refused: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """Ocorrencias da palavra inteira que nao estao dentro de nome ja achado.

    Fora tambem (e vao para ``refused``, se dada): a ocorrencia em contexto de foro ou de endereco e a
    que cai dentro de termo protegido ("Tribunal Regional Federal"): ali a palavra nomeia um lugar ou
    um orgao, nao a empresa (B3)."""

    spans = _merged(occupied)
    starts = [start for start, _end in spans]
    hits: list[tuple[int, int]] = []
    for start, end in _find_all(folded.text, word):
        original_start, original_end = folded.to_original(start, end)
        position = bisect_left(starts, original_end) - 1
        inside = position >= 0 and spans[position][1] > original_start
        if inside or "\n" in text[original_start:original_end]:
            continue
        if geo_context(text, original_start, original_end) or _in_kept_term(
            text, original_start, original_end, protected
        ):
            if refused is not None:
                refused.append((original_start, original_end))
            continue
        hits.append((original_start, original_end))
    return hits


_NEIGHBOR_WORD = re.compile(r"[^\W\d_]+(?:[-‐‑][^\W\d_]+)*")


def _in_kept_term(text: str, start: int, end: int, protected: frozenset[str]) -> bool:
    """A ocorrencia e parte de um termo protegido de 2+ palavras ("Procuradoria-Geral da Fazenda Nacional")."""

    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    before = [m.group(0) for m in _NEIGHBOR_WORD.finditer(text, max(line_start, start - 60), start)][-3:]
    after = [m.group(0) for m in _NEIGHBOR_WORD.finditer(text, end, min(line_end, end + 60))][:3]
    middle = text[start:end]
    for first in range(len(before) + 1):
        for last in range(len(after) + 1):
            if first == 0 and last == 0:
                continue  # so a palavra: ja julgada como candidata
            phrase = " ".join([*before[len(before) - first :], middle, *after[:last]])
            if fold_value(phrase) in protected:
                return True
    return False


def _capitalized(value: str) -> int:
    return int(value.isupper() or value.istitle())


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


def _edge_trim(value: str) -> str:
    """Tira da borda a palavra minuscula curta que a captura trouxe junto ("a Fulano Tal", "Fulano Tal e").

    So em texto de caixa mista (nome todo em minuscula pode ter prenome de 3 letras) e sem
    deixar menos de 2 palavras."""

    words = value.split()
    if len(words) < 3 or value == value.lower():
        return value

    def junk(word: str) -> bool:
        bare = re.sub(r"\W", "", word)
        return not bare or (len(bare) <= 3 and bare.isalpha() and bare.islower())

    start, end = 0, len(words)
    while end - start > 2 and junk(words[start]):
        start += 1
    while end - start > 2 and junk(words[end - 1]):
        end -= 1
    return " ".join(words[start:end])


def norm_core(kind: str, value: str) -> str:
    """Forma de comparacao da grafia: sem acento, caixa, pontuacao solta (``}`` do OCR, ponto de
    abreviatura), com o hifen de quebra de linha desfeito ("ME-TAIS" = "METAIS"), sem sufixo
    societario, sem palavra minuscula solta na borda."""

    cleaned = _edge_trim(value).replace("&", " e ")
    cleaned = _HYPHEN_JOIN.sub("", cleaned)
    cleaned = _STRAY.sub(" ", cleaned)
    # O sufixo societario ("S/A", "S.A.", "Ltda.") sai antes de a pontuacao virar espaco.
    core = _PUNCTUATION.sub(" ", entity_core(kind, cleaned))
    return re.sub(r"\s+", " ", core).strip()


def _skeleton(core: str) -> str:
    """Letras das palavras de conteudo coladas: espaco, hifen e conectivo ("da", "e") nao distinguem."""

    return "".join(word for word in core.split() if word not in _CONNECTIVES)


def _generation(words: Sequence[str]) -> frozenset[str]:
    return frozenset(word for word in words if word in _GENERATION)


def _pick(entity_type: str, matches: list[_Entity], core: str = "") -> _Entity | None:
    """Uma so entidade combina: e ela. Varias: so a que domina o lote, com folga larga.

    Empresa: 3x mais grafias que a seguinte. Pessoa ("Jose Carlos" pode ser qualquer um): so nome
    com 3+ palavras de conteudo, e a cabeca tem de ter 10+ grafias e 10x as da seguinte."""

    if len(matches) == 1:
        return matches[0]
    if len(matches) < 2:
        return None
    ranked = sorted(matches, key=lambda group: len(group.names), reverse=True)
    top, second = len(ranked[0].names), len(ranked[1].names)
    if entity_type == "ORGANIZATION":
        return ranked[0] if top >= 3 * second else None
    content = [word for word in core.split() if word not in _CONNECTIVES]
    return ranked[0] if len(content) >= 3 and top >= 10 and top >= 10 * second else None


def _glued(core: str, known: frozenset[str]) -> bool:
    """Captura de duas pessoas coladas ("A B C" + "D E" na linha de assinaturas): a forma e a soma
    de duas formas ja vistas, com 2+ palavras cada. Nao e a identidade de ninguem."""

    words = core.split()
    return any(
        " ".join(words[:cut]) in known and " ".join(words[cut:]) in known
        for cut in range(2, len(words) - 1)
    )


def _generation_sibling(core: str, known: frozenset[str]) -> bool:
    """Existe, no lote, esta mesma pessoa com "Filho", "Junior", "Neto"...: a forma curta e dos dois."""

    words = core.split()
    if _generation(words):
        return False
    return any(
        other != core and other.split()[: len(words)] == words and _generation(other.split()[len(words) :])
        for other in known
    )


def _distance(left: str, right: str) -> int:
    """Edicao com troca de letras vizinhas (OCR e digitacao)."""

    rows = [list(range(len(right) + 1))]
    for row, a in enumerate(left, start=1):
        current = [row]
        for column, b in enumerate(right, start=1):
            best = min(rows[-1][column] + 1, current[column - 1] + 1, rows[-1][column - 1] + (a != b))
            if row > 1 and column > 1 and a == right[column - 2] and left[row - 2] == b:
                best = min(best, rows[-2][column - 2] + 1)
            current.append(best)
        rows.append(current)
    return rows[-1][-1]


_VOWELS = frozenset("aeiou")
# Marca de uma palavra (E13): minimo de letras e de mencoes do nome completo; a palavra sozinha pode
# aparecer ate 2x o que o nome completo aparece e quase nunca em minuscula.
_BRAND_MIN_LETTERS = 5
_BRAND_MIN_FULL = 20
_BRAND_MAX_RATIO = 2
_BRAND_MAX_LOWER = 2
# Palavra de uso geral que abre nome de empresa e nao e marca de ninguem.
_GENERIC_BRAND = frozenset(
    {
        "banco", "caixa", "grupo", "companhia", "sociedade", "associacao", "cooperativa", "fundacao",
        "instituto", "sindicato", "condominio", "prefeitura", "municipio", "estado", "brasil",
        "brasileira", "nacional", "central", "centro", "sistema", "sistemas", "nova", "novo", "norte",
        "sul", "leste", "oeste", "super", "mega", "agro", "auto", "casa", "rede", "unidade", "posto",
        "loja", "lojas",
    }
)
# Palavra de ramo de atividade: empresas irmas diferem so por ela ("Comercio"/"Comercial"), nao por erro.
_ACTIVITY = frozenset(
    {
        "comercio", "comercial", "comerciais", "industria", "industrial", "industriais", "servico",
        "servicos", "transporte", "transportes", "transportadora", "engenharia", "construcao",
        "construcoes", "construtora", "empreendimento", "empreendimentos", "participacao",
        "participacoes", "administradora", "administracao", "imobiliaria", "imobiliarias",
        "incorporadora", "distribuidora", "distribuicao", "metalurgica", "siderurgica", "agricola",
        "agropecuaria", "alimentos", "materiais", "metais", "supermercados", "tecnologia",
        "consultoria", "assessoria", "representacoes", "locacao", "locacoes", "importacao",
        "exportacao", "investimentos", "empresarial", "holding", "ferragens", "equipamentos",
    }
)


def _close(left: str, right: str, *, person: bool = False, first: bool = False) -> bool:
    """Mesma palavra salvo OCR/digitacao: 1 letra (5+ letras; 4+ no sobrenome) ou 2 letras (10+).

    A diferenca no fim da palavra e a que separa o par que nao se pode juntar: "Adriano"/"Adriana"
    e "Marcos"/"Marco" (prenome de pessoa), "Comercio"/"Comercial" e "Industria"/"Industrial"
    (ramo de atividade de empresa irma). Vale a troca de duas letras vizinhas ("Servicso"), a
    letra final dobrada ("Tavaress") e, no prenome, a consoante final lida errado (n/m). Duas
    palavras com digito ("FILIAL01"/"FILIAL02") nunca; uma so, e o OCR lendo "l" como "1"."""

    if left == right:
        return True
    shortest = min(len(left), len(right))
    if shortest < (4 if person and not first else 5):
        return False
    if any(char.isdigit() for char in left) and any(char.isdigit() for char in right):
        return False
    if left == right + right[-1] or right == left + left[-1]:
        return True
    swap = len(left) == len(right) and sorted(left) == sorted(right) and _distance(left, right) == 1
    extends = left.startswith(right) or right.startswith(left)
    if not swap:
        if person and first and (extends or (left[-1] != right[-1] and left[-1] in _VOWELS and right[-1] in _VOWELS)):
            return False
        if not person and (extends or left[-1] != right[-1]) and (left in _ACTIVITY or right in _ACTIVITY):
            return False
    return _distance(left, right) <= (2 if shortest >= 10 else 1)


def _same(entity_type: str, short: str, long: str, support: Mapping[str, int] | None = None) -> bool:
    """A grafia curta e a mesma entidade da longa? (a longa ja e cabeca de grupo)

    ``support``: quantas vezes cada grafia foi vista; o prenome com 1 letra de diferenca so se
    junta quando uma das duas e rara (erro de OCR), nunca quando as duas sao bem atestadas."""

    if short == long:
        return True
    small, large = short.split(), long.split()
    content = [word for word in small if word not in _CONNECTIVES]
    if len(content) < 2:
        return False
    if _generation(small) != _generation(large):
        return False
    if short.replace(" ", "") == long.replace(" ", "") or _skeleton(short) == _skeleton(long):
        return True
    if large[: len(small)] == small:
        return True
    if len(small) == len(large):
        diffs = [(index, a, b) for index, (a, b) in enumerate(zip(small, large)) if a != b]
        if len(diffs) != 1:
            return False
        index, left, right = diffs[0]
        person = entity_type == "PERSON"
        if not _close(left, right, person=person, first=person and index == 0):
            return False
        if index > 0 or entity_type != "PERSON":
            return True
        # Prenome ("Marina"/"Marita"): uma das duas grafias tem de ser rara; duas bem vistas sao duas pessoas.
        seen = sorted((support or {}).get(core, 0) for core in (short, long))
        return len(seen) == 2 and seen[0] * 4 <= seen[1]
    if entity_type != "PERSON" or small[0] != large[0] or not _close(small[-1], large[-1], person=True):
        return False
    # Primeiro e ultimo nome batem; os do meio da curta aparecem, na ordem, na longa.
    middle = iter(large[1:-1])
    return all(any(word == other for other in middle) for word in small[1:-1])

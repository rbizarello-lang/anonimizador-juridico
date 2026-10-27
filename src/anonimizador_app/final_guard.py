"""Guarda final antes da troca (H9-k): a ultima higiene dos spans de nome e endereco, venha o span de onde vier.

Medido no lote real de 2.492 folhas (19/10/2026, so contagens): a lista do caso com a carteira inteira e a
propagacao do elenco levavam um erro de uma folha ao lote todo. "grupo economico" (motor local) virou EMPRESA 433
vezes; o prenome de pessoas da carteira casou dentro de "Sao Paulo" 89 vezes; palavras comuns em minuscula
(adjetivo, verbo) casaram com sobrenome listado 129 vezes; agentes publicos e bancos ficaram trocados em parte;
"r. decisao" e o numero de registro da matricula ("R.4") viraram endereco; o fim do nome de um socio, sobrando
de um OCR, foi para o token de outro socio; e o mesmo socio da lista saiu em 7 a 12 tokens por OCR colado.

Aqui, sobre os spans ja escolhidos (o mesmo ponto da regra do veiculo, H9-h):

1. termo juridico ou palavra de papel inteiro (``grupo economico``, ``socio gerente``, ``compradora``) nao e nome;
2. palavra unica toda em minuscula nao e pessoa NEM empresa;
3. palavra dentro de nome de municipio ou estado ("Sao Paulo", tambem colado pelo OCR: "emSao Paulo") nao e nome;
4. (retirada no mesmo dia, decisao do Operador: o prenome sozinho de pessoa da lista segue trocado);
5. agente publico pelo cargo colado (usuario do eproc que nao e advogado, "Procurador(a) da Fazenda Nacional",
   secretario-geral da Junta, escrevente, tabeliao, registrador) fica em claro, salvo nome da lista do caso;
6. instituicao financeira (banco, corretora, DTVM, pagamentos, cooperativa de credito) fica em claro, salvo lista;
7. endereco: "r. decisao/despacho/juizo...", numero de registro ou averbacao ("R.4", "AV-3"), municipio ou UF sozinho,
   e lote/quadra sem logradouro ("Quadra H 15", "Lote 22") ficam em claro;
8. a cauda de nome que nao e da entrada do span mas e o fim do nome de OUTRA pessoa do elenco vai para ela (a da lista
   primeiro); se houver duas candidatas, fica num token proprio (na duvida, separado);
9. a grafia colada ou com 1-2 letras de lixo de OCR na frente ("FULANODETAL SOUZA", "BRFULANO ...") de um nome da lista
   do caso vai para a entrada da lista, quando so UMA entrada da lista casa (sem espacos, ate 2 letras de diferenca em
   nome longo, sem outra candidata perto).

Nada aqui acrescenta span novo: so retira (o texto fica em claro) ou troca a chave (o token). Dado generico; nenhum dado
de caso.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import fold_lexical

from .geo_names import inside_geo_name, is_geo_name, multi_word_geo_names
from .llm_hygiene import is_exact_protected, is_legal_term
from . import features
from .public_agents import public_agent_context
from .gemini_detector import is_registry_office
from .sense_guard import misread
from .address_guard import company_form, initial_not_street, not_an_address
from .variant_propagation import FAMILY_CUE_BEFORE, family_span

_NAME_TYPES = frozenset({"PERSON", "ORGANIZATION"})
_PARTICLES = frozenset({"de", "da", "do", "das", "dos", "e", "d"})
_WORD = re.compile(r"[^\W\d_]+")

# Palavra de papel ou qualificacao que o motor local ou a lista tomam por nome (medido no lote de 19/10/2026).
_ROLE_TERMS = frozenset(
    fold_lexical(item)
    for item in (
        "socio gerente", "sócio-gerente", "sócia gerente", "sócio administrador", "sócio ingressante",
        "sócio retirante", "sócio quotista", "sócia quotista", "compradora", "comprador", "vendedora", "vendedor",
        "outorgante", "outorgado", "outorgada", "transmitente", "adquirente", "adquirentes", "cedente",
        "cessionária", "cessionário", "fiador", "fiadora", "avalista", "demais sociedades", "demais sócios",
        "síntese fática", "sintese dos fatos", "indústria e comércio", "comércio e indústria", "grupo familiar",
        "grupo empresarial", "titular", "proprietário", "proprietária",
    )
)

_LOWER_SINGLE = re.compile(r"^[^\W\d_]+$")

# Usuario do eproc: "Usuario: ABC99 - NOME - ESTAGIARIO" (o advogado segue trocado).
_EPROC_USER_BEFORE = re.compile(r"(?i)usu[áa]rio\s*:\s*[\w.]+\s*[-–]\s*$")
_EPROC_ROLE_AFTER = re.compile(r"^\s*[-–]\s*([^\n•|]{2,40})")
_ADVOCACY = re.compile(r"(?i)advogad|procurador\s+da\s+parte|sociedade\s+de\s+advogados")

# Cargo publico colado depois do nome (mesma linha com hifen/virgula, ou a linha seguinte).
_PUBLIC_ROLE_AFTER = re.compile(
    r"(?i)^[ \t]*(?:[-–—,][ \t]*|\n[ \t]*)(?:p/\s*)?"
    r"(?:procurador(?:a)?(?:\s*\(a\))?\s+da\s+fazenda(?:\s+nacional)?"
    r"|secret[áa]ri[oa][\s-]*geral|secretaria\s+geral"
    r"|escrevente(?:\s+autorizad[oa])?|tabeli[ãa](?:o|es)?(?:\s+substitut[oa])?"
    r"|registrador(?:a)?|oficial\s+(?:de\s+registro|substitut[oa]|registrador(?:a)?)"
    r"|analista\s+judici[áa]ri[oa]|t[ée]cnic[oa]\s+judici[áa]ri[oa]|diretor(?:a)?\s+de\s+secretaria"
    r"|estagi[áa]ri[oa]|servidor(?:a)?\s+p[úu]blic[oa])\b"
)

_FINANCIAL = re.compile(
    r"(?i)^(?:banco|bco\b|bank\b)|\bbank\b|\bcorretora\b|\b[dc]tvm\b|\bdistribuidora\s+de\s+t[íi]tulos"
    r"|\bpagamentos(?:\s+s\.?\s*a\.?)?\s*$|\binstitui[cç][ãa]o\s+de\s+pagamento|\bcooperativa\s+de\s+cr[ée]dito"
    r"|\bsicoob\b|\bsicredi\b|\bcresol\b|\bcaixa\s+econ[ôo]mica"
)

_SOLEMN_R = re.compile(
    r"(?i)^r\.?\s*(?:decis|despach|senten|ju[ií]z|ac[óo]rd|decisum|pronunciament|manifesta|peti[cç]|parecer"
    r"|provimento|determina|julgad|voto|ordem|delibera)"
)
_REGISTRY_ENTRY = re.compile(r"(?i)^(?:r|av|ato)\s*\.?\s*[-–]?\s*\d{1,4}(?:\s*/\s*[\w.-]+)?$")
_LOT_ONLY = re.compile(
    r"(?i)^(?:(?:balne[áa]rio|loteamento|jardim|residencial|condom[íi]nio)\s+[^\n,;]{0,40}?\s+)?"
    r"(?:(?:lote|lt|quadra|qd|gleba|ch[áa]cara)\.?\s*(?:n[º°o]\.?\s*)?[\w-]{1,6}(?:\s+[\w-]{1,4})?[\s,–-]*){1,3}$"
)
_STREET_WORD = re.compile(
    r"(?i)\b(?:rua|r\.|avenida|av\.|travessa|alameda|rodovia|estrada|pra[cç]a|servid[ãa]o|largo|via|beco)\b"
)
_UF_TAIL = re.compile(r"\s*[/\-–]\s*[A-Za-z]{2}\s*$")


def is_role_term(value: str) -> bool:
    folded = " ".join(_WORD.findall(fold_lexical(value)))
    return folded in _ROLE_TERMS or is_legal_term(value)


def _inside_glued_geo(text: str, start: int, end: int) -> bool:
    """"emSao Paulo": a palavra antes do span, colada pelo OCR, termina com o comeco de um nome de lugar."""

    before = re.search(r"([^\W\d_]{3,})[ \t]+$", text[max(0, start - 30) : start])
    if before is None:
        return False
    word = fold_lexical(before.group(1))
    span_words = " ".join(_WORD.findall(fold_lexical(text[start:end])))
    names = multi_word_geo_names()
    for cut in range(len(word) - 2):
        if f"{word[cut:]} {span_words}" in names:
            return True
    return False


def _words(value: str) -> list[str]:
    return [word for word in _WORD.findall(fold_lexical(value)) if word not in _PARTICLES]


def _close(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1 or min(len(left), len(right)) < 4:
        return False
    return levenshtein(left, right, 1) <= 1


def levenshtein(left: str, right: str, limit: int) -> int:
    """Distancia de edicao com teto (devolve ``limit + 1`` quando passa)."""

    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, char in enumerate(left, 1):
        current = [row]
        best = row
        for col, other in enumerate(right, 1):
            value = min(previous[col] + 1, current[col - 1] + 1, previous[col - 1] + (char != other))
            current.append(value)
            best = min(best, value)
        if best > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _compact(value: str) -> str:
    return "".join(_WORD.findall(fold_lexical(value)))


class FinalGuard:
    """Indices do elenco para as regras 4, 8 e 9; ``apply`` roda por arquivo."""

    def __init__(self, entries: Sequence[object]) -> None:
        self._entries = {entry.key: entry for entry in entries}
        people = [entry for entry in entries if entry.kind == "pessoa" and entry.action == "tokenizar"]
        self._people = [(entry, _words(entry.name)) for entry in people if len(_words(entry.name)) >= 2]
        # v0.4.11: empresas do elenco pela forma de comparacao (e a marca, primeira palavra de conteudo) -> chave; a
        # forma de duas empresas fica sem dono (None): ainda nao e endereco, mas nao escolhe empresa.
        self._companies: dict[str, str | None] = {}
        for entry in entries:
            if entry.kind != "empresa" or entry.action != "tokenizar":
                continue
            for value in (entry.name, *entry.variants):
                form = company_form(value)
                if not form:
                    continue
                forms = {form}
                words = [word for word in form.split() if word not in _PARTICLES]
                if len(words) >= 2 and len(words[0]) >= 4 and not is_geo_name(words[0]):
                    forms.add(words[0])
                for item in forms:
                    owner = self._companies.get(item, entry.key)
                    self._companies[item] = entry.key if owner == entry.key else None
        self._locations: list[tuple[int, int]] = []
        self._ranges: list[tuple[int, int]] = []
        # v0.4.11: formas (palavras dobradas, com conectivos) das razoes sociais do elenco, para o pedaco do fim do nome
        # que caiu depois de uma quebra de linha ou de pagina ("...ADMINISTRADORA" | "DE BENS LTDA").
        self._company_words: list[tuple[tuple[str, ...], str]] = []
        for entry in entries:
            if entry.kind != "empresa" or entry.action != "tokenizar":
                continue
            for value in (entry.name, *entry.variants):
                words = tuple(company_form(value).split())
                if len(words) >= 3:
                    self._company_words.append((words, entry.key))
        self._listed_compact: list[tuple[str, object]] = []
        for entry in people:
            if entry.source != "lista":
                continue
            for value in (entry.name, *entry.variants):
                if len(_words(value)) >= 2:
                    self._listed_compact.append((_compact(value), entry))

    def apply(
        self, text: str, spans: Sequence[EntitySpan], counts: Counter[str] | None = None
    ) -> list[EntitySpan]:
        counts = counts if counts is not None else Counter()
        kept: list[EntitySpan] = []
        self._locations = sorted((span.start, span.end) for span in spans if span.entity_type == "LOCATION")
        self._ranges = [(span.start, span.end) for span in spans]
        for span in spans:
            if span.entity_type == "ORGANIZATION":
                tail = self._broken_tail(text, span)
                if tail is not None:
                    counts["fim_de_razao_social"] += 1
                    span = tail
                trimmed = self._trim_holding(text, span)
                if trimmed is not span:
                    counts["holding"] += 1
                    span = trimmed
            verdict = self._judge(text, span)
            if verdict is None:
                kept.append(span)
                continue
            reason, new_key = verdict
            counts[reason] += 1
            if reason == "sobrenome_de_familia":
                kept.append(family_span(text, span.start, span.end, span.score))
                continue
            if reason == "endereco_empresa" and new_key is not None:
                owner = self._entries.get(new_key)
                kept.append(
                    replace(
                        span, entity_type="ORGANIZATION", canonical_key=new_key,
                        alvo=owner.name if owner is not None else text[span.start : span.end],
                    )
                )
                continue
            if new_key is not None:
                owner = self._entries.get(new_key)
                alvo = owner.name if owner is not None else text[span.start : span.end]
                kept.append(replace(span, canonical_key=new_key, alvo=alvo))
        return kept

    def _judge(self, text: str, span: EntitySpan) -> tuple[str, str | None] | None:
        """``None``: o span fica como esta. ``(motivo, None)``: sai (texto em claro). ``(motivo, chave)``: troca a
        chave."""

        value = text[span.start : span.end]
        entry = self._entries.get(span.canonical_key)
        listed = getattr(entry, "source", "") == "lista"
        if not listed and features.enabled("case2_false_positives"):
            # v0.4.12: expressao generica ("empresa executada"), julgador com o titulo no trecho ("Min. X"), contato do
            # orgao publico no cabecalho. Palavra comum nunca vira token.
            reason = misread(text, span.start, span.end, span.entity_type)
            if reason == "expressao_generica" and is_role_term(text[span.start : span.end]):
                reason = "termo_juridico"
            if reason:
                return (reason, None)
        if (
            not listed
            and span.entity_type in {"ORGANIZATION", "LOCATION"}
            and features.enabled("notary_public_agents")
            and is_registry_office(text[span.start : span.end])
        ):
            return ("cartorio", None)  # v0.4.12: o nome do cartorio (delegatario de servico publico) fica em claro
        if span.entity_type == "LOCATION":
            if initial_not_street(text, span.start):
                return ("endereco_inicial_de_nome", None)
            return _judge_location(value) or self._judge_address(text, span, listed)
        if span.entity_type not in _NAME_TYPES:
            return None
        words = _WORD.findall(value)
        if len(words) == 1 and _LOWER_SINGLE.match(value.strip()) and value.strip().islower():
            return ("minuscula", None)
        if is_role_term(value):
            return ("termo_juridico", None)
        if len(words) == 1 and (
            inside_geo_name(text, span.start, span.end) or _inside_glued_geo(text, span.start, span.end)
        ):
            return ("lugar", None)
        if is_geo_name(value) and (span.entity_type == "ORGANIZATION" or len(words) >= 2):
            # Sobrenome sozinho que tambem e municipio (Cunha, Toledo) nao sai so por isso.
            return ("lugar", None)
        # O prenome ou o sobrenome sozinho de pessoa da lista segue trocado (decisao do Operador, 19/10/2026: deixar o
        # prenome em claro vaza; o falso positivo de prenome se resolve restringindo a lista a operacao do lote).
        pinned = bool(getattr(entry, "pinned", False)) and getattr(entry, "action", "") == "tokenizar"
        if span.entity_type == "PERSON" and not pinned and public_agent_context(text, span.start, span.end):
            # v0.4.11: nenhum agente publico vira token; a decisao e da ocorrencia (cargo colado, nenhuma prova de
            # particular na frase), inclusive para o nome da lista do caso (juiz homonimo de um socio listado).
            return ("agente_publico", None)
        if not listed and _public_agent(text, span.start, span.end):
            return ("agente_publico", None)
        if not listed and span.entity_type == "ORGANIZATION" and _FINANCIAL.search(value.strip()):
            return ("instituicao_financeira", None)
        if not listed and is_exact_protected(value):
            return ("protegido", None)  # sistema, orgao ou instituicao da lista protegida (Assinador SERPRO, B3 S.A.)
        if span.entity_type == "PERSON" and len(words) <= 2 and _family_cue(text, span.start, value):
            return ("sobrenome_de_familia", "familia")
        if span.entity_type == "PERSON" and entry is not None:
            moved = self._owner_of_tail(value, entry)
            if moved is not None:
                return ("cauda_de_outra_pessoa", moved)
            if not listed:
                glued = self._listed_glued(value)
                if glued is not None and glued != span.canonical_key:
                    return ("grafia_colada_da_lista", glued)
        return None

    def _judge_address(self, text: str, span: EntitySpan, listed: bool) -> tuple[str, str | None] | None:
        """v0.4.11, guarda de tipo ENDERECO: cartorio, protocolo, titulo de peca e empresa do elenco nao sao endereco; o
        span do LLM, do elenco e do NER exige marcador de endereco (a lista do caso e a heuristica de endereco, que ja
        exige logradouro, nao passam pelo marcador; o pedaco colado a outro endereco tambem nao)."""

        if listed or span.recognizer in _LISTED_RECOGNIZERS:
            return None
        value = text[span.start : span.end]
        reason = not_an_address(value, frozenset(self._companies))
        if not reason:
            return None
        if reason == "endereco_empresa":
            return (reason, self._companies.get(company_form(value)) or self._companies.get(_first_form(value)))
        if reason == "endereco_sem_marcador":
            if (
                span.recognizer in _ADDRESS_RECOGNIZERS
                or self._next_to_address(text, span)
                or _STREET_BEFORE.search(text[max(0, span.start - 25) : span.start])
                or _NUMBER_AFTER.match(text[span.end : span.end + 20])
            ):
                return None
        return (reason, None)

    def _next_to_address(self, text: str, span: EntitySpan) -> bool:
        for start, end in self._locations:
            if (start, end) == (span.start, span.end):
                continue
            if end <= span.start:
                gap = text[end : span.start]
            elif span.end <= start:
                gap = text[span.end : start]
            else:
                continue
            if len(gap) <= 4 and not gap.strip(" ,;-\u2013\t"):
                return True
        return False

    def _broken_tail(self, text: str, span: EntitySpan) -> EntitySpan | None:
        """O span e o FIM de uma razao social do elenco (palavras finais), e o texto antes dele (pulando quebra de
        linha, comentario de pagina e o conectivo "de/da/do" da propria razao social) termina com as palavras que faltam:
        leva o token da empresa inteira, com o conectivo da mesma linha dentro do token. Uma empresa so; senao nada."""

        words = tuple(company_form(text[span.start : span.end]).split())
        if not words or not self._company_words:
            return None
        before = _COMMENT.sub(" ", text[max(0, span.start - 300) : span.start])
        lead = _LEAD_CONNECTIVE.search(before)
        connective = lead.group(1).casefold() if lead is not None else ""
        previous = tuple(company_form(before[: lead.start()] if lead is not None else before).split())
        owners = set()
        for form, key in self._company_words:
            for cut in range(1, len(form) - len(words) + 1):
                head, rest = form[:cut], form[cut:]
                if connective:
                    if rest[:1] != (connective,):
                        continue
                    rest = rest[1:]
                if rest != words or len(head) < 1:
                    continue
                if previous[-len(head) :] == head and key != span.canonical_key:
                    owners.add(key)
        if len(owners) != 1:
            return None
        key = owners.pop()
        start = span.start
        window = text[max(0, span.start - 10) : span.start]
        raw = _LEAD_CONNECTIVE.search(window)
        if raw is not None:
            candidate = span.start - (len(window) - raw.start())  # o "DE" da mesma linha entra no token
            if not any(other_start < span.start and other_end > candidate for other_start, other_end in self._ranges):
                start = candidate
        owner = self._entries.get(key)
        return replace(span, start=start, canonical_key=key, alvo=owner.name if owner is not None else "")

    def _trim_holding(self, text: str, span: EntitySpan) -> EntitySpan:
        """v0.4.11: "holding X" / "a holding X": a palavra fica em claro; o nome vai para a empresa X do elenco."""

        match = _HOLDING_LEAD.match(text[span.start : span.end])
        if match is None or _HOLDING_LEAD.fullmatch(text[span.start : span.end].rstrip() + " "):
            return span
        start = span.start + match.end()
        rest = text[start : span.end]
        if not rest.strip() or not rest.strip()[:1].isupper():
            return span
        if _SOCIETARY_END.search(rest) and text[span.start : span.start + match.end()].strip()[:1].isupper():
            return span  # "Holding Alfa Ltda": o nome registrado comeca por Holding
        key = self._companies.get(company_form(rest)) or span.canonical_key
        owner = self._entries.get(key)
        return replace(span, start=start, canonical_key=key, alvo=owner.name if owner is not None else rest)

    def _owner_of_tail(self, value: str, entry: object) -> str | None:
        """Regra 8: o span tem palavra que nao e da entrada dele, e o conjunto e o FIM do nome de outra pessoa."""

        words = _words(value)
        if len(words) < 2:
            return None
        own = _words(entry.name)
        if all(any(_close(word, mine) for mine in own) for word in words):
            return None
        candidates = [
            other
            for other, names in self._people
            if other.key != entry.key and len(names) >= len(words) and names[-len(words) :] == words
        ]
        if not candidates:
            return None
        listed = [other for other in candidates if other.source == "lista"]
        if len(listed) == 1:
            return listed[0].key
        if len(candidates) == 1:
            return candidates[0].key
        return "pessoa:" + " ".join(words)  # duas candidatas: token proprio (na duvida, separado)

    def _listed_glued(self, value: str) -> str | None:
        """Regra 9: grafia colada ou com lixo de OCR na frente de um nome da lista (uma entrada so)."""

        compact = _compact(value)
        if len(compact) < 12 or not self._listed_compact:
            return None
        hits: dict[str, int] = {}
        for form, listed in self._listed_compact:
            first = _words(listed.name)[0]
            for candidate in (compact, compact[1:], compact[2:]):
                if not candidate.startswith(first):
                    continue  # a diferenca nunca no prenome: Mario/Maria, Joao/Jose sao pessoas diferentes
                distance = levenshtein(candidate, form, 4)
                if distance <= 4:
                    best = hits.get(listed.key)
                    hits[listed.key] = distance if best is None else min(best, distance)
        close = {key: distance for key, distance in hits.items() if distance <= (2 if len(compact) >= 15 else 0)}
        if len(close) != 1:
            return None
        key, distance = next(iter(close.items()))
        # Nenhuma outra entrada da lista a ate 2 de distancia a mais (gap): sem vizinha, sem ambiguidade.
        if any(other != key and other_distance <= distance + 2 for other, other_distance in hits.items()):
            return None
        return key


# O nome do logradouro sem a palavra "Rua" (rua com nome de pessoa): o marcador esta logo antes ou o numero logo depois.
_STREET_BEFORE = re.compile(
    r"(?i)(?<![^\W\d_])(?:rua|r\.|avenida|av\.|travessa|alameda|rodovia|estrada|pra[cç]a|servid[aã]o|largo|beco"
    r"|loteamento|bairro|logradouro|endere[cç]o)\s*:?\s*$"
)
_NUMBER_AFTER = re.compile(r"(?i)^\s*,?\s*(?:n[º°o.]\s*\d|n[uú]mero\s+\d|\d{1,5}\b|s/n)")
_LISTED_RECOGNIZERS = frozenset({"ListaDeAlvos", "ListaDoCaso"})
# O span da heuristica de endereco ja tem logradouro (ou e a continuacao numerada dele): nao passa pelo marcador.
_ADDRESS_RECOGNIZERS = frozenset({"HeuristicaEndereco", "FormFields", "TableLabels"})
_HOLDING_LEAD = re.compile(
    r"(?i)^(?:(?:a|da|na|pela|\u00e0)\s+)?(?:holding(?:\s+(?:patrimonial|familiar|patrimonial\s+familiar))?"
    r"|administradora\s+de\s+bens|empresa\s+(?:holding|patrimonial))\s+"
)
_SOCIETARY_END = re.compile(r"(?i)[\s,.\-]+(?:ltda|eireli|epp|me|s\.?\s*/?\s*a)\.?\s*$")


def _family_cue(text: str, start: int, value: str) -> bool:
    """O nome de pessoa e so um sobrenome (com particula) logo depois de "familia", "patronimico", "sobrenome"."""

    words = [word for word in _WORD.findall(value) if fold_lexical(word) not in _PARTICLES]
    if len(words) != 1:
        return False
    return bool(FAMILY_CUE_BEFORE.search(text[max(0, start - 40) : start]))


_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LEAD_CONNECTIVE = re.compile(r"(?i)(?<![^\W\d_])(d[aeo]s?|e)[ \t]*$")


def _first_form(value: str) -> str:
    words = company_form(value).split()
    return words[0] if len(words) == 1 else ""


def _public_agent(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    if _EPROC_USER_BEFORE.search(text[line_start:start]):
        role = _EPROC_ROLE_AFTER.match(text[end : end + 60])
        return role is not None and not _ADVOCACY.search(role.group(1))
    return bool(_PUBLIC_ROLE_AFTER.match(text[end : end + 80]))


def _judge_location(value: str) -> tuple[str, None] | None:
    compact = " ".join(value.split()).strip(" ,.;:")
    if _SOLEMN_R.match(compact):
        return ("endereco_r_decisao", None)
    if _REGISTRY_ENTRY.match(compact):
        return ("endereco_registro_matricula", None)
    if is_geo_name(_UF_TAIL.sub("", compact)) or is_geo_name(compact):
        return ("endereco_so_municipio", None)
    if _LOT_ONLY.match(compact) and not _STREET_WORD.search(compact):
        return ("endereco_lote_quadra", None)
    return None


AUDIT_LABELS: dict[str, str] = {
    "minuscula": "palavra unica em minuscula (nao e pessoa nem empresa)",
    "termo_juridico": "termo juridico ou palavra de papel (grupo economico, socio gerente, compradora...)",
    "lugar": "palavra de nome de municipio ou estado (Sao Paulo)",
    "agente_publico": "agente publico pelo cargo colado (eproc, Procurador da Fazenda, Junta, cartorio)",
    "instituicao_financeira": "instituicao financeira (banco, corretora, pagamentos)",
    "protegido": "termo da lista protegida (sistema, orgao, instituicao)",
    "endereco_r_decisao": "\"r. decisao/despacho/juizo\" tomado por endereco",
    "endereco_registro_matricula": "numero de registro ou averbacao da matricula (R.4, AV-3)",
    "endereco_so_municipio": "municipio ou UF sozinho como endereco",
    "endereco_lote_quadra": "lote ou quadra sem logradouro",
    "cauda_de_outra_pessoa": "fim do nome de outra pessoa levado ao token dela (ou a token proprio)",
    "grafia_colada_da_lista": "grafia colada ou com lixo de OCR de nome da lista levada a entrada da lista",
    "endereco_cartorio": "cartorio ou registro publico tomado por endereco (v0.4.11)",
    "endereco_protocolo": "numero de protocolo tomado por endereco (v0.4.11)",
    "endereco_titulo_documento": "titulo de peca ou documento tomado por endereco (v0.4.11)",
    "endereco_empresa": "empresa do elenco tomada por endereco, levada ao token da empresa (v0.4.11)",
    "endereco_sem_marcador": "endereco sem logradouro, numero, bairro, CEP ou lote (v0.4.11)",
    "endereco_vazio": "endereco vazio (v0.4.11)",
    "endereco_inicial_de_nome": "\"R.\" de inicial de nome (A. R. Souza) tomado por \"Rua\" (v0.4.11)",
    "holding": "\"holding\" fora do token da empresa (v0.4.11)",
    "fim_de_razao_social": "fim de razao social depois de quebra de linha ou de pagina levado ao token da empresa (v0.4.11)",
    "sobrenome_de_familia": "sobrenome de familia (\"familia X\") com token de sobrenome, nao o de uma pessoa (v0.4.11)",
}


def audit_lines(counts: Counter[str]) -> list[str]:
    total = sum(counts.values())
    parts = ", ".join(f"{AUDIT_LABELS[key]} {counts[key]}" for key in AUDIT_LABELS if counts.get(key))
    return [f"Guarda final (H9-k): {total} span(s) retirados ou com o token corrigido" + (f" ({parts})" if parts else "")]


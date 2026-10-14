"""Higiene das mencoes do LLM, antes de entrarem no elenco (H9-c).

O motor local passa por ``sanitize_spans`` e pelo juizo do lote; a mencao do LLM entrava no elenco sem
filtro. Medido no lote IDPJ com o ``qwen3:4b``: trocas fora do gabarito 21 -> 580 registros, termo comum,
rotulo com numero, classe processual, cidade e bairro virando PESSOA/EMPRESA/ENDERECO, mencao sem ancora,
classe e papel inventados pelo modelo, token colado em numero.

Esta funcao descarta so mencao DO LLM que nao e nome de pessoa, de empresa nem endereco. Nunca toca no que
o motor local achou: a politica "o LLM so acrescenta protecao, nunca tira" continua valendo.

Motivos de descarte (contados em ``LlmHygieneStats``; a AUDITORIA leva so os numeros):
classe fora de pessoa/empresa/endereco; sem ancora no texto; so minusculas; termo protegido (ou nome que o
contem); rotulo seguido de numero, so numero ou sigla; termo juridico; municipio, UF ou lugar solto; pessoa
de uma palavra so (sem nome completo no elenco que a contenha). Mais: mencao colada a numero e recortada.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from anonimizador_juridico.pseudonymizer import fold_lexical

from .gemini_detector import KEEP_TERMS
from .geo_names import is_geo_name, is_state_name, place_label_before
from .roster import (
    KEEP,
    ROLE_POLICY,
    TOKENIZE,
    UNKNOWN_ROLE,
    Mention,
    Roster,
    entity_core,
    normalize_kind,
    strip_token_tail,
)

_KINDS = frozenset({"pessoa", "empresa", "endereco"})
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_TOKENS = re.compile(r"[^\W_]+")
_VOWEL = re.compile(r"[AEIOUaeiou]")
_COMPANY_SUFFIX = re.compile(r"(?i)\b(?:ltda|me|epp|eireli|s/?a|s\.a|cia|companhia)\b")
_DOMAIN = re.compile(r"(?i)[\w-]+\.[a-z]{2,}|@")
_SIGLA = re.compile(r"^[A-Z]{2,5}$")

# Rotulo de peca seguido de numero ("Pagina 12", "Evento 45", "Tema 1.209") ou sozinho ("Despacho",
# "Valor Total"): e o rotulo do documento, nao uma pessoa.
_LABEL_NUMBER = re.compile(
    r"(?i)^(?:p[aá]gina|p[aá]g\.?|fls?\.?|evento|doc\.?|anexo|par[aá]grafo|art\.?|artigo|processo|despacho|"
    r"decis[aã]o|valor(?:\s+total)?|tema|s[uú]mula|lei|cl[aá]usula|item|inciso|certid[aã]o|of[ií]cio|"
    r"mandado|id|seq\.?)"
    r"(?:\s*(?:n[º°o]\.?|n\.)?\s*(?:\d|:)|\s*$)"
)

# Siglas que a higiene reconhece como sigla processual, alem da regra "ate 5 letras maiusculas sem vogal".
_SIGLAS_PROCESSUAIS = frozenset(
    "cda cpc ctn lef idpj cnpj cpf oab cep rg eproc pje dje trf trt tst tse stm cnh ar".split()
)

# Termo juridico/processual de uso comum: nunca e nome de pessoa, empresa nem endereco. Comparado por
# ``fold_lexical`` (sem acento, minusculo): igual ao termo, ou comeca por ele (termo de varias palavras e as
# poucas palavras de rotulo de ``_PREFIXO_SO_ROTULO``): "Codigo de Processo Civil de 2015".
_TERMOS_JURIDICOS = frozenset(
    fold_lexical(item)
    for item in (
        "decisão interlocutória", "decisão", "despacho", "despacho decisório", "sentença", "acórdão", "ementa",
        "execução fiscal", "execuções fiscais", "embargos", "embargos à execução", "embargos de declaração",
        "embargos de terceiro", "desconsideração da personalidade jurídica",
        "desconsideração inversa da personalidade jurídica", "incidente de desconsideração",
        "pessoa jurídica", "pessoa física", "pessoa natural", "código de processo civil", "código civil",
        "código tributário nacional", "código de defesa do consumidor", "código de processo penal",
        "lei", "lei complementar", "lei ordinária", "súmula", "tema", "recurso especial",
        "recurso extraordinário", "recurso de apelação", "agravo", "agravo de instrumento", "agravo interno",
        "apelação", "apelação cível", "contestação", "petição inicial", "petição", "réplica",
        "mandado de segurança", "mandado de penhora", "cumprimento de sentença", "certidão de dívida ativa",
        "dívida ativa", "ação anulatória", "ação declaratória", "ação civil pública", "ação de execução",
        "medida cautelar fiscal", "cautelar fiscal", "tutela de urgência", "tutela provisória", "penhora",
        "arresto", "citação", "intimação", "sucumbência", "honorários advocatícios", "honorários",
        "correção monetária", "juros de mora", "trânsito em julgado", "coisa julgada", "prescrição",
        "prescrição intercorrente", "decadência", "responsabilidade tributária", "grupo econômico",
        "fraude à execução", "exceção de pré-executividade", "impugnação", "impugnação ao cumprimento",
        "valor total", "valor da causa", "valor da dívida", "valor do débito", "valor atualizado",
        "documento eletrônico", "documento", "classe processual", "assunto", "órgão julgador",
        "parte autora", "parte ré", "polo ativo", "polo passivo", "exequente", "executado", "executada",
        "embargante", "embargado", "requerente", "requerido", "agravante", "agravado", "apelante", "apelado",
        "recorrente", "recorrido", "impetrante", "impetrado", "terceiro interessado", "ministério público",
    )
)
_PREFIXO_SO_ROTULO = frozenset({"lei", "sumula", "decisao", "despacho", "sentenca", "acordao", "ementa"})

# Termo protegido de uma palavra que, DENTRO de um nome, tambem o descaracteriza (sigla de instituicao).
# As palavras comuns da lista (uniao, tribunal, evento, comarca...) so valem inteiras: "Uniao Comercial de
# Graos Ltda" e empresa e tem de ser trocada.
_SIGLAS_INSTITUCIONAIS = frozenset(
    "pgfn pfn inss stf stj trf4 cnj sisbajud renajud cnib infojud cpc ctn lef mpf dpu agu pgf pge pgm "
    "sniper censec serasajud".split()
)
_UNIAO_FEDERAL = ("uniao", "federal")


@dataclass(slots=True)
class LlmHygieneStats:
    """Contagens da higiene das mencoes do LLM (so numeros: vao para a AUDITORIA)."""

    classe: int = 0
    ancora: int = 0
    minusculas: int = 0
    protegido: int = 0
    rotulo_numero: int = 0
    juridico: int = 0
    geografia: int = 0
    uma_palavra: int = 0
    colado_recortado: int = 0
    colado_descartado: int = 0
    # Juizo do lote sobre as entradas do LLM (``judge_llm_entries``).
    lote_publico: int = 0
    lote_financeiro: int = 0
    lote_rua: int = 0
    # Mencoes que entraram no elenco depois do filtro.
    aceitas: int = 0

    @property
    def descartadas(self) -> int:
        return (
            self.classe + self.ancora + self.minusculas + self.protegido
            + self.rotulo_numero + self.juridico + self.geografia + self.uma_palavra
        )

    def audit_lines(self, kept_public: int = 0, mixed: int = 0) -> list[str]:
        return [
            (
                f"Higiene das mencoes do LLM: descartadas {self.descartadas} "
                f"(classe fora da lista {self.classe}, sem ancora {self.ancora}, minusculas {self.minusculas}, "
                f"termo protegido {self.protegido}, rotulo/numero {self.rotulo_numero}, "
                f"termo juridico {self.juridico}, geografia {self.geografia}, uma palavra {self.uma_palavra}); "
                f"entradas mantidas pelo papel publico do LLM: {kept_public}; papel misto: {mixed}"
            ),
            (
                f"Mencoes do LLM coladas a numero: recortadas {self.colado_recortado}, "
                f"descartadas {self.colado_descartado}; aceitas no elenco: {self.aceitas}"
            ),
            (
                "Juizo do lote sobre as entradas do LLM: mantidas por agente publico "
                f"{self.lote_publico}, por instituicao financeira {self.lote_financeiro}, "
                f"retipadas como rua com nome de pessoa {self.lote_rua}"
            ),
        ]


def _words(value: str) -> list[str]:
    return _TOKENS.findall(fold_lexical(value))


def _contains_run(words: Sequence[str], term: Sequence[str]) -> bool:
    size = len(term)
    if not size or size > len(words):
        return False
    return any(list(words[index : index + size]) == list(term) for index in range(len(words) - size + 1))


_MULTIWORD_KEEP = tuple(
    sorted(
        {tuple(_words(term)) for term in KEEP_TERMS if len(_words(term)) >= 2},
        key=len,
        reverse=True,
    )
)


def _protected(value: str, extra: Sequence[str], *, contains: bool) -> bool:
    """Termo protegido inteiro; com ``contains``, tambem o nome que o traz dentro (Receita Federal de X)."""

    if _exact_or_federal(value, extra):
        return True
    if not contains:
        return False
    words = _words(value)
    if any(word in _SIGLAS_INSTITUCIONAIS for word in words):
        return True
    if any(_contains_run(words, term) for term in _MULTIWORD_KEEP):
        return True
    for item in extra:
        term = _words(str(item))
        if term and _contains_run(words, term):
            return True
    return False


def _exact_or_federal(value: str, extra: Sequence[str]) -> bool:
    """``is_kept_term`` tambem aceita "Uniao Comercial ..." (prefixo); aqui so vale o termo inteiro e a
    "Uniao Federal"."""

    words = _words(value)
    if tuple(words[:2]) == _UNIAO_FEDERAL:
        return True
    folded = " ".join(words)
    terms = {" ".join(_words(term)) for term in (*KEEP_TERMS, *extra)}
    return folded in terms


def _is_legal_term(value: str) -> bool:
    folded = fold_lexical(value)
    if folded in _TERMOS_JURIDICOS:
        return True
    for term in _TERMOS_JURIDICOS:
        if folded.startswith(term + " ") and (" " in term or term in _PREFIXO_SO_ROTULO):
            return True
    return False


def _has_initial_capital(value: str) -> bool:
    return any(word[0].isupper() for word in _WORD.findall(value))


def _digit_share(value: str) -> float:
    chars = [char for char in value if char.isalnum()]
    if not chars:
        return 1.0
    return sum(char.isdigit() for char in chars) / len(chars)


def _is_label_or_number(value: str) -> bool:
    compact = value.strip()
    if not any(char.isalpha() for char in compact):
        return True  # so numero e pontuacao
    if _digit_share(compact) > 0.6:
        return True
    if _LABEL_NUMBER.match(compact):
        return True
    if _SIGLA.match(compact) and not _VOWEL.search(compact):
        return True  # sigla de ate 5 letras sem vogal ("TRF", "STJ", "PGFN")
    return False


def _person_words(value: str) -> list[str]:
    return _words(entity_core("pessoa", value))


def _known_full_names(mentions: Sequence[Mention], known_people: Iterable[str]) -> list[list[str]]:
    names: list[list[str]] = []
    for item in known_people:
        words = _person_words(str(item))
        if len(words) >= 2:
            names.append(words)
    for mention in mentions:
        for value in (mention.canonical, mention.text):
            if normalize_kind(mention.kind) == "pessoa" and value:
                words = _person_words(strip_token_tail(value))
                if len(words) >= 2:
                    names.append(words)
    return names


def _geo_reason(kind: str, value: str, text: str, start: int) -> bool:
    """Municipio, estado, UF ou lugar solto onde devia haver pessoa ou empresa (ou rua)."""

    if kind == "endereco":
        # Cidade/UF sozinha nao e logradouro; rua tem via ou numero.
        return is_geo_name(value) and not any(char.isdigit() for char in value)
    words = _WORD.findall(value)
    if not words:
        return False
    if _SIGLA.match(value.strip()) and is_geo_name(value):
        return True
    if is_geo_name(value) and (kind == "empresa" or len(words) == 1 or is_state_name(value)):
        return True
    # Rotulo de lugar logo antes ("Comarca de X", "Bairro: X"): nome curto sem sufixo societario.
    if 0 <= start <= len(text) and len(words) <= 3 and not _COMPANY_SUFFIX.search(value) and place_label_before(text, start):
        return True
    return False


def _trim_glued(text: str, mention: Mention, stats: LlmHygieneStats | None) -> Mention | None:
    """Mencao colada a numero ("2024PEDRO SILVA", "ACME123"): o token ficaria colado em digito. Recorta a
    primeira ou a ultima palavra (a que encosta no numero) quando sobram duas ou mais; senao, descarta."""

    start, end = mention.start, mention.end
    changed = False
    for _ in range(2):
        left_glued = start > 0 and text[start - 1].isdigit()
        right_glued = end < len(text) and text[end].isdigit()
        if not left_glued and not right_glued:
            break
        value = text[start:end]
        if left_glued:
            cut = re.search(r"\s", value)
            if cut is None:
                return _glued_drop(stats)
            start += cut.end()
        elif right_glued:
            cuts = list(re.finditer(r"\s", value))
            if not cuts:
                return _glued_drop(stats)
            end = start + cuts[-1].start()
        changed = True
        while end > start and text[end - 1] in " \t,;:.-":
            end -= 1
        while start < end and text[start] in " \t,;:-":
            start += 1
        if end <= start or len(_WORD.findall(text[start:end])) < 2:
            return _glued_drop(stats)
    if not changed:
        return mention
    if stats is not None:
        stats.colado_recortado += 1
    piece = text[start:end]
    return replace(mention, text=piece, start=start, end=end, source_text=piece)


def _glued_drop(stats: LlmHygieneStats | None) -> None:
    if stats is not None:
        stats.colado_descartado += 1
    return None


def filter_llm_mentions(
    mentions: Iterable[Mention],
    text: str,
    stats: LlmHygieneStats | None = None,
    *,
    extra_keep: Sequence[str] = (),
    known_people: Iterable[str] = (),
) -> list[Mention]:
    """Mencoes do LLM que merecem entrar no elenco. ``text`` e o que o LLM leu (as posicoes das mencoes sao
    dele). ``known_people``: nomes que o elenco ja tem; pessoa de uma palavra so passa se algum nome completo
    (do elenco, de outra mencao ou o ``nome_canonico`` dela) a contem."""

    items = list(mentions)
    extra = tuple(str(item) for item in extra_keep if str(item).strip())
    full_names = _known_full_names(items, known_people)
    kept: list[Mention] = []
    for mention in items:
        reason = _reject(mention, text, extra, full_names)
        if reason is not None:
            if stats is not None:
                setattr(stats, reason, getattr(stats, reason) + 1)
            continue
        kind = normalize_kind(mention.kind)
        if kind in {"pessoa", "empresa"}:
            trimmed = _trim_glued(text, mention, stats)
            if trimmed is None:
                continue
            mention = trimmed
        kept.append(mention)
    if stats is not None:
        stats.aceitas += len(kept)
    return kept


def _reject(mention: Mention, text: str, extra: Sequence[str], full_names: Sequence[Sequence[str]]) -> str | None:
    kind = normalize_kind(mention.kind)
    if kind not in _KINDS:
        return "classe"
    if not mention.anchored:
        return "ancora"
    value = strip_token_tail(mention.text)
    if not value:
        return None  # so token: o elenco ja o descarta
    is_street = kind == "endereco"
    if not is_street:
        letters = any(char.isalpha() for char in value)
        if letters and not _has_initial_capital(value) and not (kind == "empresa" and _DOMAIN.search(value)):
            return "minusculas"
    if _protected(value, extra, contains=not is_street):
        return "protegido"
    if _is_label_or_number(value):
        return "rotulo_numero"
    if not is_street and _is_legal_term(value):
        return "juridico"
    if _geo_reason(kind, value, text, mention.start):
        return "geografia"
    if kind == "pessoa":
        words = _person_words(value)
        if len(words) <= 1:
            word = words[0] if words else ""
            if not word or not any(word in names for names in full_names):
                return "uma_palavra"
    return None


# --- papeis do LLM (item 2) ---------------------------------------------------------------------

PUBLIC_ROLES = frozenset(role for role, action in ROLE_POLICY.items() if action == KEEP)
PRIVATE_ROLES = frozenset(role for role, action in ROLE_POLICY.items() if action == TOKENIZE)


def role_counts(roster: Roster) -> tuple[int, int]:
    """(entradas mantidas so pelo papel publico que o LLM atribuiu, entradas de papel misto: publico e
    privado ao mesmo tempo). Papel ``desconhecido`` nao conta. So contagens."""

    kept = mixed = 0
    for entry in roster.entries:
        roles = {role for role in entry.roles if role != UNKNOWN_ROLE}
        public, private = roles & PUBLIC_ROLES, roles & PRIVATE_ROLES
        if public and private:
            mixed += 1
        elif public and not private and entry.action == KEEP:
            kept += 1
    return kept, mixed

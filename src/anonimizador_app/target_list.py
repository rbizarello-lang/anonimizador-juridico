"""Lista de alvos: nomes e CPF/CNPJ que o usuario manda tokenizar sempre.

Camada deterministica aceita em 29/08/2026 (GOVERNANCA.md, secao 8.5).
Nao e regex faminta: so busca o que o usuario mandou. Indiferente a
maiusculas, acentos e quebra de linha no meio da razao social.
Pessoas: tambem as particulas do nome ja informado (MATEUS, BERNARDELLI),
com fronteira de palavra. Empresa: so frase completa, sufixo societario
retirado, prefixo com >=2 palavras de conteudo e cauda com ``&``.
Nao extrai particula de 1 palavra da razao (2.0-A). Palavras com 6+
letras aceitam 1 caractere trocado, inserido ou omitido (OCR).
KEEP_TERMS, a lista de exclusao e a denylist de particula (UF, cidade,
mes, estado civil, tese, orgao, palavra de giro) vencem — inclusive no
match fuzzy (CAMPOS nao come Campo Mourao; MARCOS nao come marco).

Ciclo 1: cada span resolve para o token da ENTRADA da lista que o originou
(``canonical_key`` / ``alvo``), fundindo sufixos LTDA/EIRELI/ME da mesma
razao e desambiguando particula compartilhada pela regra documentada em
``resolve_particle_alias``.

Ciclo 5c: particula comum (Silva, Lopes, Marcelo) so cola com rotulo de
parte, outra particula do mesmo alvo colada por DE/DA/DO, ou span ja
emitido colado da mesma forma. Nao basta coocorrer a 40 caracteres.
Frase traseira (Lopes da Silva) e buscada como nome, nao como dois
isolados. Fuzzy de 1 letra nao roda em particula comum.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from anonimizador_juridico import EntitySpan, resolve_overlaps
from anonimizador_juridico.pseudonymizer import fold_lexical

from .fidelity import is_magistrate_or_citation, is_pfn_officer
from .gemini_detector import is_kept_term
from .target_ids import find_listed_id_spans, names_from_listed_cnpjs, parse_target_ids

# Nomes muito curtos (siglas de 1-2 letras) causariam trocas acidentais.
_MIN_TARGET_CHARS = 3
_MIN_PARTICLE_CHARS = 5
_MIN_FUZZY_CHARS = 6
_TOKEN = re.compile(r"[A-Za-zÀ-ÿĀ-ž0-9]+")
_LEGAL_TAIL = re.compile(
    r"(?:"
    r"[\s,.\-–—]+"
    r"(?:ltda\.?|etda|lida|eireli\.?|s/?a\.?|s\.a\.?|me|epp|&\s+esquadrias)"
    r")+$",
    re.IGNORECASE,
)
_LEGAL_TOKENS = frozenset({"ltda", "etda", "lida", "eireli", "me", "epp", "sa"})
_CONNECTIVES = frozenset({"de", "da", "do", "dos", "das", "e", "&"})
_NEXT_TOKEN = re.compile(r"[\s,.\-–—]*(&\s+)?([A-Za-zÀ-ÿĀ-ž0-9./]+)")
_CORPORATE_FOLLOW = re.compile(
    r"(?ix)\s+"
    r"(?:com[eé]rcio|ind[uú]stria|participa(?:[cç][oõ]es)?"
    r"|transportes|constru[cç][oõ]es|servi[cç]os|holding"
    r"|ltda\.?|eireli|s/?a\.?|s\.a\.?|\bme\b|\bepp\b)"
)
_PARTICLE_SKIP = frozenset(
    {
        "junior",
        "júnior",
        "filho",
        "filha",
        "neto",
        "neta",
        "de",
        "da",
        "do",
        "dos",
        "das",
        "e",
        "ltda",
        "me",
        "epp",
        "eireli",
    }
)
_COMPANY_PARTICLE_SKIP = _PARTICLE_SKIP | frozenset(
    {
        "comercio",
        "comercial",
        "industria",
        "industrias",
        "participacoes",
        "transportes",
        "construcoes",
        "servicos",
        "vidros",
        "acessorios",
        "esquadrias",
        "holding",
        "acessorios",
        "vidros",
        "confeccoes",
        "esquadrias",
        "limitada",
        "sociedade",
        "empresaria",
        "companhia",
        "artigos",
        "pecas",
        "materiais",
        "produtos",
    }
)
# Prenome/sobrenome frequente (≥5 letras). A frase completa, a frase
# traseira (Lopes da Silva) e a partícula distinta (CANTAGALO, BERNARDELLI)
# continuam. Isolado só com rótulo de parte ou cola onomástica DE/DA/DO.
_COMMON_PERSON_PARTICLES = frozenset(
    {
        "adriana",
        "alessandra",
        "alexandre",
        "almeida",
        "alves",
        "amanda",
        "anderson",
        "andrade",
        "antonio",
        "araujo",
        "azevedo",
        "barbara",
        "barbosa",
        "barros",
        "batista",
        "bruna",
        "bruno",
        "camila",
        "campos",
        "cardoso",
        "carla",
        "carlos",
        "carolina",
        "carvalho",
        "castro",
        "cesar",
        "claudia",
        "claudio",
        "coelho",
        "costa",
        "cristina",
        "cunha",
        "daniel",
        "daniela",
        "eduardo",
        "farias",
        "fernanda",
        "fernandes",
        "fernando",
        "francisco",
        "freitas",
        "gabriel",
        "gabriela",
        "garcia",
        "gomes",
        "goncalves",
        "guimaraes",
        "gustavo",
        "helena",
        "henrique",
        "jessica",
        "juliana",
        "leandro",
        "leonardo",
        "leticia",
        "lima",
        "lopes",
        "luciana",
        "luciano",
        "machado",
        "marcelo",
        "marcia",
        "marcio",
        "marcos",
        "maria",
        "mariana",
        "marina",
        "martins",
        "medeiros",
        "mendes",
        "monteiro",
        "moraes",
        "moreira",
        "moura",
        "nascimento",
        "nunes",
        "oliveira",
        "patricia",
        "paulo",
        "pedro",
        "pereira",
        "pinto",
        "rafael",
        "ramos",
        "ribeiro",
        "ricardo",
        "roberta",
        "roberto",
        "rocha",
        "rodrigues",
        "rodrigo",
        "santos",
        "silva",
        "soares",
        "souza",
        "sousa",
        "teixeira",
        "vanessa",
        "vieira",
        "xavier",
    }
)
_ROLE_CUE = re.compile(
    r"(?i)(?:devedor(?:a|es)?|codevedor(?:a|es)?|adquirente(?:s)?|"
    r"s[oó]ci[oa]s?|executad[oa]s?|coexecutad[oa]s?|"
    r"requerid[oa]s?|embargante(?:s)?|transmitente(?:s)?|"
    r"testemunha(?:s)?|inventariante(?:s)?|"
    r"representante(?:s)?\s+legal|"
    r"\br[eé]us?\b|\br[eé]\b|corr[eé]us?|"
    r"nome\s+d[oa]s?\s+devedor(?:a|es)?)\s*[:\-–—]?\s*$"
)
# Cola onomastica: espaco ou 1 quebra, opcionalmente DE/DA/DO/DOS/DAS.
# Nao inclui "e" (Silva e Lopes sao duas pessoas) nem virgula.
_NAME_GLUE = re.compile(
    r"(?ix)^"
    r"(?:[ \t]+|[ \t]*\n[ \t]*)"
    r"(?:(?:d[ao]s?|de)(?:[ \t]+|[ \t]*\n[ \t]*))?"
    r"$"
)
# Palavra unica que nunca vira semente nem match de particula. Frase de 2+
# palavras da lista continua buscada (a razao "… do Parana Ltda" ainda casa).
_PARTICLE_BLOCKLIST = frozenset(
    {
        # UF / pais
        "acre",
        "alagoas",
        "amapa",
        "amazonas",
        "bahia",
        "ceara",
        "goias",
        "maranhao",
        "paraiba",
        "parana",
        "pernambuco",
        "piaui",
        "rondonia",
        "roraima",
        "sergipe",
        "tocantins",
        "brasil",
        "brasileira",
        "brasileiro",
        # cidade / geo (nao inclui sobrenome comum: silva, santos, campos, costa)
        "curitiba",
        "florianopolis",
        "joinville",
        "blumenau",
        "camboriu",
        "cascavel",
        "maringa",
        "londrina",
        "pelotas",
        "ubirata",
        "campo",
        "mourao",
        "alegre",
        "aparecida",
        # mes
        "janeiro",
        "fevereiro",
        "marco",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
        # estado civil
        "casado",
        "casada",
        "solteiro",
        "solteira",
        "viuvo",
        "viuva",
        "divorciado",
        "divorciada",
        "separado",
        "separada",
        # tese / peca / orgao
        "inversa",
        "espolio",
        "constricao",
        "recuperacao",
        "judicial",
        "falencia",
        "prescricao",
        "material",
        "contagem",
        "postura",
        "fantasia",
        "empresa",
        "individual",
        "antiga",
        "supremo",
        "corte",
        "correio",
        "carteiro",
        "parcela",
        "natural",
        "controlador",
        "excelencia",
        "presidente",
        "ordinario",
        "promova",
        # giro societario / objeto que vaza da razao
        "sistemas",
        "sistema",
        "imoveis",
        "imovel",
        "trabalho",
        "apoio",
        "madeira",
        "maneira",
        "veiculos",
        "veiculo",
        "maquinas",
        "maquina",
        "cidade",
        "transporte",
        "coletivo",
        "administradora",
        "administrador",
        "consultoria",
        "assessoria",
        "empresarial",
        "diagnostico",
        "equipamentos",
        "digitais",
        "ambiental",
        "ambientais",
        "informatica",
        "laboratorio",
        "quatro",
        "quadro",
        "index",
        "agencia",
        "anonima",
        "estao",
        "locacao",
        "medicina",
        "saude",
        "treinamento",
        "treinamentos",
        "imagem",
        "emissao",
        "organizacao",
    }
)
_PAREN_LABELS = re.compile(
    r"(?i)(?:nome\s+fantasia\s*:|empresa\s+individual|(?:/\s*)?\bantiga\b)"
)
_LEGAL_STATUS_TAIL = re.compile(
    r"(?i)(?:[\s,.\-–—]+em\s+recupera[cç][aã]o\s+judicial)+\s*$"
)
_ESPOLIO_PREFIX = re.compile(r"(?i)^esp[oó]lio\s+de\s+")
_ACCENT = {
    "a": "aáàâãä",
    "e": "eéèêë",
    "i": "iíìîï",
    "o": "oóòôõö",
    "u": "uúùûü",
    "c": "cç",
    "n": "nñ",
    "s": "sšś",
}
_COMPANY_MARKERS = re.compile(
    r"(?is)\b(?:"
    r"sociedade|empresa|executada|devedora|pessoa\s+jur[ií]dica|"
    r"cnpj\s+d[ae]|marca|filial|matriz|raz[aã]o\s+social|firma|loja|grupo|"
    r"quadro\s+societ"
    r")\b"
)
_COMPANY_FOLLOW = re.compile(
    r"(?i)^\s*(?:com[eé]rcio|vidros|confec[cç][oõ]es|ltda)\b"
)
_PERSON_MARKERS = re.compile(
    r"(?is)\b(?:"
    r"sra?\.?|s[oó]cio|s[oó]cia|administrador(?:a)?|representante|cpf"
    r")\b"
)
_VIA_CUE = re.compile(
    r"(?ix)"
    r"(?:^|[\s,;:(])"
    r"(?:rua|r\.|r\s|avenida|av\.|ave\.?|avn\.|alameda|travessa|pra[cç]a|p[cç]\.|logradouro)"
    r"[\s\S]{0,80}$"
)


def is_blocked_particle(value: str) -> bool:
    """UF, cidade, mes, estado civil, tese ou giro — nunca como palavra unica."""

    compact = fold_lexical(value)
    if not compact or " " in compact:
        return False
    return compact in _PARTICLE_BLOCKLIST


def is_common_person_particle(value: str) -> bool:
    """Prenome/sobrenome banal: so casa isolado com apoio (vizinho ou rotulo)."""

    compact = fold_lexical(value)
    if not compact or " " in compact:
        return False
    return compact in _COMMON_PERSON_PARTICLES


def is_common_person_name_span(value: str) -> bool:
    """Nome com 2+ palavras em que nenhuma particula e distintiva.

    NER nao deve tokenizar 'Helena Lopes da Silva' no corpo se a lista e a
    capa nao ancoraram. Uma palavra distintiva (Cantagalo, Bernardelli) salva.
    """

    tokens = [
        item.strip(".,;:()[]")
        for item in re.split(r"\s+", value.strip())
        if item.strip(".,;:()[]")
    ]
    content = [item for item in tokens if fold_lexical(item) not in _CONNECTIVES]
    if len(content) < 2:
        return False
    for item in content:
        folded = fold_lexical(item)
        if len(folded) < _MIN_PARTICLE_CHARS:
            continue
        if is_common_person_particle(item) or is_blocked_particle(item):
            continue
        return False
    return True


def _particle_source(value: str, *, company: bool) -> str:
    """Texto do qual se extraem particulas: sem rotulo de parentese nem cauda falimentar."""

    stripped = value.replace("(", " ").replace(")", " ")
    stripped = _PAREN_LABELS.sub(" ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip(" -,.")
    if company:
        stripped = _LEGAL_STATUS_TAIL.sub("", stripped)
        stripped = _LEGAL_TAIL.sub("", stripped).strip(" -,.")
    else:
        stripped = _ESPOLIO_PREFIX.sub("", stripped).strip()
    return stripped


@dataclass(frozen=True, slots=True)
class _TargetEntry:
    original: str
    kind: str
    entity_type: str
    body_folded: str
    canonical_key: str
    phrases: tuple[str, ...]
    particles: tuple[str, ...]
    names: tuple[str, ...] = ()


class TargetListEngine:
    """Tokeniza os nomes e os CPF/CNPJ informados pelo usuario onde aparecam."""

    provider = "targets"

    def __init__(
        self,
        *,
        companies: Iterable[str] = (),
        people: Iterable[str] = (),
        ids: Iterable[str] = (),
        extra_keep: Iterable[str] = (),
        lookup_cnpj_names: bool = True,
        cnpj_index_path: Path | None = None,
    ) -> None:
        self.ids = parse_target_ids(ids)
        extra_companies = ()
        if lookup_cnpj_names and self.ids:
            extra_companies = names_from_listed_cnpjs(
                self.ids,
                extra_keep=extra_keep,
                index_path=cnpj_index_path,
            )
        self.companies = _clean((*companies, *extra_companies))
        self.people = _clean(people)
        self.extra_keep = tuple(extra_keep)
        self._entries = _build_entries(self.companies, self.people)

    def has_targets(self) -> bool:
        return bool(self.companies or self.people or self.ids)

    def detect(self, text: str) -> list[EntitySpan]:
        occupied: list[tuple[int, int]] = []
        spans: list[EntitySpan] = []
        for start, end, record in find_listed_id_spans(text, self.ids):
            occupied.append((start, end))
            spans.append(
                EntitySpan(
                    start=start,
                    end=end,
                    entity_type=record.entity_type,
                    score=0.96,
                    recognizer="ListaDeAlvos",
                    canonical_key=record.canonical,
                    alvo=record.formatted,
                )
            )
        if not self._entries:
            return resolve_overlaps(spans)
        complete_starts: dict[str, list[int]] = defaultdict(list)

        phrase_jobs: list[tuple[int, _TargetEntry, str]] = []
        skipped_keys: set[str] = set()
        for entry in self._entries:
            if is_kept_term(entry.original, self.extra_keep):
                skipped_keys.add(entry.canonical_key)
                continue
            for phrase in entry.phrases:
                if is_kept_term(phrase, self.extra_keep):
                    continue
                if is_blocked_particle(phrase):
                    continue
                phrase_jobs.append((len(phrase), entry, phrase))
        phrase_jobs.sort(key=lambda item: item[0], reverse=True)

        for _length, entry, phrase in phrase_jobs:
            # Nome completo listado (2+ palavras) e pedido explicito do operador:
            # "A PGFN requer a citacao do socio FULANO" nao o protege (ver
            # is_pfn_officer). Variante parcial continua cedendo ao procurador.
            listed_full_name = phrase in entry.names and len(phrase.split()) >= 2
            for start, end in _exact_and_fuzzy_spans(text, phrase):
                if entry.kind == "pessoa" and (
                    is_magistrate_or_citation(text, start, end)
                    or (not listed_full_name and is_pfn_officer(text, start, end))
                ):
                    continue
                if entry.kind == "empresa":
                    end = _extend_company_end(text, start, end, entry)
                complete_starts[entry.canonical_key].append(start)
                if any(start < other_end and end > other_start for other_start, other_end in occupied):
                    continue
                occupied.append((start, end))
                spans.append(
                    EntitySpan(
                        start=start,
                        end=end,
                        entity_type=entry.entity_type,
                        score=0.95,
                        recognizer="ListaDeAlvos",
                        canonical_key=entry.canonical_key,
                        alvo=entry.original,
                    )
                )

        for start, end, entry in _company_domain_spans(text, self._entries):
            if entry.canonical_key in skipped_keys:
                continue
            if any(start < other_end and end > other_start for other_start, other_end in occupied):
                continue
            complete_starts[entry.canonical_key].append(start)
            occupied.append((start, end))
            spans.append(
                EntitySpan(
                    start=start,
                    end=end,
                    entity_type="ORGANIZATION",
                    score=0.95,
                    recognizer="ListaDeAlvos",
                    canonical_key=entry.canonical_key,
                    alvo=entry.original,
                )
            )

        particle_owners: dict[str, list[_TargetEntry]] = defaultdict(list)
        particle_forms: dict[str, str] = {}
        for entry in self._entries:
            for particle in entry.particles:
                folded = fold_lexical(particle)
                if folded not in particle_forms:
                    particle_forms[folded] = particle
                if entry not in particle_owners[folded]:
                    particle_owners[folded].append(entry)

        particle_jobs = sorted(
            particle_forms.items(), key=lambda item: len(item[1]), reverse=True
        )
        for folded, form in particle_jobs:
            if is_kept_term(form, self.extra_keep) or is_blocked_particle(form):
                continue
            owners = [
                item
                for item in particle_owners[folded]
                if item.canonical_key not in skipped_keys
            ]
            if not owners:
                continue
            common = is_common_person_particle(form)
            hits = (
                _exact_spans(text, form)
                if common
                else _exact_and_fuzzy_spans(text, form)
            )
            for start, end in hits:
                if is_blocked_particle(text[start:end]):
                    continue
                if any(start < other_end and end > other_start for other_start, other_end in occupied):
                    continue
                corporate_follow = bool(_CORPORATE_FOLLOW.match(text[end:]))
                company_owners = [item for item in owners if item.kind == "empresa"]
                if corporate_follow and not company_owners:
                    continue
                if _particle_blocked(text, start, end):
                    continue
                if common and not _particle_has_support(
                    text,
                    start,
                    end,
                    owners,
                    spans,
                ):
                    continue
                entry, canonical_key, alvo = resolve_particle_alias(
                    text,
                    start,
                    end,
                    particle=form,
                    candidates=owners,
                    complete_starts=complete_starts,
                    corporate_follow=corporate_follow,
                )
                if entry is not None and entry.kind == "empresa":
                    end = _extend_company_end(text, start, end, entry)
                occupied.append((start, end))
                spans.append(
                    EntitySpan(
                        start=start,
                        end=end,
                        entity_type=entry.entity_type if entry is not None else _majority_type(owners),
                        score=0.95,
                        recognizer="ListaDeAlvos",
                        canonical_key=canonical_key,
                        alvo=alvo,
                    )
                )
        return resolve_overlaps(spans)

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []


def resolve_particle_alias(
    text: str,
    start: int,
    end: int,
    *,
    particle: str,
    candidates: Sequence[_TargetEntry],
    complete_starts: dict[str, list[int]],
    corporate_follow: bool = False,
) -> tuple[_TargetEntry | None, str, str]:
    """Resolve partícula ambígua para a entrada da lista que a originou.

    Ordem determinística (Ciclo 1):

    1. Partícula única (uma só entrada contém o termo) → essa entrada.
    2. Contexto imediato na mesma linha, até ~80 caracteres antes/depois:
       marcadores de empresa (``sociedade``, ``empresa``, ``executada``,
       ``devedora``, ``pessoa jurídica``, ``CNPJ d[ae]``, ``marca``, ``filial``,
       ``matriz``, ``razão social``, ``firma``, ``loja``, ``grupo``,
       ``quadro societário``, ou seguida de ``COMÉRCIO``/``VIDROS``/
       ``CONFECÇÕES``/``LTDA``) → a entrada de empresa candidata (se só uma
       empresa contém a partícula; se várias, a de menção completa mais
       próxima antes no texto; se nenhuma antes, a mais próxima depois).
       Marcadores de pessoa (``Sr.``, ``Sra.``, ``sócio``, ``sócia``,
       ``administrador``, ``administradora``, ``representante``, ``CPF``) →
       a pessoa candidata pela mesma regra de proximidade.
    3. Sem marcador: a candidata cuja menção COMPLETA mais próxima ANTES no
       texto; se nenhuma antes, a mais próxima depois.
    4. Sem nenhuma menção completa no documento: token dedicado por partícula,
       tipado pela maioria das candidatas (empate → PERSON, como a busca antiga).
    """

    if len(candidates) == 1:
        entry = candidates[0]
        return entry, entry.canonical_key, entry.original

    kind = _context_kind(text, start, end, corporate_follow=corporate_follow)
    pool: Sequence[_TargetEntry] = candidates
    if kind == "empresa":
        companies = [item for item in candidates if item.kind == "empresa"]
        if len(companies) == 1:
            entry = companies[0]
            return entry, entry.canonical_key, entry.original
        if companies:
            pool = companies
    elif kind == "pessoa":
        people = [item for item in candidates if item.kind == "pessoa"]
        if len(people) == 1:
            entry = people[0]
            return entry, entry.canonical_key, entry.original
        if people:
            pool = people

    nearest = _nearest_complete(start, pool, complete_starts)
    if nearest is not None:
        return nearest, nearest.canonical_key, nearest.original

    dedicated = f"alvo:particula:{fold_lexical(particle)}"
    return None, dedicated, particle


def phrase_pattern(value: str) -> re.Pattern[str]:
    """Casa o nome com acento opcional e qualquer espaco/quebra entre palavras."""

    words = [item for item in value.split() if item]
    if not words:
        return re.compile(r"(?!x)x")
    body = r"\s+".join(_word_pattern(word) for word in words)
    return re.compile(rf"(?<![0-9A-Za-zÀ-ÿĀ-ž])(?:{body})(?![0-9A-Za-zÀ-ÿĀ-ž])")


def _context_kind(
    text: str, start: int, end: int, *, corporate_follow: bool = False
) -> str | None:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    before = text[max(line_start, start - 80) : start]
    after = text[end : min(line_end, end + 80)]
    if corporate_follow or _COMPANY_FOLLOW.match(after):
        return "empresa"
    if _COMPANY_MARKERS.search(before) or _COMPANY_MARKERS.search(after):
        return "empresa"
    if _PERSON_MARKERS.search(before) or _PERSON_MARKERS.search(after):
        return "pessoa"
    return None


def _nearest_complete(
    position: int,
    candidates: Sequence[_TargetEntry],
    complete_starts: dict[str, list[int]],
) -> _TargetEntry | None:
    best: _TargetEntry | None = None
    best_pos = -1
    for entry in candidates:
        befores = [item for item in complete_starts.get(entry.canonical_key, ()) if item < position]
        if befores:
            last = max(befores)
            if last > best_pos:
                best_pos = last
                best = entry
    if best is not None:
        return best
    best_pos = 10**18
    for entry in candidates:
        afters = [item for item in complete_starts.get(entry.canonical_key, ()) if item > position]
        if afters:
            first = min(afters)
            if first < best_pos:
                best_pos = first
                best = entry
    return best


def _majority_type(candidates: Sequence[_TargetEntry]) -> str:
    companies = sum(1 for item in candidates if item.kind == "empresa")
    people = sum(1 for item in candidates if item.kind == "pessoa")
    if companies > people:
        return "ORGANIZATION"
    return "PERSON"


def _build_entries(companies: Sequence[str], people: Sequence[str]) -> tuple[_TargetEntry, ...]:
    entries: list[_TargetEntry] = []
    company_groups: dict[str, list[str]] = {}
    company_order: list[str] = []
    for value in companies:
        body = fold_lexical(_LEGAL_TAIL.sub("", value).strip(" -,."))
        if not body:
            continue
        if body not in company_groups:
            company_groups[body] = []
            company_order.append(body)
        if value not in company_groups[body]:
            company_groups[body].append(value)
    for body in company_order:
        originals = tuple(company_groups[body])
        phrases = _company_search_terms(originals)
        entries.append(
            _TargetEntry(
                original=originals[0],
                kind="empresa",
                entity_type="ORGANIZATION",
                body_folded=body,
                canonical_key=f"alvo:empresa:{body}",
                phrases=phrases,
                particles=(),
            )
        )

    person_groups: dict[str, list[str]] = {}
    person_order: list[str] = []
    for value in people:
        body = fold_lexical(value)
        if not body:
            continue
        aliased = next(
            (
                existing
                for existing in person_order
                if _same_case_person(person_groups[existing][0], value)
            ),
            None,
        )
        if aliased is not None:
            if value not in person_groups[aliased]:
                person_groups[aliased].append(value)
            continue
        if body not in person_groups:
            person_groups[body] = []
            person_order.append(body)
        if value not in person_groups[body]:
            person_groups[body].append(value)
    for body in person_order:
        originals = tuple(person_groups[body])
        particles: list[str] = []
        seen = set()
        for original in originals:
            for part in _entry_particles(original, company=False):
                key = fold_lexical(part)
                if key in seen:
                    continue
                seen.add(key)
                particles.append(part)
        phrases = tuple(
            dict.fromkeys(
                (
                    *originals,
                    *(item for original in originals for item in _glued_pairs(original)),
                    *(item for original in originals for item in _leading_person_phrases(original)),
                    *(item for original in originals for item in _trailing_person_phrases(original)),
                )
            )
        )
        entries.append(
            _TargetEntry(
                original=originals[0],
                kind="pessoa",
                entity_type="PERSON",
                body_folded=body,
                canonical_key=f"alvo:pessoa:{body}",
                phrases=phrases,
                particles=tuple(particles),
                names=originals,
            )
        )
    return tuple(entries)


def _entry_particles(value: str, *, company: bool) -> tuple[str, ...]:
    if company:
        return ()
    skip = {fold_lexical(item) for item in _PARTICLE_SKIP}
    skip |= _PARTICLE_BLOCKLIST
    parts = [item for item in _particle_source(value, company=company).split() if item]
    if not company and len(parts) < 2:
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for part in parts:
        compact = part.strip(".,;:")
        if len(compact) < _MIN_PARTICLE_CHARS:
            continue
        folded = fold_lexical(compact)
        if folded in skip or folded in seen:
            continue
        if is_kept_term(compact):
            continue
        seen.add(folded)
        found.append(compact)
    return tuple(found)


def _exact_spans(text: str, value: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in phrase_pattern(value).finditer(text)]


def _exact_and_fuzzy_spans(text: str, value: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for match in phrase_pattern(value).finditer(text):
        key = (match.start(), match.end())
        seen.add(key)
        found.append(key)
    per_word = "&" in value
    for start, end in _fuzzy_phrase_spans(text, value, per_word=per_word):
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def _fuzzy_phrase_spans(
    text: str, value: str, *, per_word: bool = False
) -> list[tuple[int, int]]:
    """Uma letra a mais, a menos ou trocada, so em palavra com 6+ letras.

    Cauda com ``&`` (VIDROS & ESQUADRIAS) admite 1 edicao por palavra longa,
    para OCR do tipo VIDR0S & ESQUADRLAS.
    """

    targets = [
        _fold_word(item)
        for item in value.split()
        if item and any(char.isalnum() for char in item)
    ]
    if not targets:
        return []
    starts, ends, words = _folded_tokens(text)
    size = len(targets)
    if size == 0 or len(words) < size:
        return []
    hits: list[tuple[int, int]] = []
    for index in range(len(words) - size + 1):
        edits = 0
        aligned = True
        for offset, target in enumerate(targets):
            folded = words[index + offset]
            distance = _edit_distance(folded, target)
            if distance > 1:
                aligned = False
                break
            if distance and len(target) < _MIN_FUZZY_CHARS:
                aligned = False
                break
            edits += distance
            if not per_word and edits > 1:
                aligned = False
                break
        if not aligned or edits == 0:
            continue
        if per_word or edits == 1:
            hits.append((starts[index], ends[index + size - 1]))
    return hits


@lru_cache(maxsize=2)
def _folded_tokens(text: str) -> tuple[tuple[int, ...], tuple[int, ...], tuple[str, ...]]:
    """Palavras do texto ja dobradas, uma vez por texto (nao uma vez por alvo).

    Refazer a dobra de cada palavra para cada alvo custava minutos por mil
    folhas com uma lista de alvos de cem nomes.
    """

    starts: list[int] = []
    ends: list[int] = []
    words: list[str] = []
    for match in _TOKEN.finditer(text):
        starts.append(match.start())
        ends.append(match.end())
        words.append(_fold_word(match.group()))
    return tuple(starts), tuple(ends), tuple(words)


def _fold_word(value: str) -> str:
    return "".join(_fold_char(char) for char in value)


def _edit_distance(left: str, right: str) -> int:
    """0, 1 ou 2 (2 = 'mais de um'). So substituiçao, inserçao ou exclusao unica."""

    if left == right:
        return 0
    shorter, longer = left, right
    if len(shorter) > len(longer):
        shorter, longer = longer, shorter
    if len(longer) - len(shorter) > 1:
        return 2
    if len(shorter) == len(longer):
        diff = 0
        for one, two in zip(shorter, longer):
            if one != two:
                diff += 1
                if diff > 1:
                    return 2
        return diff
    skipped = False
    index = 0
    for char in longer:
        if index < len(shorter) and shorter[index] == char:
            index += 1
            continue
        if skipped:
            return 2
        skipped = True
    return 1


def _word_pattern(word: str) -> str:
    return "".join(_char_class(char) for char in word)


def _char_class(char: str) -> str:
    folded = _fold_char(char)
    if char in {"'", "’", "`", "´", "ʼ", "′"}:
        return r"['’`´ʼ′]?"
    variants = _ACCENT.get(folded)
    if variants:
        return "[" + variants + variants.upper() + "]"
    if char.isalnum():
        lower, upper = char.lower(), char.upper()
        if lower != upper:
            return f"[{re.escape(lower)}{re.escape(upper)}]"
        return re.escape(char)
    return re.escape(char)


@lru_cache(maxsize=4096)
def _fold_char(char: str) -> str:
    decomposed = unicodedata.normalize("NFKD", char)
    stripped = "".join(item for item in decomposed if not unicodedata.combining(item))
    return stripped.casefold() or char.casefold()


def _clean(values: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        value = " ".join(str(item).split()).strip(" -,;:|")
        if len(value) < _MIN_TARGET_CHARS:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return tuple(cleaned)


def _company_search_terms(companies: Iterable[str]) -> tuple[str, ...]:
    """Tambem busca a razao sem sufixo societario (LTDA, ME, EIRELI, ETDA, LIDA).

    Prefixo de 2 e 3 palavras de conteudo (BERNARDELLI VIDROS, TRAMONTANA COMÉRCIO DE
    VIDROS) e cauda distintiva com ``&`` (VIDROS & ESQUADRIAS) so entram se a
    entrada da lista as contiver — nao ha busca de VIDROS generico.
    """

    terms: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        value = candidate.strip(" -,.")
        if len(value) < 3:
            return
        key = value.casefold()
        if key in seen:
            return
        seen.add(key)
        terms.append(value)

    for value in companies:
        add(value)
        stripped = _LEGAL_TAIL.sub("", value).strip(" -,.")
        if len(stripped) >= 5:
            add(stripped)
        for count in (2, 3):
            prefix = _company_prefix(stripped, min_content=count)
            if prefix:
                add(prefix)
        remainder = _distinctive_ampersand_tail(value)
        if remainder:
            add(remainder)
            mark = remainder.find("&")
            if mark >= 0:
                add(remainder[mark:])
    return tuple(terms)


def _company_prefix(stripped: str, min_content: int = 3) -> str | None:
    words = [item for item in stripped.split() if item]
    taken: list[str] = []
    content = 0
    for word in words:
        taken.append(word)
        if fold_lexical(word) not in _CONNECTIVES:
            content += 1
        if content >= min_content:
            break
    if content < min_content:
        return None
    prefix = " ".join(taken)
    if prefix.casefold() == stripped.casefold():
        return None
    return prefix


def _distinctive_ampersand_tail(value: str) -> str | None:
    """So quando a entrada contem '& ESQUADRIAS' (ou outra cauda com &)."""

    if "&" not in value:
        return None
    words = [item for item in value.split() if item]
    if len(words) < 2:
        return None
    rest = " ".join(words[1:])
    return rest if "&" in rest else None


def _glued_pairs(value: str) -> tuple[str, ...]:
    """Concatenacao sem espaco de pares consecutivos (MATEUSTRAMONTANA, JUILIANADE)."""

    parts = [item.strip(".,;:") for item in value.split() if item.strip(".,;:")]
    found: list[str] = []
    connectives = {fold_lexical(item) for item in ("de", "da", "do", "dos", "das")}
    for left, right in zip(parts, parts[1:]):
        letters_left = sum(char.isalpha() for char in left)
        letters_right = sum(char.isalpha() for char in right)
        if letters_left + letters_right >= 10:
            found.append(left + right)
        elif letters_left >= 6 and fold_lexical(right) in connectives:
            found.append(left + right)
    return tuple(found)


def _same_case_person(left: str, right: str) -> bool:
    """Agrupa grafia canonica e alias OCR da mesma pessoa na lista do caso."""

    skip = {fold_lexical(item) for item in _PARTICLE_SKIP}
    first = [fold_lexical(word) for word in left.split() if fold_lexical(word) not in skip]
    second = [fold_lexical(word) for word in right.split() if fold_lexical(word) not in skip]
    if not first or not second:
        return False
    if len(first) == len(second) and all(
        one == two or (_edit_distance(one, two) <= 1 and min(len(one), len(two)) >= 5)
        for one, two in zip(first, second)
    ):
        return True
    if len(first) >= 2 and len(second) >= 2 and first[0] == second[0] and first[-1] == second[-1]:
        return True
    if len(first) >= 2 and len(second) >= 2:
        first_ok = first[0] == second[0] or (
            _edit_distance(first[0], second[0]) <= 1 and min(len(first[0]), len(second[0])) >= 5
        )
        last_a, last_b = first[-1], second[-1]
        last_ok = last_a == last_b or (
            min(len(last_a), len(last_b)) >= 6
            and (last_a.endswith(last_b) or last_b.endswith(last_a))
        )
        if first_ok and last_ok:
            return True
    return False


def _word_fits(token: str, target: str) -> bool:
    if token == target:
        return True
    distance = _edit_distance(token, target)
    return distance == 1 and len(target) >= _MIN_FUZZY_CHARS


def _remaining_body_words(matched_text: str, body_folded: str) -> list[str]:
    remaining = body_folded.split()
    for token in fold_lexical(matched_text).split():
        if not remaining:
            break
        index = next(
            (pos for pos, word in enumerate(remaining) if _word_fits(token, word) or token == word),
            None,
        )
        if index is None:
            continue
        remaining = remaining[index + 1 :]
    return remaining


def _extend_company_end(text: str, start: int, end: int, entry: _TargetEntry) -> int:
    """Estende o span ate o resto da razao e a cauda juridica/OCR (ETDA, LIDA, –ME)."""

    remaining = _remaining_body_words(text[start:end], entry.body_folded)
    allow_esquadrias = any("&" in item for item in (entry.original, *entry.phrases))
    cursor = end
    while cursor < len(text):
        match = _NEXT_TOKEN.match(text[cursor:])
        if match is None:
            break
        token = fold_lexical(match.group(2).rstrip(".,;:"))
        ampersand = bool(match.group(1))
        if token in _LEGAL_TOKENS or (token == "esquadrias" and (ampersand or allow_esquadrias)):
            cursor += match.end()
            continue
        if remaining and _word_fits(token, remaining[0]):
            remaining.pop(0)
            cursor += match.end()
            continue
        break
    return cursor


def _leading_person_phrases(value: str) -> tuple[str, ...]:
    """Primeiro par do nome (VERA LUCIA, SUTILOGU CICO) e JULIANA DE CASSIA."""

    words = [item.strip(".,;:") for item in value.split() if item.strip(".,;:")]
    if len(words) < 3:
        return ()
    if len(words[1]) >= 3:
        return (f"{words[0]} {words[1]}",)
    if fold_lexical(words[1]) in _CONNECTIVES:
        return (f"{words[0]} {words[1]} {words[2]}",)
    return ()


def _trailing_person_phrases(value: str) -> tuple[str, ...]:
    """Sobrenomes colados (Lopes da Silva). Nao gera 'da Silva' sozinho."""

    words = [item.strip(".,;:") for item in value.split() if item.strip(".,;:")]
    if len(words) < 3:
        return ()
    rest = words[1:]
    significant = [item for item in rest if fold_lexical(item) not in _CONNECTIVES]
    if len(significant) < 2:
        return ()
    return (" ".join(rest),)


def _particle_has_support(
    text: str,
    start: int,
    end: int,
    owners: Sequence[_TargetEntry],
    spans: Sequence[EntitySpan],
) -> bool:
    """Sobrenome banal so cola com rotulo de parte ou cola onomastica."""

    if _ROLE_CUE.search(text[max(0, start - 48) : start]):
        return True
    self_fold = fold_lexical(text[start:end])
    for entry in owners:
        for other in entry.particles:
            if fold_lexical(other) == self_fold:
                continue
            if _name_adjacent_to_phrase(text, start, end, other):
                return True
    owner_keys = {item.canonical_key for item in owners}
    for span in spans:
        if span.canonical_key not in owner_keys:
            continue
        if span.end <= start and _NAME_GLUE.fullmatch(text[span.end : start]):
            return True
        if end <= span.start and _NAME_GLUE.fullmatch(text[end : span.start]):
            return True
    return False


def _name_adjacent_to_phrase(text: str, start: int, end: int, other: str) -> bool:
    """Outra particula do mesmo alvo imediatamente ao lado (DE/DA/DO ou espaco)."""

    try:
        pattern = phrase_pattern(other)
    except re.error:
        return False
    left = text[max(0, start - 48) : start]
    for match in pattern.finditer(left):
        if _NAME_GLUE.fullmatch(left[match.end() :]):
            return True
    right = text[end : min(len(text), end + 48)]
    for match in pattern.finditer(right):
        if _NAME_GLUE.fullmatch(right[: match.start()]):
            return True
    return False


def _particle_blocked(text: str, start: int, end: int) -> bool:
    """Particula nao come logradouro nem magistrado/PFN (fidelidade)."""

    window = text[max(0, start - 100) : start]
    if _VIA_CUE.search(window):
        return True
    return is_magistrate_or_citation(text, start, end) or is_pfn_officer(text, start, end)


_DOMAIN_TLD = r"(?:com\.br|combr|com|net|org|co\.or|co|or)"


def _significant_company_words(value: str) -> tuple[str, ...]:
    stripped = _LEGAL_TAIL.sub("", value).strip(" -,.")
    words: list[str] = []
    for part in stripped.replace("&", " ").split():
        compact = part.strip(".,;:")
        if not compact:
            continue
        folded = fold_lexical(compact)
        if folded in _CONNECTIVES or folded in _LEGAL_TOKENS:
            continue
        words.append(folded)
    return tuple(words)


def _domain_stems(words: Sequence[str]) -> tuple[str, ...]:
    if not words:
        return ()
    stems: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        if len(item) < 8 or item in seen:
            return
        seen.add(item)
        stems.append(item)

    if len(words) >= 2:
        add(words[0] + words[1])
        for extra in words[2:]:
            add(words[0] + extra)
    add("".join(words))
    return tuple(stems)


def _ocr_glued_domain_pattern(brand: str, products: Sequence[str]) -> re.Pattern[str] | None:
    if len(brand) < 5:
        return None
    if not any("vidr" in item or "dros" in item for item in products):
        return None
    return re.compile(
        rf"(?i)(?:[a-z0-9]{{0,16}}){re.escape(brand)}(?:vidr|dros|vdros)[a-z]{{0,8}}"
        rf"\.{_DOMAIN_TLD}\b"
    )


def _company_domain_spans(
    text: str, entries: Sequence[_TargetEntry]
) -> list[tuple[int, int, _TargetEntry]]:
    """Dominio e marca colada da empresa alvo (bernardellividros.com.br, OCR .co.or)."""

    owned: dict[str, _TargetEntry] = {}
    ocr_jobs: list[tuple[re.Pattern[str], _TargetEntry]] = []
    seen_ocr: set[str] = set()
    for entry in entries:
        if entry.kind != "empresa":
            continue
        words = _significant_company_words(entry.original)
        for stem in _domain_stems(words):
            owned.setdefault(stem, entry)
        if words:
            brand = words[0]
            if brand not in seen_ocr:
                pattern = _ocr_glued_domain_pattern(brand, words[1:])
                if pattern is not None:
                    seen_ocr.add(brand)
                    ocr_jobs.append((pattern, entry))
    hits: list[tuple[int, int, _TargetEntry]] = []
    seen_span: set[tuple[int, int]] = set()
    for stem, entry in owned.items():
        pattern = re.compile(
            rf"(?i)\b{re.escape(stem)}(?:vidros|dros|vdros)?\.{_DOMAIN_TLD}\b"
        )
        for match in pattern.finditer(text):
            key = (match.start(), match.end())
            if key in seen_span:
                continue
            seen_span.add(key)
            hits.append((match.start(), match.end(), entry))
    for pattern, entry in ocr_jobs:
        for match in pattern.finditer(text):
            key = (match.start(), match.end())
            if key in seen_span:
                continue
            seen_span.add(key)
            hits.append((match.start(), match.end(), entry))
    hits.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    return hits


def attach_list_canonicals(
    text: str,
    spans: Sequence[EntitySpan],
    engine: TargetListEngine,
) -> list[EntitySpan]:
    """Funde PERSON/ORG de NER com a entrada da lista (mesmo canonical_key / alvo)."""

    lookup: dict[str, _TargetEntry] = {}
    for entry in engine._entries:
        lookup[entry.body_folded] = entry
        lookup[fold_lexical(entry.original)] = entry
        for phrase in entry.phrases:
            lookup[fold_lexical(phrase)] = entry
    bound: list[EntitySpan] = []
    for span in spans:
        if span.canonical_key or span.entity_type not in {"PERSON", "ORGANIZATION"}:
            bound.append(span)
            continue
        entry = lookup.get(fold_lexical(span.value_from(text)))
        if entry is None or entry.entity_type != span.entity_type:
            bound.append(span)
            continue
        bound.append(
            replace(span, canonical_key=entry.canonical_key, alvo=entry.original)
        )
    return bound


def _person_particles(people: Iterable[str]) -> tuple[str, ...]:
    """Prenome e sobrenome de um alvo ja informado — nunca termo solto inventado."""

    particles: list[str] = []
    seen: set[str] = set()
    for value in people:
        for part in _entry_particles(value, company=False):
            key = part.casefold()
            if key in seen:
                continue
            seen.add(key)
            particles.append(part)
    return tuple(particles)

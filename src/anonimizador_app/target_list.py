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
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from anonimizador_juridico import EntitySpan, resolve_overlaps
from anonimizador_juridico.pseudonymizer import fold_lexical

from .case_list import CaseRules, parse_alias_lines
from .fidelity import is_magistrate_or_citation, is_pfn_officer
from .gemini_detector import is_kept_term, is_system_term
from .geo_names import is_geo_name
from .name_generation import conflicting_generation, generation_of, generation_tail, strip_generation, suffix_label
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
        # H9-i lote B1: prenome/sobrenome que tambem e palavra comum (a curta nem vira particula; vale para a
        # grafia adicional de uma palavra so).
        "rosa",
        "paz",
        "vitoria",
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
    # H9-i lote B1: grafias adicionais (apelido, nome fantasia, razao antiga) da mesma entrada.
    aliases: tuple[str, ...] = ()
    # H9-n: grafias adicionais de uma palavra que so casam com apoio (``alias_needs_support``), como particula.
    guarded: tuple[str, ...] = ()


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
        aliases: Iterable[str] = (),
        processes: Iterable[str] = (),
        cdas: Iterable[str] = (),
        contacts: Iterable[str] = (),
    ) -> None:
        self.ids = parse_target_ids(ids)
        extra_companies = ()
        if lookup_cnpj_names and self.ids:
            extra_companies = names_from_listed_cnpjs(
                self.ids,
                extra_keep=extra_keep,
                index_path=cnpj_index_path,
            )
        companies = list(companies)
        people = list(people)
        # H9-i lote B1: grafias adicionais ("nome principal = grafia1; grafia2") vao para a MESMA entrada.
        alias_map = _place_aliases(parse_alias_lines(aliases), companies, people)
        self.companies = _clean((*companies, *extra_companies))
        self.people = _clean(people)
        self.extra_keep = tuple(extra_keep)
        self._extra_folded = frozenset(fold_lexical(item) for item in self.extra_keep if str(item).strip())
        self._entries = _build_entries(self.companies, self.people, alias_map)
        self.rules = CaseRules(processes=processes, cdas=cdas, contacts=contacts)
        self._index = _MentionIndex(self._entries)
        self._extra_terms = terms_by_word(self.extra_keep)

    def _public_agent(self, text: str, start: int, end: int) -> bool:
        """H9-o: pedaco ou grafia de pessoa do caso que cai no nome de um julgador ou dentro de um termo protegido de
        2+ palavras (lista fixa e "Termos protegidos"): fica em claro."""

        return in_public_agent_name(text, start, end) or inside_protected_term(text, start, end, self._extra_terms)

    def has_targets(self) -> bool:
        return bool(self.companies or self.people or self.ids or self.rules)

    def _kept(self, value: str) -> bool:
        """Termo protegido que a lista do caso NAO vence: a lista de exclusoes do usuario e os termos fixos (Uniao,
        PGFN...). O sistema da lista protegida (``sistemas_protegidos.txt``) cede ao nome listado (H9-i)."""

        if not is_kept_term(value, self.extra_keep):
            return False
        return not (is_system_term(value) and fold_lexical(value) not in self._extra_folded)

    @property
    def entries(self) -> tuple["_TargetEntry", ...]:
        return self._entries

    def resolve_mention(
        self,
        value: str,
        *,
        text: str = "",
        start: int = -1,
        end: int = -1,
    ) -> "_TargetEntry | None":
        """A entrada da lista a que uma mencao (do LLM ou do motor local) pertence, ou ``None``.

        Casa o nome, as grafias adicionais, a razao sem sufixo, os prefixos (forma curta), o nome fantasia e as
        frases do nome (Lopes da Silva). Particula de uma palavra: a distintiva casa sozinha; a comum (Silva,
        Campos, Rosa) so com apoio no texto (rotulo de parte ou outra palavra do mesmo nome colada), e so com
        posicao. Particula de duas entradas: ``resolve_particle_alias``. Varias palavras soltas do mesmo nome
        ("Gioconda Pereira"): todas do mesmo alvo e uma distintiva."""

        return self._index.resolve(
            value, text=text, start=start, end=end, kept=self._kept
        )

    def detect(self, text: str) -> list[EntitySpan]:
        occupied: list[tuple[int, int]] = []
        spans: list[EntitySpan] = []
        for span in self.rules.detect(text):
            occupied.append((span.start, span.end))
            spans.append(span)
        for start, end, record in find_listed_id_spans(text, self.ids):
            if any(start < other_end and end > other_start for other_start, other_end in occupied):
                continue
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
            if self._kept(entry.original):
                skipped_keys.add(entry.canonical_key)
                continue
            for phrase in entry.phrases:
                if self._kept(phrase):
                    continue
                if is_blocked_particle(phrase):
                    continue
                phrase_jobs.append((len(phrase), entry, phrase))
        phrase_jobs.sort(key=lambda item: item[0], reverse=True)
        occupied.extend(self._generation_blocks(text, skipped_keys))

        for _length, entry, phrase in phrase_jobs:
            # Nome completo listado (2+ palavras) e pedido explicito do operador:
            # "A PGFN requer a citacao do socio FULANO" nao o protege (ver
            # is_pfn_officer). Variante parcial continua cedendo ao procurador.
            listed_full_name = phrase in entry.names and len(phrase.split()) >= 2
            # H9-n: grafia de uma palavra que e prenome/sobrenome comum casa sozinha, mas so exata (sem o OCR de 1
            # letra: Marcia nao come Marcio).
            exact_only = len(phrase.split()) == 1 and is_common_person_particle(phrase)
            hits = _exact_spans(text, phrase) if exact_only else _exact_and_fuzzy_spans(text, phrase)
            # H9-o: so o nome completo listado (nao a grafia nem a frase parcial) passa pelo nome de julgador.
            original_full = listed_full_name and phrase not in entry.aliases
            for start, end in hits:
                if entry.kind == "pessoa" and (
                    is_magistrate_or_citation(text, start, end)
                    or (not listed_full_name and is_pfn_officer(text, start, end))
                    or (not original_full and self._public_agent(text, start, end))
                ):
                    continue
                if entry.kind == "pessoa" and not generation_of(entry.original):
                    tail = generation_tail(text, end)
                    if tail is not None:
                        # v0.4.10: "Fulano" listado seguido de "Filho"/"Neto" e OUTRA pessoa (o filho, o neto).
                        son = self._generation_span(start, tail, entry)
                        if not any(start < other_end and son.end > other_start for other_start, other_end in occupied):
                            occupied.append((start, son.end))
                            spans.append(son)
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

        self._ocr_tolerant(text, spans, occupied, skipped_keys, complete_starts)
        for start, end, entry in _acronym_spans(text, self._entries, occupied):
            if entry.canonical_key in skipped_keys:
                continue
            occupied.append((start, end))
            spans.append(
                EntitySpan(
                    start=start, end=end, entity_type="ORGANIZATION", score=0.95, recognizer="ListaDeAlvos",
                    canonical_key=entry.canonical_key, alvo=entry.original,
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
        shared: list[tuple[int, int, tuple[_TargetEntry, ...]]] = []
        for folded, form in particle_jobs:
            if self._kept(form) or is_blocked_particle(form):
                continue
            owners = [
                item
                for item in particle_owners[folded]
                if item.canonical_key not in skipped_keys
            ]
            if not owners:
                continue
            common = is_common_person_particle(form) or any(
                folded in {fold_lexical(item) for item in owner.guarded} for owner in owners
            )
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
                if not company_owners and generation_tail(text, end) is not None:
                    continue  # v0.4.10: "Silva Filho" nao e o listado Silva
                if not company_owners and (
                    text[start:end].islower() or _inside_other_name(text, start, end, owners)
                ):
                    # v0.4.10: prenome/sobrenome solto da lista em minuscula, ou dentro do nome inteiro de OUTRA pessoa
                    # (servidor na linha de tabela, assinatura com o cargo depois): nao e o listado.
                    continue
                if not company_owners and self._public_agent(text, start, end):
                    continue  # H9-o: prenome/sobrenome dentro do nome do relator ou de termo protegido
                if common and not _particle_has_support(
                    text,
                    start,
                    end,
                    owners,
                    spans,
                ):
                    continue
                people_owners = [item for item in owners if item.kind == "pessoa"]
                if len(people_owners) >= 2 and (
                    not company_owners
                    or _context_kind(text, start, end, corporate_follow=corporate_follow) != "empresa"
                ):
                    # v0.4.14: sobrenome de 2+ pessoas da lista (a familia): nunca vai para a pessoa da mencao completa
                    # mais proxima. Decide depois, pelos pedacos colados (``_shared_surname_spans``).
                    occupied.append((start, end))
                    shared.append((start, end, tuple(people_owners)))
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
        if shared:
            spans.extend(_shared_surname_spans(text, shared, spans))
        return resolve_overlaps(_join_listed_pieces(text, spans))

    def _ocr_tolerant(
        self,
        text: str,
        spans: list[EntitySpan],
        occupied: list[tuple[int, int]],
        skipped_keys: set[str],
        complete_starts: dict[str, list[int]],
    ) -> None:
        """v0.4.12 (chave ``ocr_fuzzy_names``): o nome da lista deturpado pelo OCR, so nas folhas lidas por OCR, com a
        chave do nome certo; grafia perto de 2+ nomes nao casa (``ocr_fuzzy``). Tudo vai para o AVISOS.md."""

        from . import features, ocr_fuzzy, uncertainty

        if not features.enabled("ocr_fuzzy_names") or "<!-- ocr:" not in text:
            return
        entries = [entry for entry in self._entries if entry.canonical_key not in skipped_keys]
        by_key = {entry.canonical_key: entry for entry in entries}
        hits, refusals = ocr_fuzzy.find(text, ocr_fuzzy.targets_from(entries))
        for refusal in refusals:
            # Nenhum dos nomes candidatos fica com o trecho: nem o pedaco exato comum aos dois ("Fulana de Tal" de duas
            # irmas "Fulana de Tal Souza" e "Fulana de Tal Sousa"). Na duvida, em claro (o AVISOS.md diz a folha).
            keys = {target.key for target in refusal.targets}
            for span in [s for s in spans if s.canonical_key in keys and refusal.start <= s.start and s.end <= refusal.end]:
                spans.remove(span)
                if (span.start, span.end) in occupied:
                    occupied.remove((span.start, span.end))
            occupied.append((refusal.start, refusal.end))  # nem prenome nem sobrenome solto da lista ali
            uncertainty.record(
                text,
                uncertainty.Pending(
                    refusal.start, refusal.end, uncertainty.AMBIGUOUS,
                    candidates=tuple((target.entity_type, target.key) for target in refusal.targets),
                ),
            )
        for hit in hits:
            entry = by_key.get(hit.target.key)
            if entry is None:
                continue
            start, end = hit.start, hit.end
            if entry.kind == "pessoa" and (
                is_magistrate_or_citation(text, start, end) or self._public_agent(text, start, end)
            ):
                continue
            if entry.kind == "pessoa" and not generation_of(entry.original) and generation_tail(text, end) is not None:
                continue  # "Fulano Filho" deturpado e outra pessoa: fica para o resto do motor
            inside = [span for span in spans if span.start < end and start < span.end]
            if any((span.start, span.end) == (start, end) for span in inside):
                continue  # a regra exata ja pegou este trecho
            if inside and not all(
                span.canonical_key == entry.canonical_key and start <= span.start and span.end <= end for span in inside
            ):
                if any(span.canonical_key != entry.canonical_key for span in inside):
                    uncertainty.record(
                        text,
                        uncertainty.Pending(
                            start, end, uncertainty.AMBIGUOUS,
                            candidates=((entry.entity_type, entry.canonical_key),)
                            + tuple((span.entity_type, span.canonical_key) for span in inside),
                        ),
                    )
                continue
            for span in inside:  # pedaco exato do MESMO nome dentro da grafia deturpada: o nome inteiro vence
                spans.remove(span)
                if (span.start, span.end) in occupied:
                    occupied.remove((span.start, span.end))
            occupied.append((start, end))
            complete_starts[entry.canonical_key].append(start)
            spans.append(
                EntitySpan(
                    start=start, end=end, entity_type=entry.entity_type, score=0.95, recognizer="ListaDeAlvos",
                    canonical_key=entry.canonical_key, alvo=entry.original,
                )
            )
            uncertainty.record(
                text,
                uncertainty.Pending(start, end, uncertainty.FUZZY_NAME, entry.entity_type, entry.canonical_key),
            )

    def _generation_span(self, start: int, tail: re.Match[str], entry: "_TargetEntry") -> EntitySpan:
        """O nome listado + sufixo de geracao: span da OUTRA pessoa (chave e alvo proprios)."""

        label = suffix_label(tail)
        word = label.upper() if entry.original.isupper() else label.capitalize()
        return EntitySpan(
            start=start,
            end=tail.end(),
            entity_type="PERSON",
            score=0.95,
            recognizer="ListaDeAlvos",
            canonical_key=f"{entry.canonical_key} {label}",
            alvo=f"{entry.original} {word}",
        )

    def _generation_blocks(self, text: str, skipped: set[str]) -> list[tuple[int, int]]:
        """v0.4.10: listado "Fulano Filho" (sem o pai na lista): o nome do pai sem o sufixo nao e da entrada. Os
        trechos ficam ocupados para a lista (o NER decide)."""

        bodies = {entry.body_folded for entry in self._entries if entry.kind == "pessoa"}
        blocks: list[tuple[int, int]] = []
        for entry in self._entries:
            if entry.kind != "pessoa" or entry.canonical_key in skipped:
                continue
            generation = generation_of(entry.original)
            base = strip_generation(entry.original)
            if not generation or len(base.split()) < 2 or fold_lexical(base) in bodies:
                continue
            for start, end in _exact_and_fuzzy_spans(text, base):
                tail = generation_tail(text, end)
                if tail is not None and suffix_label(tail) in generation:
                    continue
                blocks.append((start, end))
        return blocks

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



# v0.4.15: a sigla (3 ou 4 letras) que abre a razao social da lista ("ABC Comercio Ltda" -> "ABC"), escrita sozinha em caixa alta, e a
# empresa (leitura folha a folha: "a ABC", "da ABC" em claro). So sigla de 2 a 5 letras de UMA empresa da lista, sem
# outra palavra em caixa alta colada (fora o sufixo societario: "ABC LTDA EPP" e a mesma).
_ACRONYM = re.compile(r"[A-Z][A-Z0-9]{2,3}")
_UPPER_WORD_BEFORE = re.compile(r"[A-ZÀ-Ý][A-ZÀ-Ý0-9]+[ \t]+$")
_UPPER_WORD_AFTER = re.compile(r"[ \t]+([A-ZÀ-Ý][A-ZÀ-Ý0-9]+)")


def _acronym_spans(
    text: str, entries: Sequence[_TargetEntry], occupied: Sequence[tuple[int, int]]
) -> list[tuple[int, int, _TargetEntry]]:
    owners: dict[str, list[_TargetEntry]] = defaultdict(list)
    for entry in entries:
        if entry.kind != "empresa":
            continue
        words = entry.original.split()
        if len(words) < 2 or not _ACRONYM.fullmatch(words[0]) or not any(char.isalpha() for char in words[0]):
            continue
        if is_kept_term(words[0]) or is_blocked_particle(words[0]) or is_geo_name(words[0]):
            continue
        if not 3 <= len(words[0]) <= 4 or fold_lexical(words[0]) in _dictionary_words():
            continue  # "BR", "CR" (moeda, pais) e palavra comum em caixa alta ("COSTA") nao sao sigla
        if is_common_person_particle(words[0]):
            continue
        if entry not in owners[words[0]]:
            owners[words[0]].append(entry)
    found: list[tuple[int, int, _TargetEntry]] = []
    for acronym, candidates in owners.items():
        if len(candidates) != 1:
            continue  # duas empresas da lista com a mesma sigla: nao diz qual
        for match in re.finditer(rf"(?<![\w&/.-]){re.escape(acronym)}(?![\w&/-])", text):
            start, end = match.span()
            if any(start < other_end and other_start < end for other_start, other_end in occupied):
                continue
            after = _UPPER_WORD_AFTER.match(text, end)
            suffixed = after is not None and after.group(1).casefold().rstrip(".") in _LEGAL_TOKENS
            if after is not None and not suffixed:
                continue
            line_start = text.rfind("\n", 0, start) + 1
            if not suffixed and _UPPER_WORD_BEFORE.search(text[line_start:start]):
                continue  # "EMITENTE ABC LTDA" passa: o sufixo societario diz que e a empresa
            if is_magistrate_or_citation(text, start, end):
                continue
            found.append((start, end, candidates[0]))
    return found


def _glued_runs(text: str, pieces: Sequence[tuple[int, int, object]]) -> list[list[tuple[int, int, object]]]:
    """Pedacos (inicio, fim, dado) em sequencia, colados so por espaco, uma quebra de linha ou de/da/do/dos/das."""

    runs: list[list[tuple[int, int, object]]] = []
    for piece in sorted(pieces, key=lambda item: (item[0], item[1])):
        if runs and runs[-1][-1][1] <= piece[0] and _NAME_GLUE.fullmatch(text[runs[-1][-1][1] : piece[0]]):
            runs[-1].append(piece)
        else:
            runs.append([piece])
    return runs


def _shared_surname_spans(
    text: str,
    shared: Sequence[tuple[int, int, tuple[_TargetEntry, ...]]],
    spans: Sequence[EntitySpan],
) -> list[EntitySpan]:
    """v0.4.14: o sobrenome (ou prenome) de 2+ pessoas da lista, casado sozinho. Antes ia para a pessoa da mencao
    completa mais proxima: "Fulana Prado Souza" (fora da lista) virava "Fulana Prado [token da Beltrana Souza]", e a
    irma, o pai e a tia ficavam todos com o token de uma pessoa so.

    Agora decide pelo nome escrito em volta: os pedacos da lista colados a ele (outro sobrenome, o prenome, a frase
    "Prenome Segundo") dizem de quem e quando so UMA pessoa da lista tem todos ("Beltrana Prado Souza Lima" escrita sem
    o ultimo nome). Sem isso, o sobrenome leva o token de SOBRENOME da familia ([SOBRENOME_n], explicado no ELENCO), nunca
    o de uma pessoa. O nome inteiro de outra pessoa achado pelo NER vence depois (``yield_partial_to_other_person``)."""

    from .variant_propagation import family_span

    by_key = {}
    pieces: list[tuple[int, int, object]] = []
    for span in spans:
        if span.recognizer == "ListaDeAlvos" and span.entity_type == "PERSON" and span.canonical_key:
            pieces.append((span.start, span.end, frozenset({span.canonical_key})))
    for start, end, owners in shared:
        for owner in owners:
            by_key[owner.canonical_key] = owner
        pieces.append((start, end, ("shared", frozenset(owner.canonical_key for owner in owners))))
    found: list[EntitySpan] = []
    for run in _glued_runs(text, pieces):
        keys: frozenset[str] | None = None
        for _start, _end, data in run:
            owned = data[1] if isinstance(data, tuple) else data
            keys = owned if keys is None else keys & owned
        for start, end, data in run:
            if not isinstance(data, tuple):
                continue
            if keys is not None and len(keys) == 1 and next(iter(keys)) in by_key:
                entry = by_key[next(iter(keys))]
                found.append(
                    EntitySpan(
                        start=start, end=end, entity_type="PERSON", score=0.95, recognizer="ListaDeAlvos",
                        canonical_key=entry.canonical_key, alvo=entry.original,
                    )
                )
            else:
                found.append(replace(family_span(text, start, end, score=0.95), recognizer="ListaDeAlvos"))
    return found


def _join_listed_pieces(text: str, spans: Sequence[EntitySpan]) -> list[EntitySpan]:
    """v0.4.14: pedacos da lista da MESMA pessoa colados ("Prenome Segundo" + "Sobrenome" + quebra de linha +
    "Outro") viram um span so: o texto mostrava o token repetido palavra a palavra."""

    people = [span for span in spans if span.recognizer == "ListaDeAlvos" and span.entity_type == "PERSON"]
    if len(people) < 2:
        return list(spans)
    out = [span for span in spans if not (span.recognizer == "ListaDeAlvos" and span.entity_type == "PERSON")]
    ordered = sorted(people, key=lambda span: (span.start, span.end))
    current = ordered[0]
    for span in ordered[1:]:
        if (
            span.canonical_key == current.canonical_key
            and current.end <= span.start
            and _NAME_GLUE.fullmatch(text[current.end : span.start])
        ):
            current = replace(current, end=span.end, score=max(current.score, span.score))
            continue
        out.append(current)
        current = span
    out.append(current)
    return out


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


def _company_body(value: str) -> str:
    return fold_lexical(_LEGAL_TAIL.sub("", value).strip(" -,."))


def _place_aliases(
    parsed: Sequence[tuple[str, tuple[str, ...]]],
    companies: list[str],
    people: list[str],
) -> dict[tuple[str, str], list[str]]:
    """Cada linha de grafias vai para a entrada do nome principal (empresa ou pessoa ja listada). Principal que nao
    esta em nenhuma lista entra na de empresas se tiver sufixo societario, senao na de pessoas."""

    placed: dict[tuple[str, str], list[str]] = {}
    for principal, aliases in parsed:
        folded = fold_lexical(principal)
        body = _company_body(principal)
        if any(fold_lexical(item) == folded or _company_body(item) == body for item in companies):
            key = ("empresa", body)
        elif any(fold_lexical(item) == folded for item in people):
            key = ("pessoa", folded)
        elif _LEGAL_TAIL.search(principal) or _CORPORATE_FOLLOW.search(" " + principal):
            companies.append(principal)
            key = ("empresa", body)
        else:
            people.append(principal)
            key = ("pessoa", folded)
        bucket = placed.setdefault(key, [])
        for alias in aliases:
            if alias not in bucket:
                bucket.append(alias)
    return placed


def _single_common_word(value: str) -> bool:
    words = value.split()
    return len(words) == 1 and (is_common_person_particle(value) or is_blocked_particle(value))


_COMMON_WORDS_FILE = Path(__file__).with_name("data") / "palavras_comuns.txt"


@lru_cache(maxsize=1)
def _dictionary_words() -> frozenset[str]:
    """``data/palavras_comuns.txt`` (o mesmo dicionario da propagacao de variante), dobrado."""

    try:
        lines = _COMMON_WORDS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(fold_lexical(line.strip()) for line in lines if line.strip() and not line.startswith("#"))


def alias_needs_support(value: str) -> bool:
    """H9-n: a grafia que o Operador cadastrou em "Outras grafias" casa SOZINHA, mesmo palavra unica ou prenome/
    sobrenome comum (Maria, Silva): e decisao consciente. Continua exigindo apoio (rotulo de parte ou outra palavra do
    mesmo nome colada) a palavra unica que e UF, municipio ou estado (``is_geo_name``), palavra da denylist de
    particula (mes, estado civil, tese, giro) ou palavra do dicionario em uso comum (``data/palavras_comuns.txt``).
    O termo protegido (aba Regras e termos fixos) nunca casa, com ou sem apoio."""

    words = value.split()
    if len(words) != 1:
        return False
    folded = fold_lexical(value)
    return is_blocked_particle(value) or is_geo_name(value) or folded in _dictionary_words()


def _build_entries(
    companies: Sequence[str],
    people: Sequence[str],
    aliases: dict[tuple[str, str], list[str]] | None = None,
) -> tuple[_TargetEntry, ...]:
    aliases = aliases or {}
    entries: list[_TargetEntry] = []
    company_groups: dict[str, list[str]] = {}
    company_order: list[str] = []
    for value in companies:
        body = _company_body(value)
        if not body:
            continue
        if body not in company_groups:
            company_groups[body] = []
            company_order.append(body)
        if value not in company_groups[body]:
            company_groups[body].append(value)
    for body in company_order:
        originals = tuple(company_groups[body])
        extra: list[str] = []
        loose: list[str] = []
        for alias in aliases.get(("empresa", body), ()):
            alias_body = _company_body(alias)
            if not alias_body or (alias_body != body and alias_body in company_groups):
                continue  # grafia que e outra entrada da lista: duas entradas nunca se fundem
            if alias_needs_support(alias):
                loose.append(alias)  # H9-n: UF, municipio, denylist ou dicionario: so com apoio, como particula
            elif alias not in originals and alias not in extra:
                extra.append(alias)  # H9-n: a grafia cadastrada casa sozinha, mesmo palavra comum de nome
        phrases = _company_search_terms((*originals, *extra))
        entries.append(
            _TargetEntry(
                original=originals[0],
                kind="empresa",
                entity_type="ORGANIZATION",
                body_folded=body,
                canonical_key=f"alvo:empresa:{body}",
                phrases=phrases,
                particles=tuple(loose),
                names=(*originals, *extra),
                aliases=tuple(extra),
                guarded=tuple(loose),
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
    folded_bodies = set(person_groups)
    for body in person_order:
        originals = tuple(person_groups[body])
        extra: list[str] = []
        loose: list[str] = []
        for original in originals:
            for alias in aliases.get(("pessoa", fold_lexical(original)), ()):
                alias_body = fold_lexical(alias)
                if not alias_body or (alias_body != body and alias_body in folded_bodies):
                    continue  # grafia que e outra pessoa da lista: duas entradas nunca se fundem
                if alias_needs_support(alias):
                    loose.append(alias)  # H9-n: so com apoio (ver alias_needs_support)
                elif alias not in originals and alias not in extra:
                    extra.append(alias)
        particles: list[str] = []
        seen = set()
        for original in (*originals, *extra):
            for part in _entry_particles(original, company=False):
                key = fold_lexical(part)
                if key in seen:
                    continue
                seen.add(key)
                particles.append(part)
        for part in loose:
            key = fold_lexical(part)
            if key not in seen:
                seen.add(key)
                particles.append(part)
        # As frases derivadas (par inicial, sobrenomes colados) saem so do nome listado; a grafia adicional
        # entra inteira (e com as particulas), sem gerar frase nova.
        phrases = tuple(
            dict.fromkeys(
                (
                    *originals,
                    *extra,
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
                names=(*originals, *extra),
                aliases=tuple(extra),
                guarded=tuple(loose),
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


_RUN_WORD = re.compile(r"[^\W\d_][^\W\d_'’-]*")


def _inside_other_name(text: str, start: int, end: int, owners: Sequence["_TargetEntry"]) -> bool:
    """v0.4.10: a palavra esta no meio de um nome escrito por inteiro (3+ palavras de nome na mesma linha) que comeca
    e termina por palavras que nao sao de nenhum dos listados: e o nome de outra pessoa ("MARCIO AUGUSTO DO CARMO DE
    ALMEIDA" x listado "AUGUSTO ...")."""

    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    words = [
        (line_start + m.start(), line_start + m.end(), m.group()) for m in _RUN_WORD.finditer(text[line_start:line_end])
    ]
    index = next((i for i, (a, b, _w) in enumerate(words) if a <= start and end <= b), None)
    if index is None:
        return False

    def joined(i: int, j: int) -> bool:
        return bool(re.fullmatch(r"[ \t]+", text[words[i][1] : words[j][0]]))

    def name_like(word: str) -> bool:
        folded = fold_lexical(word)
        return folded in _CONNECTIVES or (word[:1].isupper() and len(folded) >= 2)

    left = index
    while left > 0 and joined(left - 1, left) and name_like(words[left - 1][2]):
        left -= 1
    right = index
    while right + 1 < len(words) and joined(right, right + 1) and name_like(words[right + 1][2]):
        right += 1
    content = [fold_lexical(w) for _a, _b, w in words[left : right + 1] if fold_lexical(w) not in _CONNECTIVES]
    if len(content) < 3:
        return False
    owned = {word for owner in owners for name in (owner.original, *owner.names) for word in fold_lexical(name).split()}
    # Outra pessoa: o nome comeca E termina por palavras que nao sao do listado (prenome e sobrenome final dela).
    return content[0] not in owned and content[-1] not in owned


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
    if _VIA_CUE.search(window) or PLACE_NAME_BEFORE.search(window):
        return True
    return is_magistrate_or_citation(text, start, end) or is_pfn_officer(text, start, end)


# v0.4.15: nome de lugar com prenome de pessoa ("Colonia Dona Fulana", "Vila Santa Beltrana", "Jardim Sicrano"): o prenome
# que vem logo depois e do lugar, nao da pessoa.
PLACE_NAME_BEFORE = re.compile(
    r"(?i)\b(?:col[ôo]nia|vila|jardim|parque|bairro|loteamento|distrito|linha|localidade|comunidade|residencial"
    r"|recanto|ch[áa]cara|s[íi]tio|fazenda|est[âa]ncia|n[úu]cleo)[ \t]+"
    r"(?:(?:dona|dom|santa|santo|s[ãa]o|nossa[ \t]+senhora(?:[ \t]+d[aeo]s?)?)[ \t]+)?$"
)


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


# H9-o: o nome de pessoa mais longo (NER) que vence o pedaco de um listado entra com esta fonte: ancorado pela lista
# (nao cai no filtro de "nome so banal") e acima do NER e da capa no desempate (rank em ``detector.py``).
ANCHORED_NER_RECOGNIZER = "NerAncoradoLista"
_TITLE_WORDS = frozenset({"dr", "dra", "sr", "sra", "srta", "exmo", "exma", "doutor", "doutora", "senhor", "senhora"})


def _span_words(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
    return [
        (start + match.start(), start + match.end(), match.group())
        for match in re.finditer(r"[^\W\d_][^\W\d_'’]*", text[start:end])
    ]


def _is_name_word(word: str) -> bool:
    from .role_labels import ROLE_LABEL_WORDS

    folded = fold_lexical(word)
    return (
        len(folded) >= 2
        and word[:1].isupper()
        and folded not in _CONNECTIVES
        and folded not in _TITLE_WORDS
        and folded not in ROLE_LABEL_WORDS
        and folded not in _SKIP_FOLDED
        and not is_blocked_particle(word)
    )


def yield_partial_to_other_person(
    text: str,
    spans: Sequence[EntitySpan],
    engine: "TargetListEngine",
    yielded: list[tuple[EntitySpan, EntitySpan]] | None = None,
) -> list[EntitySpan]:
    """H9-o: o pedaco de um listado (sobrenome ou prenome casado sozinho, frase parcial) que cai DENTRO de um nome de
    pessoa mais longo de OUTRA pessoa (NER, capa) cede: o nome longo vence e vira token proprio.

    Caso medido: o sobrenome do socio listado, casado sozinho (lista, prioridade 500), comia o sobrenome de familia
    dentro de "Prenome Outro Sobrenome" de outro membro da familia (NER, 200); o sobrenome saia com o token do socio
    e o prenome do outro ficava em claro. Continua vencendo como hoje: o nome completo listado, a grafia cadastrada
    ("Outras grafias") e o pedaco dentro de um nome que comeca pelo mesmo prenome do listado (pode ser ele mesmo, por
    extenso). O prenome sozinho do listado continua trocado (H9-k): so cede quando esta dentro do nome mais longo."""

    entries = {entry.canonical_key: entry for entry in engine.entries if entry.kind == "pessoa"}
    hosts = sorted(
        (
            index
            for index, span in enumerate(spans)
            if span.entity_type == "PERSON" and span.recognizer not in {"ListaDeAlvos", "ListaDoCaso"}
        ),
        key=lambda index: spans[index].start,
    )
    host_starts = [spans[index].start for index in hosts]
    if not entries or not hosts:
        return list(spans)
    full_forms: dict[str, frozenset[str]] = {}
    first_words: dict[str, frozenset[str]] = {}
    for key, entry in entries.items():
        values = (entry.original, *entry.names)
        full_forms[key] = frozenset(_mention_form(value) for value in (*values, *entry.guarded))
        firsts = set()
        for value in values:
            words = [word for word in _mention_form(value).split() if word not in _SKIP_FOLDED]
            if words:
                firsts.add(words[0])
        first_words[key] = frozenset(firsts)

    dropped: set[int] = set()
    promoted: dict[int, EntitySpan] = {}
    for index, span in enumerate(spans):
        if span.recognizer != "ListaDeAlvos" or span.entity_type not in {"PERSON", "FAMILY_NAME"}:
            continue
        if span.entity_type == "FAMILY_NAME":
            # v0.4.14: o sobrenome de familia (2+ pessoas da lista) dentro do nome inteiro de alguem achado pelo NER cede
            # a ele: o nome vira token proprio (ou o do listado, se for ele por extenso).
            entry = _family_entry(span, text)
            firsts: frozenset[str] = frozenset()
        else:
            entry = entries.get(span.canonical_key)
            if entry is None:
                continue
            if _mention_form(span.value_from(text)) in full_forms[entry.canonical_key]:
                continue  # nome completo listado ou grafia cadastrada: vence como hoje
            firsts = first_words[entry.canonical_key]
        # Os nomes que comecam antes do pedaco (os mais proximos primeiro; NER quase nunca se sobrepoe).
        position = bisect_right(host_starts, span.start)
        for host_index in reversed(hosts[max(0, position - 4) : position]):
            host = promoted.get(host_index, spans[host_index])
            if not (host.start <= span.start and span.end <= host.end):
                continue
            if host.end - host.start <= span.end - span.start:
                continue
            outcome = _other_person_host(text, host, span, entry, firsts, engine)
            if outcome is None:
                continue
            dropped.add(index)
            promoted[host_index] = outcome
            if yielded is not None:
                yielded.append((span, outcome))
            break
    if not dropped:
        return list(spans)
    return [promoted.get(index, span) for index, span in enumerate(spans) if index not in dropped]


def restore_orphan_partials(
    resolved: Sequence[EntitySpan], yielded: Sequence[tuple[EntitySpan, EntitySpan]]
) -> list[EntitySpan]:
    """v0.4.10: o pedaco listado que cedeu a um nome mais longo volta quando o nome longo NAO sobreviveu ao
    ``resolve_overlaps`` (perdeu para outro span): sem isso o sobrenome de familia ficava orfao, em claro."""

    if not yielded:
        return list(resolved)
    alive = {(span.start, span.end) for span in resolved}
    lost = [
        part
        for part, host in yielded
        if (host.start, host.end) not in alive
        and not any(item.start <= part.start and part.end <= item.end for item in resolved)
    ]
    if not lost:
        return list(resolved)
    return resolve_overlaps([*resolved, *lost])


def _family_entry(span: EntitySpan, text: str) -> _TargetEntry:
    """Entrada provisoria do sobrenome de familia (so para ``_other_person_host``): chave e grafia do sobrenome."""

    value = span.value_from(text)
    return _TargetEntry(
        original=value,
        kind="pessoa",
        entity_type="PERSON",
        body_folded=fold_lexical(value),
        canonical_key=span.canonical_key or "",
        phrases=(),
        particles=(),
        names=(value,),
    )


# v0.4.14: nome inteiro do NER partido por UMA quebra de linha entre duas palavras de nome ("Prenome Segundo" na linha
# de cima, "Sobrenome Final" na de baixo), fora de tabela.
_NAME_LINE_BREAK = re.compile(r"[^\W\d_][ \t]*\n[ \t]*[^\W\d_]")


def _broken_name(value: str) -> bool:
    return value.count("\n") == 1 and "|" not in value and bool(_NAME_LINE_BREAK.search(value))


def _other_person_host(
    text: str,
    host: EntitySpan,
    part: EntitySpan,
    entry: _TargetEntry,
    entry_firsts: frozenset[str],
    engine: "TargetListEngine",
) -> EntitySpan | None:
    """O nome mais longo, se e de OUTRA pessoa (e passa pelos filtros de fidelidade); senao ``None``."""

    value = host.value_from(text)
    if ("\n" in value and not _broken_name(value)) or (host.canonical_key and host.canonical_key == entry.canonical_key):
        return None
    content = [item for item in _span_words(text, host.start, host.end) if fold_lexical(item[2]) not in _CONNECTIVES]
    if len(content) < 2:
        return None
    first = content[0]
    if not _is_name_word(first[2]):
        return None
    if (
        engine._kept(value)
        or is_magistrate_or_citation(text, host.start, host.end)
        or is_pfn_officer(text, host.start, host.end)
        or in_public_agent_name(text, host.start, host.end)
    ):
        return None
    other_generation = conflicting_generation(entry.original, value)
    resolved = engine.resolve_mention(value, text=text, start=host.start, end=host.end)
    if resolved is not None and resolved.canonical_key == entry.canonical_key and not other_generation:
        if first[0] < part.start:
            return None
        # v0.4.10: o prenome listado no comeco do nome inteiro da MESMA pessoa: um token so, o dela, no nome inteiro.
        return replace(
            host, canonical_key=entry.canonical_key, alvo=entry.original, recognizer="ListaDeAlvos", score=0.96
        )
    if first[0] >= part.start:
        # v0.4.10: o pedaco e o prenome do nome longo. So cede se o nome longo e de OUTRA pessoa: outro sufixo de
        # geracao (pai x filho) ou o sobrenome final fora do nome listado ("Fulano Pereira" x listado "Fulano Silva").
        listed_words = {word for name in (entry.original, *entry.names) for word in _mention_form(name).split()}
        last = fold_lexical(content[-1][2])
        if not other_generation and (last in listed_words or last in _SKIP_FOLDED):
            if fold_lexical(first[2]) in entry_firsts and last in listed_words and "\n" not in value:
                # Mesmo prenome e mesmo sobrenome final: o listado por extenso (nome do meio a mais). Um token so, o
                # dele, no nome inteiro: o nome do meio nao fica em claro.
                return replace(
                    host, canonical_key=entry.canonical_key, alvo=entry.original, recognizer="ListaDeAlvos", score=0.96
                )
            return None
    else:
        outside = [item for item in content if item[1] <= part.start or item[0] >= part.end]
        if not any(_is_name_word(item[2]) for item in outside):
            return None
        if fold_lexical(first[2]) in entry_firsts and not other_generation:
            return None  # mesmo prenome do listado: pode ser ele mesmo por extenso
    if resolved is not None and resolved.canonical_key == entry.canonical_key:
        resolved = None
    if resolved is not None and resolved.kind == "pessoa":
        return replace(host, canonical_key=resolved.canonical_key, alvo=resolved.original, recognizer="ListaDeAlvos")
    if host.recognizer in {"HeaderHeuristic", "FormFields", ANCHORED_NER_RECOGNIZER}:
        return host
    return replace(host, recognizer=ANCHORED_NER_RECOGNIZER)


# H9-o: rotulo de julgador logo antes do nome ("Rel. Des.", "Relatora", "Juíza Federal", "Min.").
_AGENT_CUE_BEFORE = re.compile(
    r"(?i)(?:\brel\.|\brelator(?:a)?|\bdes\.|\bdesembargador(?:a)?(?:\s+federal)?"
    r"|\bju[íi]z(?:a)?(?:\s+(?:federal|de\s+direito|substitut[oa]))?|(?<![^\W\d_])min\.|\bminist(?:ro|ra))"
    r"\s*[:,\-–]?\s*$"
)
_AGENT_CUE_AFTER = re.compile(
    r"(?i)^\s*[,–—\-]?\s*\(?\s*(?:relator(?:a)?|desembargador(?:a)?|ju[íi]z(?:a)?|minist(?:ro|ra)"
    # v0.4.10: cargo de servidor depois do nome na assinatura ("FULANO, Servidor", "Analista Judiciario").
    r"|servidor(?:a)?|analista\s+judici[aá]ri[oa]|t[eé]cnic[oa]\s+judici[aá]ri[oa]|diretor(?:a)?\s+de\s+secretaria"
    r"|oficial(?:a)?\s+de\s+justi[cç]a|escriv[aã]o?|procurador(?:a)?\s+da\s+fazenda)\b"
)
# Uma palavra de nome (maiuscula, abreviatura curta "Des.", ou particula de/da/do/dos/das/e) colada ao que vem depois.
_NAME_BACK = re.compile(r"(?:[A-ZÀ-Ý][^\W\d_]*(?:['’-][^\W\d_]+)?|[A-Z][a-z]{0,3}\.|(?i:d[aeo]s?|e))[ \t]+$")
_NAME_AHEAD = re.compile(r"^[ \t]+(?:[A-ZÀ-Ý][^\W\d_]*(?:['’-][^\W\d_]+)?|(?i:d[aeo]s?|e)(?=[ \t]+[A-ZÀ-Ý]))")
_AGENT_WALK = 6


def in_public_agent_name(text: str, start: int, end: int) -> bool:
    """H9-o: o trecho esta dentro do nome de um julgador: o nome (palavras maiusculas e particulas, na mesma linha) que
    o contem vem logo depois de Rel., Relator(a), Des., Desembargador(a), Juiz(a), Min., Ministro(a), ou logo antes de
    ", Relatora" / ", Desembargador"... Ex.: "Rel. Des. Maria de Fátima Exemplo": nem "Maria" nem "Fátima" sao
    trocados como grafia de uma pessoa do caso."""

    position = start
    for _step in range(_AGENT_WALK + 1):
        if _AGENT_CUE_BEFORE.search(text[max(0, position - 60) : position]) or is_magistrate_or_citation(
            text, position, end
        ):
            return True
        match = _NAME_BACK.search(text[max(0, position - 40) : position])
        if match is None:
            break
        position -= len(match.group())
    position = end
    for _step in range(_AGENT_WALK):
        match = _NAME_AHEAD.match(text[position : position + 40])
        if match is None:
            break
        position += len(match.group())
    return bool(_AGENT_CUE_AFTER.match(text[position : position + 40]))


@lru_cache(maxsize=1)
def _protected_by_word() -> dict[str, tuple[str, ...]]:
    """Termos protegidos fixos de 2+ palavras (``KEEP_TERMS``, com ``sistemas_protegidos.txt``) por palavra."""

    from .gemini_detector import KEEP_TERMS

    return terms_by_word(KEEP_TERMS)


def terms_by_word(terms: Iterable[str]) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = defaultdict(list)
    for term in terms:
        words = [word for word in fold_lexical(str(term)).split() if word]
        if len(words) < 2:
            continue
        for word in set(words):
            found[word].append(str(term))
    return {word: tuple(items) for word, items in found.items()}


@lru_cache(maxsize=4096)
def _term_pattern(term: str) -> re.Pattern[str]:
    return phrase_pattern(term)


def inside_protected_term(
    text: str, start: int, end: int, extra: dict[str, tuple[str, ...]] | None = None
) -> bool:
    """H9-o: o trecho cabe dentro de uma ocorrencia de termo protegido de 2+ palavras (lista fixa e "Termos
    protegidos" do Operador, como o nome do relator)."""

    words = [word for word in fold_lexical(text[start:end]).split() if word]
    if not words:
        return False
    candidates = set(_protected_by_word().get(words[0], ()))
    if extra:
        candidates.update(extra.get(words[0], ()))
    if not candidates:
        return False
    low = max(0, start - 200)
    window = text[low : min(len(text), end + 200)]
    for term in candidates:
        for match in _term_pattern(term).finditer(window):
            if low + match.start() <= start and end <= low + match.end():
                return True
    return False


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


_MENTION_EDGE = " \t\n,.;:()[]{}\"'-–—"
_MENTION_TITLE = re.compile(r"^(?:dr|dra|sr|sra|srta|exmo|exma)\.?\s+")
_SKIP_FOLDED = frozenset(fold_lexical(item) for item in _PARTICLE_SKIP)


def _generation_mismatch(value: str, entry: "_TargetEntry") -> bool:
    """v0.4.10: a mencao e o listado diferem por sufixo de geracao (pai x filho, avo x neto)."""

    return entry.kind == "pessoa" and conflicting_generation(entry.original, value)


def _mention_form(value: str) -> str:
    folded = " ".join(fold_lexical(value).split()).strip(_MENTION_EDGE)
    folded = _MENTION_TITLE.sub("", folded)
    return _ESPOLIO_PREFIX.sub("", folded).strip(_MENTION_EDGE)


class _MentionIndex:
    """As formas de cada entrada da lista, para resolver a mencao que chega de fora da busca da lista (o LLM, o
    NER): nome, grafia adicional, razao sem sufixo, prefixo, frase do nome e particula (``resolve_mention``)."""

    def __init__(self, entries: Sequence[_TargetEntry]) -> None:
        self._entries = tuple(entries)
        self._forms: dict[str, list[_TargetEntry]] = defaultdict(list)
        self._particles: dict[str, list[_TargetEntry]] = defaultdict(list)
        self._words: dict[str, frozenset[str]] = {}
        self._guarded: set[str] = set()
        for entry in self._entries:
            keys: set[str] = {entry.body_folded}
            aliases = {_mention_form(item) for item in entry.aliases}
            self._guarded.update(fold_lexical(item) for item in entry.guarded)
            for value in (entry.original, *entry.names, *entry.phrases):
                for candidate in (value, _LEGAL_TAIL.sub("", value) if entry.kind == "empresa" else value):
                    folded = _mention_form(candidate)
                    if not folded:
                        continue
                    # H9-n: a grafia cadastrada pelo Operador casa sozinha, mesmo palavra comum de nome.
                    if " " not in folded and _single_common_word(folded) and folded not in aliases:
                        continue
                    keys.add(folded)
            for key in keys:
                if entry not in self._forms[key]:
                    self._forms[key].append(entry)
            for particle in entry.particles:
                folded = fold_lexical(particle)
                if entry not in self._particles[folded]:
                    self._particles[folded].append(entry)
            words = {
                word
                for value in (entry.original, *entry.names)
                for word in _mention_form(value).split()
                if word not in _SKIP_FOLDED
            }
            self._words[entry.canonical_key] = frozenset(words)

    def resolve(
        self,
        value: str,
        *,
        text: str = "",
        start: int = -1,
        end: int = -1,
        kept=lambda _value: False,
    ) -> _TargetEntry | None:
        folded = _mention_form(value)
        if not folded:
            return None
        positioned = bool(text) and 0 <= start < end <= len(text)
        if positioned and generation_of(value) == frozenset():
            tail = generation_tail(text, end)
            if tail is not None and len(folded.split()) >= 2:
                value = value + " " + suffix_label(tail)  # a mencao "Fulano" seguida de "Filho" no texto
        candidates = self._forms.get(folded) or self._forms.get(_mention_form(_LEGAL_TAIL.sub("", value)))
        if candidates:
            candidates = [item for item in candidates if not kept(item.original) and not _generation_mismatch(value, item)]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1 and positioned:
                return self._alias(text, start, end, value, candidates)
            return None
        words = [word for word in folded.split() if word not in _SKIP_FOLDED]
        if len(words) == 1:
            word = words[0]
            if is_blocked_particle(word):
                return None
            owners = [
                item
                for item in self._particles.get(word, ())
                if not kept(item.original) and not _generation_mismatch(value, item)
            ]
            if not owners:
                return None
            if positioned and _particle_blocked(text, start, end):
                return None
            if is_common_person_particle(word) or word in self._guarded:
                if not positioned:
                    return None
                if not (
                    _particle_has_support(text, start, end, owners, ())
                    or any(
                        _name_adjacent_to_phrase(text, start, end, name)
                        for owner in owners
                        for name in owner.names
                    )
                ):
                    return None
            if len(owners) == 1:
                return owners[0]
            return self._alias(text, start, end, value, owners) if positioned else None
        if len(words) >= 2:
            wanted = set(words)
            owners = [
                item
                for item in self._entries
                if item.kind == "pessoa"
                and wanted <= self._words.get(item.canonical_key, frozenset())
                and not kept(item.original)
                and not _generation_mismatch(value, item)
            ]
            distinctive = any(
                len(word) >= _MIN_PARTICLE_CHARS
                and not is_common_person_particle(word)
                and not is_blocked_particle(word)
                for word in words
            )
            if len(owners) == 1 and distinctive:
                return owners[0]
        return None

    def _alias(
        self, text: str, start: int, end: int, value: str, candidates: Sequence[_TargetEntry]
    ) -> _TargetEntry | None:
        complete: dict[str, list[int]] = defaultdict(list)
        for entry in candidates:
            for name in entry.names or (entry.original,):
                complete[entry.canonical_key].extend(
                    match.start() for match in phrase_pattern(name).finditer(text)
                )
        entry, _key, _alvo = resolve_particle_alias(
            text, start, end, particle=value, candidates=candidates, complete_starts=complete
        )
        return entry

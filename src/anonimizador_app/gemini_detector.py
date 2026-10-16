from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from anonimizador_juridico import EntitySpan, resolve_overlaps
from anonimizador_juridico.pseudonymizer import fold_lexical

from .role_labels import is_fixed_label, is_role_label

SYSTEMS_FILE = Path(__file__).with_name("data") / "sistemas_protegidos.txt"


@lru_cache(maxsize=1)
def protected_systems() -> frozenset[str]:
    """Ferramentas e sistemas do processo que nunca viram token (SISBAJUD, RENAJUD, CNIB, PJe, e-CAC...), da lista
    ``data/sistemas_protegidos.txt`` (um termo por linha, minusculo; facil de aumentar sem mexer em codigo)."""

    terms = set()
    for raw in SYSTEMS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip().casefold()
        if line and not line.startswith("#"):
            terms.add(line)
            plain = "".join(c for c in unicodedata.normalize("NFKD", line) if not unicodedata.combining(c))
            terms.add(plain)  # a mesma expressao sem acento
    return frozenset(terms)


def is_system_term(value: str) -> bool:
    """O valor inteiro e um termo da lista de sistemas/orgaos protegidos (``data/sistemas_protegidos.txt``)."""

    return re.sub(r"\s+", " ", value).strip().casefold() in protected_systems()


# So a expressao de varias palavras ("Banco do Brasil S.A.") protege tambem o que vem depois; sigla de uma palavra so
# vale inteira ("Sida Comercial Ltda" e empresa), exceto os sistemas antigos.
_SYSTEMS_EXACT_ONLY = frozenset(
    term for term in protected_systems() if " " not in term
) - frozenset({"sisbajud", "renajud", "cnib", "infojud", "serasajud", "sniper", "censec", "ccs-bacen", "simba"})

KEEP_TERMS = protected_systems() | frozenset(
    {
        "uniao",
        "união",
        "fazenda nacional",
        "fazenda publica",
        "fazenda pública",
        "procuradoria",
        "procuradoria-geral da fazenda nacional",
        "pgfn",
        "pfn",
        "inss",
        "stf",
        "stj",
        "trf4",
        "trf 4",
        "tribunal",
        "justica federal",
        "justiça federal",
        "receita federal",
        "cnj",
        "cpc",
        "ctn",
        "lef",
        "evento",
        "peticao",
        "petição",
        "embargos",
        "agravo",
        "execucao fiscal",
        "execução fiscal",
        "vara federal",
        "juízo federal",
        "juizo federal",
        "subseção judiciária",
        "subsecao judiciaria",
        "subseção judiciaria",
        "dívida ativa",
        "divida ativa",
        "documento extraido",
        "documento extraído",
        "supremo",
        "supremo tribunal federal",
        "vossa excelencia",
        "vossa excelência",
        "nobre excelencia",
        "nobre excelência",
        "presidente da republica",
        "presidente da república",
        "pessoa natural",
        # 26/09/2026: o NER tomava "pessoa jurídica" por empresa (lote eproc de 25/09).
        "pessoa juridica",
        "pessoa jurídica",
        "pessoa fisica",
        "pessoa física",
        "socio controlador",
        "sócio controlador",
        "consumidor final",
        "registro de imoveis",
        "registro de imóveis",
        "desconsideracao da personalidade",
        "desconsideração da personalidade",
        "desconsideracao inversa",
        "desconsideração inversa",
        "personalidade juridica",
        "personalidade jurídica",
        "prescricao intercorrente",
        "prescrição intercorrente",
        "citacao pelo correio",
        "citação pelo correio",
        "citacao por correio",
        "citação por correio",
        "ministério público",
        "ministerio publico",
        "mpf",
        "defensoria pública",
        "defensoria publica",
        "dpu",
        "poder judiciário",
        "poder judiciario",
        "tribunal de justiça",
        "tribunal de justica",
        "tribunal regional federal",
        "superior tribunal de justiça",
        "superior tribunal de justica",
        "justiça do trabalho",
        "justica do trabalho",
        "justiça estadual",
        "justica estadual",
        "justiça eleitoral",
        "justica eleitoral",
        "caixa econômica",
        "caixa economica",
        "banco do brasil",
        "vara da fazenda pública",
        "vara da fazenda publica",
        "constituição federal",
        "constituicao federal",
        "código de processo civil",
        "codigo de processo civil",
        "código de processo penal",
        "codigo de processo penal",
        "comarca",
        "agu",
        "pgf",
        "pge",
        "pgm",
        "advocacia-geral da união",
        "advocacia-geral da uniao",
        "advocacia geral da união",
        "advocacia geral da uniao",
        "procuradoria-geral federal",
        "procuradoria geral federal",
        "procuradoria-geral do estado",
        "procuradoria geral do estado",
        "procuradoria-geral do município",
        "procuradoria-geral do municipio",
        "procuradoria geral do município",
        "procuradoria geral do municipio",
    }
)

_TYPE_MAP = {
    "PERSON": "PERSON",
    "PESSOA": "PERSON",
    "NOME": "PERSON",
    "ORGANIZATION": "ORGANIZATION",
    "EMPRESA": "ORGANIZATION",
    "ORG": "ORGANIZATION",
    "LOCATION": "LOCATION",
    "ENDERECO": "LOCATION",
    "ENDEREÇO": "LOCATION",
    "LOCAL": "LOCATION",
}


class ContextualEngine(Protocol):
    def detect(self, text: str) -> list[EntitySpan]: ...

    def scan_residual(self, text: str) -> list[str]: ...


_STREET_HINTS = (
    "rua ",
    "r. ",
    "avenida ",
    "ave. ",
    "ave ",
    "av. ",
    "avn. ",
    "avn ",
    "alameda ",
    "travessa ",
    "praca ",
    "praça ",
    "rodovia ",
    "estrada ",
    "logradouro ",
    "quadra ",
)
_STREET_ABBREV = re.compile(r"(?:^|[\s,;])r\s")
# H9-i: vias de uma palavra comum (rio, linha, bairro...) so valem como inicio da palavra, nunca dentro de um nome ("Mario Silva").
_STREET_WORD_HINT = re.compile(r"(?:^|[\s,;(])(?:servid[ãa]o|loteamento|linha|rio|bairro|beco|gleba|localidade|distrito|acesso|tv\.|rod\.)\s")


# Nome de cartorio ou de oficio ("1o Registro de Imoveis de X", "Cartorio de Notas de X", "Tabelionato de
# Protesto", "Cartorio do 2o Oficio"): e ente do registro publico, fica em claro, nunca vira [EMPRESA_n].
_REGISTRY_OFFICE = re.compile(
    r"^(?:\d{1,2}\s*o?\s*)?(?:"
    r"(?:cartorio|oficio|servico|tabelionato)\s+(?:d[eao]s?\s+)?(?:\d{1,2}\s*o?\s*)?"
    r"(?:oficio\s+|cartorio\s+)?(?:registro|notas|protesto|imoveis|titulos|registral|civil|\d)"
    r"|(?:cartorio|oficio|servico|tabelionato)\s+d[eao]s?\s+\d{1,2}\s*o?\s*(?:oficio|cartorio|registro)"
    r"|registro\s+(?:geral\s+)?de\s+(?:imoveis|titulos|documentos|pessoas)"
    r"|registro\s+civil|servico\s+registral|(?:oficio|cartorio|tabelionato)\b"
    r")"
)


def is_registry_office(value: str) -> bool:
    """O valor abre com o nome de um cartorio/oficio de registro ou de notas."""

    return bool(_REGISTRY_OFFICE.match(fold_lexical(value)))


def is_kept_term(value: str, extra: Iterable[str] = ()) -> bool:
    compact = re.sub(r"\s+", " ", value).strip().casefold()
    terms = KEEP_TERMS | {
        re.sub(r"\s+", " ", item).strip().casefold() for item in extra if str(item).strip()
    }
    if compact in terms:
        return True
    # Sistema acrescentado em H9-i (PJe, e-CAC, Regularize, SIDA...) so vale inteiro: "Sida Comercial Ltda" e empresa.
    return any(
        compact.startswith(term + " ") for term in terms if term not in _SYSTEMS_EXACT_ONLY
    )


_GENERIC_WORDS = frozenset(
    {
        "porto",
        "alegre",
        "rio",
        "janeiro",
        "paulo",
        "brasilia",
        "brasília",
        "federal",
        "sul",
        "norte",
        "oeste",
        "leste",
        "grande",
        "santa",
        "santo",
        "uniao",
        "união",
        "fazenda",
        "tribunal",
        "justica",
        "justiça",
        "receita",
        "ministerio",
        "ministério",
        "vara",
        "comarca",
        "oficio",
        "ofício",
        "secao",
        "seção",
        "turma",
        "camara",
        "câmara",
        "forum",
        "fórum",
        "executada",
        "exequente",
        "embargante",
        "impetrante",
        "autor",
        "reu",
        "réu",
        "ltda",
        "ltda.",
        "s.a.",
        "sa",
        "me",
        "epp",
        "sociedade",
        "empresaria",
        "empresária",
        "limitada",
        "individual",
        "responsabilidade",
        "natureza",
        "juridica",
        "jurídica",
        "objeto",
        "social",
        "simples",
        "anonima",
        "anônima",
        "microempresa",
        "eireli",
        "s/a",
        "s.a",
    }
)

_GENERIC_PHRASES = frozenset(
    {
        "sociedade empresaria limitada",
        "sociedade empresária limitada",
        "sociedade limitada",
        "sociedade simples",
        "sociedade anonima",
        "sociedade anônima",
        "sociedade anónima",
        "empresario individual",
        "empresário individual",
        "microempresa",
        "empresa de pequeno porte",
        "empresa individual de responsabilidade limitada",
        "natureza juridica",
        "natureza jurídica",
        "objeto social",
        "ltda",
        "eireli",
        "s.a.",
        "s.a",
        "s/a",
    }
)

_PUBLIC_ORG_MARKERS = frozenset(
    {
        "uniao",
        "união",
        "fazenda",
        "tribunal",
        "justica",
        "justiça",
        "receita",
        "ministerio",
        "ministério",
        "procuradoria",
        "vara",
        "comarca",
        "oficio",
        "ofício",
        "secao",
        "seção",
        "subsecao",
        "subseção",
        "juizo",
        "juízo",
        "turma",
        "camara",
        "câmara",
        "forum",
        "fórum",
        "secretaria",
    }
)


def _content_words(value: str) -> list[str]:
    words: list[str] = []
    for item in re.split(r"\s+", value.strip()):
        compact = item.strip(".,;:()[]").casefold()
        if len(compact) <= 2 or compact in _GENERIC_WORDS:
            continue
        words.append(compact)
    return words


def _looks_like_furniture(value: str) -> bool:
    compact = re.sub(r"\s+", " ", value).strip().casefold()
    if "assuntos" in compact and (
        "codigo" in compact or "código" in compact or "descri" in compact
    ):
        return True
    if compact in {"dívida ativa", "divida ativa"}:
        return True
    return False


def is_legal_nature(value: str) -> bool:
    """Natureza jurídica / sufixo societário isolado — não é razão social."""

    compact = re.sub(r"\s+", " ", value).strip().casefold()
    return compact in _GENERIC_PHRASES


def is_too_generic(entity_type: str, value: str) -> bool:
    """Recusa mencoes vagas que descaracterizariam a peca se fossem trocadas."""

    if _looks_like_furniture(value):
        return True
    if is_fixed_label(value):
        return True  # "CPF/CNPJ:", "LTDA", "S/A" soltos: rotulo ou sufixo, nao entidade (H9-i)
    if entity_type in {"PERSON", "ORGANIZATION"} and is_role_label(value):
        return True  # "SOCIO ADMINISTRADOR", "ADMINISTRADOR(ES)": cargo, nao nome (H9-h)
    if len(value.strip()) < 5:
        return True
    compact = re.sub(r"\s+", " ", value).strip().casefold()
    if compact in _GENERIC_PHRASES:
        return True
    words = [item for item in re.split(r"\s+", value.strip()) if item]
    if entity_type == "PERSON":
        return len(words) < 2 or len(_content_words(value)) < 2
    if entity_type == "ORGANIZATION":
        tokens = {
            item.strip(".,;:()[]").casefold()
            for item in words
        }
        if tokens & _PUBLIC_ORG_MARKERS:
            return True
        return len(words) < 2 or len(_content_words(value)) < 1
    if entity_type == "LOCATION":
        if any(hint in compact for hint in _STREET_HINTS) or _STREET_WORD_HINT.search(compact):
            return False
        return _STREET_ABBREV.search(compact) is None
    return False


def _has_boundaries(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1].isalnum():
        return False
    if end < len(text) and text[end].isalnum():
        return False
    return True


def locate_mentions(
    text: str,
    mentions: Sequence[tuple[str, str]],
    extra_keep: Iterable[str] = (),
    *,
    recognizer: str = "GeminiContextual",
    score: float = 0.72,
    enforce_generic_filter: bool = True,
) -> list[EntitySpan]:
    occupied: list[tuple[int, int]] = []
    spans: list[EntitySpan] = []
    ordered = sorted(
        ((entity_type, value.strip()) for entity_type, value in mentions if value.strip()),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    for entity_type, value in ordered:
        if is_kept_term(value, extra_keep):
            continue
        if enforce_generic_filter and is_too_generic(entity_type, value):
            continue
        pattern = re.compile(re.escape(value), re.IGNORECASE)
        for match in pattern.finditer(text):
            index, end = match.start(), match.end()
            bounded = _has_boundaries(text, index, end)
            free = not any(
                index < other_end and end > other_start for other_start, other_end in occupied
            )
            if bounded and free:
                occupied.append((index, end))
                spans.append(
                    EntitySpan(
                        start=index,
                        end=end,
                        entity_type=entity_type,
                        score=score,
                        recognizer=recognizer,
                    )
                )
    return resolve_overlaps(spans)


def parse_entity_payload(raw: str) -> list[tuple[str, str]]:
    payload = _load_json(raw)
    mentions: list[tuple[str, str]] = []
    for item in payload.get("entities", []):
        mapped = _TYPE_MAP.get(str(item.get("type", "")).upper())
        text = str(item.get("text", "")).strip()
        if mapped and text:
            mentions.append((mapped, text))
    return mentions


def parse_residual_payload(raw: str) -> list[str]:
    payload = _load_json(raw)
    leftovers: list[str] = []
    for item in payload.get("leftovers", []):
        value = str(item).strip()
        if len(value) < 5 or value.startswith("[") or is_kept_term(value):
            continue
        leftovers.append(value)
    return leftovers


def _load_json(raw: str) -> dict:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)

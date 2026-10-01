"""Heuristica de cabecalho eproc/PJe/CDA: so ancora rotulada, sem varrer a peca."""

from __future__ import annotations

import re
from collections.abc import Iterable

from anonimizador_juridico import EntitySpan

from .gemini_detector import is_kept_term, is_too_generic, locate_mentions

# Capa/autuacao: corta antes da listagem de eventos e da peca propriamente dita.
_MAX_COVER_CHARS = 16_000
# Peticao inicial + primeira CDA, nao o restante dos autos.
_MAX_LABELED_CHARS = 80_000

_COVER_END = re.compile(
    r"(?im)^(?:##\s+Listagem dos Eventos\b|P[ÁA]GINA DE SEPARAC|##\s+Evento\s+\d+)"
)

_PARTY_LABELS = (
    "polo ativo",
    "polo passivo",
    "exequente",
    "executado",
    "executada",
    "embargante",
    "embargado",
    "embargada",
)

_INLINE_PARTY = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(polo\s+ativo|polo\s+passivo|exequente|"
    r"executad[oa]s?|embargante|embargad[oa]|advogad[oa]s?)\s*"
    r"(?:[:|]\s*|\n+\s*[:|]?\s*)"
    r"([^\n|]{5,220})"
)

_LAWYER_SPLIT = re.compile(
    r"(?i)\b(?:procurador(?:a|es|\(\s*es\s*\))?|advogad[oa]s?)\s*:\s*"
)

_DEVEDOR = re.compile(
    r"(?is)(?<!grande )(?:nome\s+do\s+)?devedor(?:a)?(?:\s+identifica[cç][aã]o)?\s*[:|]?\s+"
    r"([^\n|]{5,180}?)"
    r"(?=\s+(?:cgc|cnpj|cpf|endere[cç]o|cep|bairro|munic|uf|telefone|fone)\b"
    r"|\s*-{3,}|\s*\||$)"
)

_ENDERECO_LABEL = re.compile(
    r"(?is)"
    r"(?:"
    r"endere[cç]o(?!\s+eletr[oô]nico)(?:\s+telefone)?"
    r"|sediad[oa]s?"
    r"|estabelecid[oa]s?"
    r"|residente(?:\s+e\s+domiciliad[oa]s?)?"
    r"|domiciliad[oa]s?"
    r"|sede\s+para"
    r"|sede\s+(?:em|n[aoa]|à)"
    r"|passa\s+a\s+ser"
    r"|filial\s+(?:em|n[aoa]|à)"
    r"|alterad[oa]\s+para"
    r")\s*(?:à|á|a|em|n[aoa]|[:|])?\s+"
)

_STREET_VALUE = re.compile(
    r"(?ix)"
    r"("
    r"(?:rua|r\.|avenida|av\.|alameda|travessa|pra[cç]a|rodovia|estrada|logradouro)\s+"
    r"[A-Za-zÀ-ÿ0-9.'ºª\-]+(?:[\t\n ]+[A-Za-zÀ-ÿ0-9.'ºª\-]+){0,12}"
    r"(?:\s*,)?\s*(?:n[ºo°.]?\s*)?\d+[A-Za-z]?"
    r"|"
    r"r\s+[A-Za-zÀ-ÿ0-9.'\-]{2,40}\s+\d+"
    r")"
)

_COMPANY_MARK = re.compile(
    r"(?i)(?:\b(?:ltdame|ltda\.?|eireli|s/a|\bme\b|\bepp\b|companhia|\bcia\b|"
    r"associa[cç][aã]o|cooperativa)\b|s\.a\.?)"
)

_PESSOA_JURIDICA = re.compile(r"(?i)pessoa\s+jur[ií]dica")
_ROLE_TAIL = re.compile(
    r"(?i)\s*[-–—]?\s*(?:pessoa\s+jur[ií]dica|pessoa\s+f[ií]sica|entidade)\s*$"
)
_TOKEN_MARK = re.compile(r"\[[A-ZÁÉÍÓÚÂÊÔÃÕÇ_]+_\d+(?:·G\d+)?\]")
_PAREN_ID = re.compile(r"\((?:CNPJ|CGC|CPF)[^)]*\)", re.IGNORECASE)
_LABELED_ID = re.compile(r"(?i)\b(?:CNPJ|CGC|CPF)\s*:?\s*[\dA-Z./-]{8,}")
_OAB_COMPACT = re.compile(r"\b[A-Z]{2}\.?\s?\d{3,7}\b")
_OAB_LABELED = re.compile(r"(?i)\bOAB\s*/?\s*[A-Z]{2}\b[^,]{0,20}")
_LOGIN = re.compile(r"\b[A-Z]{2,}\.[A-Z0-9.]{2,}\b")
_ELETRONICO = re.compile(r"(?i)endere[cç]o\s+eletr[oô]nico")


class HeaderHeuristicEngine:
    """Ancoras da capa e linhas rotuladas da CDA. Nao substitui o GLiNER."""

    provider = "header"

    def __init__(self, *, extra_keep: Iterable[str] = ()) -> None:
        self.extra_keep = tuple(extra_keep)

    def detect(self, text: str) -> list[EntitySpan]:
        mentions = extract_header_mentions(text)
        return locate_mentions(
            text,
            mentions,
            extra_keep=self.extra_keep,
            recognizer="HeaderHeuristic",
            score=0.88,
        )

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []


def street_values(text: str) -> list[str]:
    """Vias com numero, para auditoria. Nao e o detector de endereco."""

    return [match.group(1).strip() for match in _STREET_VALUE.finditer(text)]


def extract_header_mentions(text: str) -> list[tuple[str, str]]:
    cover = _cover_window(text)
    labeled = text[: min(len(text), _MAX_LABELED_CHARS)]
    mentions: list[tuple[str, str]] = []
    mentions.extend(_mentions_from_tables(cover))
    mentions.extend(_mentions_from_inline_parties(cover))
    mentions.extend(_mentions_from_devedor(labeled))
    mentions.extend(_mentions_from_endereco(labeled))
    return _dedupe_mentions(mentions)


def _cover_window(text: str) -> str:
    prefix = text[: min(len(text), _MAX_COVER_CHARS)]
    match = _COVER_END.search(prefix)
    if match is None:
        return prefix
    return prefix[: match.start()]


def _mentions_from_tables(cover: str) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    for rows in _markdown_tables(cover):
        if len(rows) < 2:
            continue
        header = rows[0]
        data_rows = [row for row in rows[1:] if not _is_separator_row(row)]
        if not data_rows:
            continue
        for index, label in enumerate(header):
            kind = _party_kind(label)
            if kind is None:
                continue
            for row in data_rows:
                if index >= len(row):
                    continue
                mentions.extend(_mentions_from_party_cell(row[index], kind))
    return mentions


def _mentions_from_inline_parties(cover: str) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    for match in _INLINE_PARTY.finditer(cover):
        label, raw = match.group(1), match.group(2)
        kind = "PERSON" if _is_lawyer_label(label) else _party_kind(label) or "PERSON"
        mentions.extend(_mentions_from_party_cell(raw, kind))
    return mentions


def _mentions_from_party_cell(cell: str, default_kind: str) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    chunks = _LAWYER_SPLIT.split(cell)
    party = chunks[0] if chunks else cell
    lawyers = chunks[1:]
    cleaned = _clean_party_value(party)
    if cleaned:
        if _looks_like_company(party, cleaned):
            kind = "ORGANIZATION"
        elif default_kind == "PERSON":
            kind = "PERSON"
        else:
            kind = "PERSON"
        mentions.append((kind, cleaned))
    for blob in lawyers:
        for name in _lawyer_names(blob):
            mentions.append(("PERSON", name))
    return mentions


def _mentions_from_devedor(labeled: str) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    for match in _DEVEDOR.finditer(labeled):
        raw = match.group(1)
        cleaned = _clean_party_value(raw)
        if not cleaned:
            continue
        kind = "ORGANIZATION" if _looks_like_company(raw, cleaned) else "PERSON"
        mentions.append((kind, cleaned))
    return mentions


def _mentions_from_endereco(labeled: str) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    for match in _ENDERECO_LABEL.finditer(labeled):
        if _ELETRONICO.search(labeled[max(0, match.start() - 24) : match.end()]):
            continue
        snippet = labeled[match.end() : match.end() + 220]
        street = _STREET_VALUE.search(snippet)
        if street is None:
            continue
        original = street.group(1).strip()
        collapsed = re.sub(r"\s+", " ", original).strip()
        if is_too_generic("LOCATION", collapsed) or not re.search(r"\d", collapsed):
            continue
        mentions.append(("LOCATION", original))
        if collapsed != original:
            mentions.append(("LOCATION", collapsed))
    return mentions


def _looks_like_company(raw: str, cleaned: str) -> bool:
    return bool(_COMPANY_MARK.search(cleaned) or _PESSOA_JURIDICA.search(raw))


def _clean_party_value(value: str) -> str:
    cleaned = _TOKEN_MARK.sub(" ", value)
    cleaned = _PAREN_ID.sub(" ", cleaned)
    cleaned = _LABELED_ID.sub(" ", cleaned)
    cleaned = _LOGIN.sub(" ", cleaned)
    cleaned = _OAB_LABELED.sub(" ", cleaned)
    cleaned = _OAB_COMPACT.sub(" ", cleaned)
    cleaned = re.sub(r"[()]", " ", cleaned)
    cleaned = _ROLE_TAIL.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,;:|")
    words = [item for item in cleaned.split() if item]
    if len(words) < 2:
        return ""
    if is_kept_term(cleaned) or is_too_generic(
        "ORGANIZATION" if _COMPANY_MARK.search(cleaned) else "PERSON",
        cleaned,
    ):
        return ""
    return cleaned


def _lawyer_names(blob: str) -> list[str]:
    text = _TOKEN_MARK.sub(" ", blob)
    text = _LOGIN.sub(" ", text)
    text = _OAB_LABELED.sub(" | ", text)
    text = _OAB_COMPACT.sub(" | ", text)
    names: list[str] = []
    for chunk in re.split(r"[|;,/]", text):
        cleaned = re.sub(r"\s+", " ", chunk).strip(" -,;:.")
        if len(cleaned.split()) < 2:
            continue
        if is_kept_term(cleaned) or is_too_generic("PERSON", cleaned):
            continue
        names.append(cleaned)
    return names


def _party_kind(label: str) -> str | None:
    compact = re.sub(r"\s+", " ", label).strip().casefold()
    compact = compact.strip(":| ")
    if _is_lawyer_label(compact):
        return "PERSON"
    for item in _PARTY_LABELS:
        if compact == item or compact.startswith(item):
            return "ORGANIZATION"
    return None


def _is_lawyer_label(label: str) -> bool:
    compact = re.sub(r"\s+", " ", label).strip().casefold()
    return bool(re.match(r"advogad[oa]s?$", compact))


def _markdown_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if lines[index].lstrip().startswith("|"):
            block: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                block.append(lines[index])
                index += 1
            rows = [_table_cells(line) for line in block]
            if rows:
                tables.append(rows)
            continue
        index += 1
    return tables


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells)


def _dedupe_mentions(mentions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entity_type, value in mentions:
        key = (entity_type, value.casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append((entity_type, value))
    return unique

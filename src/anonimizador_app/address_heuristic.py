"""Heuristica deterministica de logradouro (Ciclo 3).

Varre via+numero em qualquer parte do documento e reconstitui o par
logradouro/numero do formulario da Receita. Nao mexe no GLiNER nem no
limiar 0,75. Rank de overlap igual ao HeaderHeuristic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from anonimizador_juridico import EntitySpan, resolve_overlaps

from .address_guard import initial_not_street
from .gemini_detector import is_kept_term

RECOGNIZER = "HeuristicaEndereco"
_SCORE = 0.90

# "R" sem ponto so abrevia Rua antes de palavra: "R E G I S T R O" (cabecalho espacado) e
# "R D ATIVA" (celula de tabela) nao sao logradouro.
_VIA = (
    r"(?:Rua|R\.|R\s+(?=[A-ZÁ-Ú]{2})|Avenida|Ave\.?|Av\.|Avn\.|Alameda|Travessa|Pra[cç]a|P[cç]\."
    r"|Rodovia|Rod\.|Estrada|Servid[ãa]o|Loteamento|Beco)"
)
_NUMBER_MARK = r"(?:n[º°o.]?\s*|n[uú]mero\s+)?"
# Nome do logradouro: nao atravessa celula de tabela ("|") nem vao de coluna (3+ espacos).
_NAME = r"(?:(?![^\S\n]{3})[^,\n|])"
_NAME_NO_DIGIT = r"(?:(?![^\S\n]{3})[^,\n|\d])"
# Predial com milhar ("2.350") e um numero so: parar no ponto deixava ".350" colado ao token.
_PREDIAL = r"\d{1,5}(?:\.\d{3})?"

_VIA_NUMBER = re.compile(
    r"(?ix)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    r"("
    rf"{_VIA}"
    r"\s*[A-ZÁ-Ú]"
    rf"{_NAME}{{2,80}}?"
    r"\s*(?:[,;.\-–—]\s*)?"
    rf"{_NUMBER_MARK}"
    r"(?<![A-Za-z0-9/])"
    rf"{_PREDIAL}"
    r")\b"
)

# H9-h: logradouro cujo NOME e um numero ("Rua 2070, 235, Centro", "Rua 1500, nº 10", "Avenida 3, 45"): via, numero do
# logradouro, virgula (ou ponto e virgula) e o numero predial. Sem o segundo numero nao e endereco ("Rua 12").
_VIA_NUMERADA = re.compile(
    r"(?ix)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    r"("
    rf"{_VIA}"
    r"\s*\d{1,5}"
    r"\s*[,;]\s*"
    rf"{_NUMBER_MARK}"
    r"(?<![A-Za-z0-9/])"
    rf"{_PREDIAL}"
    r")\b"
)

# H9-i: logradouro com nome proprio SEM numero predial ("Rua Fulano de Tal", "Estrada Municipal Fulano", "Rio Fulano",
# "Servidao Fulano", "Loteamento Fulano", "Linha Fulano", "bairro Fulano"): o logradouro inteiro, com o nome, e um
# endereco so; o nome de pessoa dentro dele nao vira PESSOA. A palavra de via aceita qualquer caixa, exceto as
# ambiguas com palavra comum (Rio, Linha, Praca), que exigem a inicial maiuscula ou a caixa alta.
_VIA_NOME_FIXA = (
    r"(?i:Rua|R\.|Avenida|Av\.|Avn\.|Travessa|Tv\.|Alameda|Rodovia|Rod\.|Servid[ãa]o|Loteamento|Beco|Bairro"
    r"|Estrada(?:[ \t]+(?:Municipal|Estadual|Federal|Geral|Vicinal|Rural))?)"
)
_VIA_NOME_AMBIGUA = (
    r"(?:Pra[cç]a|PRA[CÇ]A|Rio|RIO|Linha|LINHA|Gleba|GLEBA|Localidade|LOCALIDADE|Distrito|DISTRITO|Acesso|ACESSO)"
)
_NOME_CAIXA = "A-ZÁÀÂÃÉÊÍÓÔÕÚÇ"
_NOME_TITULO = r"(?:Dra?|Prof[a]?|Cel|Gen|Pres|Eng|Cap|Mal|Sen|Dep|Ver|Pe|Sto|Sta|Des|Min)\."
_NOME_PALAVRA = rf"(?:{_NOME_TITULO}|[{_NOME_CAIXA}][\wÀ-ÿ'’\-]*)"
_NOME_CONECTIVO = r"(?i:d[aeo]s?|e)"
# Palavra que abre outro campo ou outra via: o nome do logradouro para antes dela.
_NOME_PARADA = (
    r"(?i:Bairro|Cidade|Munic[ií]pio|Comarca|CEP|Complemento|N[uú]mero|Telefone|CPF|CNPJ|Estado|Distrito|Rua|Avenida"
    r"|Travessa|Alameda|Rodovia|Estrada|Beco|Loteamento|Servid[ãa]o|Rio|Linha|inscrit[oa]|portador[a]?|brasileir[oa]|residente|domiciliad[oa]"
    r"|Ltda|Epp|Eireli|Me)"
)
_VIA_NOME = re.compile(
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    rf"((?:{_VIA_NOME_FIXA}|{_VIA_NOME_AMBIGUA})"
    r"[ \t]+"
    rf"(?P<nome>(?!{_NOME_CONECTIVO}\b|{_NOME_PARADA}\b){_NOME_PALAVRA}"
    rf"(?:[ \t]+(?:{_NOME_CONECTIVO}[ \t]+)?(?!{_NOME_PARADA}\b){_NOME_PALAVRA}){{0,5}}))"
    r"(?![\wÀ-ÿ])",
    re.MULTILINE,
)
# Bairro de nome comum ("bairro Centro"): nao e logradouro de pessoa.
_NOME_GENERICO = frozenset("centro industrial comercial rural urbano velho novo nova alto baixo".split())
_CODIGO_DEPOIS = re.compile(r"[.,]?[ \t]*[A-Za-z]{0,3}\d{2,}[/\-]\d")
_VIA_COMUM = re.compile(r"(?i)^(?:pra[cç]a|rio|linha|gleba|localidade|distrito|acesso|beco)\b")

_VIA_ONLY = re.compile(
    rf"(?imx)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    r"("
    rf"{_VIA}"
    rf"\s+[A-ZÁ-Ú]{_NAME}{{3,80}}?"
    r")"
    r"\s+n[º°o.]?\s*$"
)

_VIA_EOL = re.compile(
    rf"(?imx)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    rf"({_VIA})"
    r"\s*$"
)

_VIA_PARTIAL = re.compile(
    rf"(?imx)"
    r"(?:(?<=^)|(?<=[\s,;:(]))"
    r"("
    rf"{_VIA}"
    rf"\s+[A-ZÁ-Ú]{_NAME_NO_DIGIT}{{1,60}}"
    r")"
    r"\s*$"
)

_CONTINUATION_NUMBER = re.compile(
    r"(?ix)"
    r"^\s*"
    r"("
    rf"[A-ZÁ-Ú]{_NAME}{{0,60}}?"
    r"(?:[,;.\-–—]\s*)?"
    rf"{_NUMBER_MARK}"
    r"(?<![A-Za-z0-9/])"
    rf"{_PREDIAL}"
    r")"
    r"\b"
)

_NEXT_NUMBER = re.compile(rf"^\s*({_PREDIAL})\b")

_FORM_LABEL = re.compile(
    r"^(?:Logradouro|Endere[cç]o(?:\s+Completo)?|RUA|AV\.|AVENIDA)\b",
    re.IGNORECASE,
)
_VIA_LINE = re.compile(
    r"^(?:RUA|R\.|R\s+|AV\.|AVENIDA|AVN\.|ALAMEDA|TRAVESSA|PRA[CÇ]A|P[CÇ]\.)\b",
    re.IGNORECASE,
)
_NUMBER_LABEL = re.compile(r"^N[uú]mero\b", re.IGNORECASE)
_NUMBER_MARKED = re.compile(r"^N[º°o.]?\s*[:.]?\s*(\d{1,5})\b", re.IGNORECASE)
_ISOLATED_NUMBER = re.compile(r"^(\d{2,5})\s*$")

_BARE_VIA = re.compile(
    r"^(?:rua|r\.|avenida|av\.|avn\.|alameda|travessa|pra[cç]a|p[cç]\.)\s*$",
    re.IGNORECASE,
)


class AddressHeuristicEngine:
    """Via+numero no corpo do documento e formulario Receita (logradouro / numero)."""

    provider = "address"

    def __init__(self, *, extra_keep: Iterable[str] = ()) -> None:
        self.extra_keep = tuple(extra_keep)

    def detect(self, text: str) -> list[EntitySpan]:
        spans = list(_spans_via_number(text, self.extra_keep))
        spans.extend(_spans_named_via(text, self.extra_keep))
        spans.extend(_spans_broken_number(text, self.extra_keep))
        spans.extend(_spans_wrapped_via(text, self.extra_keep))
        spans.extend(_spans_receita_form(text, self.extra_keep))
        return resolve_overlaps(spans)

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []


def _ok_span(text: str, start: int, end: int, extra_keep: Iterable[str], minimum: int = 8) -> bool:
    if end <= start or end > len(text):
        return False
    value = text[start:end]
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) < minimum:
        return False
    if initial_not_street(text, start):
        return False  # v0.4.11: "A. R. Souza Neto": o "R." e inicial de prenome, nao "Rua"
    if _BARE_VIA.match(collapsed):
        return False
    if is_kept_term(collapsed, extra_keep):
        return False
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    if line and is_kept_term(line, extra_keep):
        return False
    return True


def _make_span(start: int, end: int) -> EntitySpan:
    return EntitySpan(
        start=start,
        end=end,
        entity_type="LOCATION",
        score=_SCORE,
        recognizer=RECOGNIZER,
    )


def _spans_via_number(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    found: list[EntitySpan] = []
    for pattern in (_VIA_NUMBER, _VIA_NUMERADA):
        for match in pattern.finditer(text):
            start, end = match.start(1), match.end(1)
            if not _ok_span(text, start, end, extra_keep):
                continue
            found.append(_make_span(start, end))
    return found


def _spans_named_via(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    """Logradouro com nome proprio e sem numero predial (H9-i): "Rua Fulano de Tal", "Estrada Municipal Fulano", "Rio
    Fulano", "bairro Fulano". Quando o numero vem junto, o padrao via+numero (mais longo) vence."""

    from .geo_names import is_geo_name

    found: list[EntitySpan] = []
    for match in _VIA_NOME.finditer(text):
        start, end = match.start(1), match.end(1)
        value = re.sub(r"\s+", " ", text[start:end]).strip()
        name = match.group("nome")
        if sum(char.isalpha() for char in name) < 3:
            continue
        if _VIA_COMUM.match(value) and is_geo_name(value):
            continue  # "Rio Grande", "Rio Branco": municipio, nao logradouro
        if value.casefold().startswith("bairro") and name.casefold() in _NOME_GENERICO:
            continue
        if _CODIGO_DEPOIS.match(text, end):
            continue  # carimbo de cartorio ("Av. X de Y. B00/810."): o nome vem seguido de codigo, nao de endereco
        if not _ok_span(text, start, end, extra_keep, minimum=7):
            continue
        found.append(_make_span(start, end))
    return found


def _spans_broken_number(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    """Via termina em n° / nº e o predial esta na linha seguinte (Raposo Tavares)."""

    found: list[EntitySpan] = []
    for match in _VIA_ONLY.finditer(text):
        tail = text[match.end() :]
        number = _NEXT_NUMBER.match(tail)
        if number is None:
            continue
        start = match.start(1)
        end = match.end() + number.end()
        if not _ok_span(text, start, end, extra_keep):
            continue
        found.append(_make_span(start, end))
    return found


_NUMBER_CUE = re.compile(
    r"(?ix)(?:n[º°o.]|n[uú]mero|[,;.\-–—])\s*\d{1,5}\b"
)


def _spans_wrapped_via(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    """Via no fim da linha e nome+numero na seguinte (a Rua / Raposo Tavares n° 281)."""

    found: list[EntitySpan] = []
    for pattern in (_VIA_EOL, _VIA_PARTIAL):
        for match in pattern.finditer(text):
            cont = _CONTINUATION_NUMBER.match(text[match.end() :])
            if cont is None or not _NUMBER_CUE.search(cont.group(1)):
                continue
            start = match.start(1)
            end = match.end() + cont.end(1)
            if not _ok_span(text, start, end, extra_keep):
                continue
            found.append(_make_span(start, end))
    return found


def _iter_lines(text: str) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    cursor = 0
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        rows.append((cursor, cursor + len(body), body))
        cursor += len(raw)
    return rows


def _next_nonempty(
    rows: list[tuple[int, int, str]], start: int, *, limit: int = 8
) -> tuple[int, int, int, str] | None:
    seen = 0
    index = start
    while index < len(rows) and seen < limit:
        line_start, line_end, body = rows[index]
        stripped = body.strip()
        if stripped:
            return index, line_start, line_end, stripped
        seen += 1
        index += 1
    return None


def _looks_like_street(value: str) -> bool:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if _BARE_VIA.match(collapsed):
        return False
    if _VIA_LINE.match(collapsed) and not re.search(r"\d", collapsed):
        return True
    return False


def _spans_receita_form(text: str, extra_keep: Iterable[str]) -> list[EntitySpan]:
    """Logradouro numa linha, numero na seguinte (formulario Receita / QSA)."""

    rows = _iter_lines(text)
    found: list[EntitySpan] = []
    index = 0
    while index < len(rows):
        _line_start, _line_end, body = rows[index]
        stripped = body.strip()
        street_start = street_end = -1
        if _FORM_LABEL.match(stripped) and len(stripped.split()) <= 4:
            nxt = _next_nonempty(rows, index + 1)
            if nxt is not None and _looks_like_street(nxt[3]):
                _sidx, street_start, street_end, _sbody = nxt
        elif _looks_like_street(stripped):
            street_start = _line_start + (len(body) - len(body.lstrip()))
            street_end = _line_end
        if street_start < 0:
            index += 1
            continue
        number_hit = _find_form_number(rows, index + 1)
        if number_hit is None:
            index += 1
            continue
        _nidx, _nstart, number_end = number_hit
        if not _ok_span(text, street_start, number_end, extra_keep):
            index += 1
            continue
        found.append(_make_span(street_start, number_end))
        index = max(index + 1, _nidx + 1)
    return found


def _find_form_number(
    rows: list[tuple[int, int, str]], start: int
) -> tuple[int, int, int] | None:
    seen = 0
    index = start
    expect_isolated = False
    while index < len(rows) and seen < 10:
        line_start, line_end, body = rows[index]
        stripped = body.strip()
        if not stripped:
            index += 1
            seen += 1
            continue
        marked = _NUMBER_MARKED.match(stripped)
        if marked:
            abs_start = line_start + body.find(marked.group(1))
            return index, abs_start, abs_start + len(marked.group(1))
        if _NUMBER_LABEL.match(stripped):
            expect_isolated = True
            index += 1
            seen += 1
            continue
        isolated = _ISOLATED_NUMBER.match(stripped)
        if isolated and expect_isolated:
            abs_start = line_start + body.find(isolated.group(1))
            return index, abs_start, abs_start + len(isolated.group(1))
        if expect_isolated:
            return None
        index += 1
        seen += 1
    return None


# v0.4.15: complemento e bairro logo depois do endereco ja trocado ("[ENDERECO_1], apto. 602", ", sala 5", ", Galpao C 07",
# ", bairro Centro"): entram no mesmo token. O complemento e a palavra-gatilho e ate 3 palavras curtas de letras e
# digitos; o bairro, a palavra "bairro" e ate 5 palavras de nome. O endereco vizinho separado so por virgula (o bairro
# que o detector ja trocou a parte) tambem entra.
_TAIL_SEPARATOR = r"[ \t]*(?:,|[-–](?=[ \t\n]))[ \t]*\n?[ \t]*"
_TAIL_GAP = r"(?:[ \t]+|[ \t]*\n[ \t]*)"
_TAIL_COMPLEMENT = re.compile(
    _TAIL_SEPARATOR
    + r"(?i:casa|sala|salas|sl\.|apto\.?|apartamento|ap\.|conjunto|conj\.|cj\.|galp[ãa]o|bloco|bl\.|loja|lj\.|box"
    r"|andar|lote|quadra|qd\.|fundos|t[ée]rreo|sobreloja)(?![^\W\d_])"
    r"(?:(?:" + _TAIL_GAP + r")?(?:n[º°o]\.?[ \t]*)?(?:\d[\w/-]{0,5}|[A-Z](?:\d[\w/-]{0,4})?)(?![\w/-])){0,3}"
)
_TAIL_DISTRICT = re.compile(
    _TAIL_SEPARATOR
    + r"(?i:bairro)[ \t]*:?" + _TAIL_GAP + r"[A-ZÀ-Ý0-9][^\W_]*(?:" + _TAIL_GAP
    + r"(?:(?i:d[aeo]s?|e)[ \t]+)?[A-ZÀ-Ý0-9][^\W_]*){0,4}"
)
_TAIL_JOIN = re.compile(_TAIL_SEPARATOR + r"$")


def extend_address_tails(text: str, spans: Iterable[EntitySpan]) -> list[EntitySpan]:
    """O span de endereco (LOCATION) estendido sobre o complemento, o bairro e o endereco vizinho colado por virgula."""

    from dataclasses import replace

    ordered = sorted(spans, key=lambda span: (span.start, span.end))
    dropped: set[int] = set()
    result: list[EntitySpan] = []
    for index, span in enumerate(ordered):
        if index in dropped:
            continue
        if span.entity_type != "LOCATION":
            result.append(span)
            continue
        end = span.end
        for _ in range(6):
            match = _TAIL_COMPLEMENT.match(text, end) or _TAIL_DISTRICT.match(text, end)
            if match is not None and match.end() > end:
                blocked = any(
                    other.start < match.end() and end < other.end
                    for position, other in enumerate(ordered)
                    if position != index and position not in dropped
                )
                if not blocked:
                    end = match.end()
                    continue
            nearby = next(
                (
                    position
                    for position in range(index + 1, len(ordered))
                    if position not in dropped
                    and ordered[position].start >= end
                    and ordered[position].start - end <= 6
                ),
                None,
            )
            if (
                nearby is not None
                and ordered[nearby].entity_type == "LOCATION"
                and _TAIL_JOIN.fullmatch(text[end : ordered[nearby].start])
                and text[end : ordered[nearby].start].strip()
            ):
                dropped.add(nearby)
                end = ordered[nearby].end
                continue
            break
        result.append(replace(span, end=end) if end != span.end else span)
    return result

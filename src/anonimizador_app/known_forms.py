"""Passada final de formas conhecidas (H9-h): todo ID que o mapa ja tem e trocado em todas as ocorrencias do texto.

Medido no lote IDPJ real depois da H9-g: o numero do processo principal ficava em claro em 13 rodapes e o valor do
NIRE ficava em claro depois do rotulo "Arquivamento", embora as duas formas ja estivessem no mapa: o reconhecedor
exige rotulo ou contexto, e a propagacao do lote so cobria alguns tipos (placa, chassi, RENAVAM, e-mail). Um ID que
o mapa ja conhece, escrito igual no resto do texto, nao precisa de rotulo: o valor ja foi provado com rotulo.

Vale para ID (processo CNJ, NIRE, CNPJ, CPF, PIS, CNH, RENAVAM, placa, chassi, e-mail, chave PIX, codigo do eproc):
os digitos do valor conhecido, com ou sem pontuacao e com o mesmo numero partido por uma quebra de linha (processo),
fora de numero maior. Nome de pessoa, de empresa e endereco NAO entram aqui: esses ja tem a propagacao do elenco, que
respeita as entradas "manter" e as travas de termo protegido; uma passada cega por nome reabriria a super-redacao.
Spans que ja existem no texto (qualquer tipo) nao sao tocados; so o que esta fora deles. Termo da lista de exclusoes
do usuario continua protegido.

H9-k: RG (7+ digitos, com ou sem pontuacao e DV) e CEP (forma pontuada em qualquer lugar; 8 digitos corridos so em
contexto de endereco ou na coluna CEP) tambem entram; ver ``_SHORT_TYPES``.

H9-i lote B1: os processos, CDAs e contatos listados na aba Caso (``case_list.CaseRules``) tambem entram aqui, em toda
ocorrencia e antes das demais formas; o processo da lista vale ate dentro de ementa.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import canonicalize
from anonimizador_juridico.validators import only_digits
from anonimizador_juridico.recognizers import _CDA_PGFN, _CNJ_FORMATADO, column_header

from .batch_propagation import _id_pattern
from .fidelity import drop_protected_spans

RECOGNIZER = "FormaConhecida"
_SCORE = 0.93
# Tipos cujo valor e uma sequencia de digitos longa: o numero igual, com a pontuacao que for, e o mesmo ID.
_NUMERIC = {
    "BR_PROCESSO": 20,
    "BR_CNPJ": 14,
    "BR_CPF": 11,
    "BR_NIRE": 11,
    "BR_PIS": 11,
    "BR_CNH": 11,
    "BR_RENAVAM": 9,
    "BR_PROCESSO_ADMINISTRATIVO": 15,
    "BR_CDA": 9,
}
_PATTERN_TYPES = frozenset({"BR_PLACA", "BR_CHASSI", "EMAIL_ADDRESS", "BR_OAB"})
# H9-k: RG e CEP conhecidos tambem sao trocados em toda ocorrencia, mas com forma propria (sao curtos: 7 a 10 e 8
# digitos, abaixo do minimo de 9 da corrida de digitos generica).
#  - RG: so o RG conhecido com 7+ digitos; vale com ou sem pontuacao ("1.234.567-8", "1234567-8", "12345678") e com ou
#    sem o DV no fim ("1.234.567" quando o mapa tem "1.234.567-8", e o contrario); sem DV so na forma pontuada, que e
#    a do RG (sete digitos corridos sem pontuacao podem ser outra coisa).
#  - CEP: a forma pontuada do CEP ("88.330-000", "88330-000") vale sozinha; os 8 digitos sem hifen ("88330000",
#    "88.330.000") so em contexto de endereco (rotulo CEP, logradouro, bairro, municipio, UF colada) ou na coluna CEP:
#    oito digitos corridos tambem sao data, protocolo, conta.
_SHORT_TYPES = {"BR_RG": 7, "BR_CEP": 8}
_RG_KNOWN = re.compile(
    r"(?<![\d.\-/,])(?<!R\$ )(?<!R\$)\d{1,3}(?:\.?\d{3}){2}(?:\.?\d{3})?(?:[\-.]?[0-9Xx])?(?![\dA-Za-z]|[.,\-/]\d)"
)
_CEP_FORMATTED = re.compile(r"(?<![\d.\-/])\d{2}\.?\d{3}[ \t]?-[ \t]?\d{3}(?!\d|[.,\-/]\d)")
_CEP_PLAIN = re.compile(r"(?<![\d.\-/,])\d{2}\.?\d{3}\.?\d{3}(?!\d|[.,\-/]\d)")
_ADDRESS_CONTEXT = re.compile(
    r"(?i)(?:\bCEP\b|\brua\b|\bav(?:enida|\.)|\btravessa\b|\brodovia\b|\bestrada\b|\bbairro\b|\bmunic[ií]pio\b"
    r"|\blogradouro\b|\bendere[cç]o\b|\bcidade\b|\bloteamento\b|\bn[º°]\s*\d)"
)
_UF_BEFORE = re.compile(
    r"(?:^|[\s,(/\-–])(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)"
    r"[\s,\-–/]{1,4}$"
)
_MONEY_BEFORE = re.compile(r"R\$\s*$")


def _cep_in_address(text: str, start: int, end: int) -> bool:
    """Os 8 digitos sem hifen so sao CEP em contexto de endereco: rotulo CEP, palavra de logradouro na mesma linha
    antes do numero, UF colada antes, ou a coluna CEP (Markdown ou bloco alinhado)."""

    line_start = text.rfind("\n", 0, start) + 1
    before = text[max(line_start, start - 60) : start]
    # So o trecho da mesma clausula: corta no ultimo ";", "|" ou ponto final antes do numero.
    before = re.split(r"[;|]|\.\s", before)[-1]
    if re.search(r"(?i)\bCEP\b[^\d\n]{0,12}$", before) or _UF_BEFORE.search(before):
        return True
    if _ADDRESS_CONTEXT.search(before):
        return True
    header = column_header(text, start, end)
    return header is not None and re.search(r"(?i)\bCEP\b", header) is not None
_MIN_DIGITS = 9
# Digitos com a pontuacao do documento no meio; nunca espaco (dois numeros vizinhos nao viram um).
_RUN = re.compile(r"(?<!\d)\d(?:[.\-/]?\d){8,24}(?!\d)")
_CNJ = re.compile(rf"(?<!\d)(?:{_CNJ_FORMATADO})(?!\d)")
# NIRE escrito com espacos entre os grupos ("42 2 0123456 7"); v0.4.14: ou partido por uma quebra de linha no carimbo
# ("Empresa: 42 2" na linha de cima, "0123456 7" na de baixo). So o valor ja conhecido.
_NIRE_SPACED = re.compile(r"(?<!\d)\d(?:(?:[ .\-]|[ \t]*\n[ \t]*)?\d){10}(?!\d)")
# CDA da PGFN escrita com espacos ("80 2 19 012345-67"): o numero conhecido vale em qualquer grafia (H9-i).
_CDA_SPACED = re.compile(rf"(?<![\d.\-/])(?:{_CDA_PGFN})(?!\d|[.\-/]\d)")


def _oab_pattern(canonical: str) -> re.Pattern[str] | None:
    """Inscricao da OAB ja conhecida ("SC12345B", ou so "12345"): "OAB/SC 12.345", "OAB SC nº 12345-B", "12345/SC"
    voltam ao mesmo token na passada final (H9-i). Exige "OAB" ou a UF junto, nunca o numero solto."""

    match = re.fullmatch(r"([A-Z]{2})?(\d+)([A-Z]?)", canonical)
    if match is None:
        return None
    uf, digits, letter = match.groups()
    number = r"\.?".join(digits)
    suffix = rf"(?:\s*-?\s*{letter})?" if letter else ""
    parts = []
    if uf:
        parts.append(rf"OAB\s*[/\-]?\s*{uf}\s*(?:n[º°o.]*\s*)?:?\s*-?\s*{number}{suffix}")
        parts.append(rf"(?:OAB\s+)?{number}{suffix}\s*[/\-]\s*{uf}")
    else:
        parts.append(rf"OAB\s*(?:n[º°o.]*\s*)?:?\s*{number}{suffix}")
    return re.compile(r"(?<![\w.])(?:" + "|".join(parts) + r")(?![\w]|\.\d)", re.IGNORECASE)


class KnownForms:
    """IDs que o mapa (e os arquivos do lote) ja conhecem: ``(tipo, canonico)`` -> grafias vistas."""

    def __init__(self) -> None:
        self._originals: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._digits: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self._patterns: dict[tuple[str, str], re.Pattern[str] | None] = {}
        self._short: dict[str, dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
        self.counts: Counter[str] = Counter()
        self._case = None

    def add_case(self, rules: object | None) -> None:
        """Regras do caso (``CaseRules``): o valor listado e trocado em todo o texto, sem rotulo."""

        if not rules:
            return
        self._case = rules
        for entity_type, canonical in getattr(rules, "values", ()):
            self.add(entity_type, canonical, "")

    def add(self, entity_type: str, canonical: str, original: str = "") -> None:
        if entity_type in _SHORT_TYPES:
            self._add_short(entity_type, canonical, original)
            return
        if entity_type not in _NUMERIC and entity_type not in _PATTERN_TYPES:
            return
        canonical = canonical or canonicalize(entity_type, original)
        if not canonical:
            return
        key = (entity_type, canonical)
        self._originals[key].add(original)
        if entity_type in _NUMERIC and canonical.isdigit() and len(canonical) >= max(_MIN_DIGITS, _NUMERIC[entity_type]):
            self._digits[canonical].add(key)

    def _add_short(self, entity_type: str, canonical: str, original: str) -> None:
        canonical = canonical or canonicalize(entity_type, original)
        body = canonical[:-1] if canonical.endswith("X") else canonical
        if not body.isdigit() or len(body) < _SHORT_TYPES[entity_type]:
            return
        if entity_type == "BR_CEP" and len(canonical) != 8:
            return
        if entity_type == "BR_RG" and len(body) > 12:
            return
        key = (entity_type, canonical)
        self._originals[key].add(original)
        self._short[entity_type][canonical].add(key)

    def add_mapping(self, mapping: object | None) -> None:
        for record in getattr(mapping, "records", ()) or ():
            for original in record.originals or [""]:
                self.add(record.entity_type, record.canonical, original)

    def add_spans(self, text: str, spans: Iterable[EntitySpan]) -> None:
        for span in spans:
            if span.entity_type in _NUMERIC or span.entity_type in _PATTERN_TYPES or span.entity_type in _SHORT_TYPES:
                value = span.value_from(text)
                self.add(span.entity_type, span.canonical_key or canonicalize(span.entity_type, value), value)

    def __len__(self) -> int:
        return len(self._originals)

    def spans(
        self,
        text: str,
        taken: Sequence[EntitySpan],
        exclusions: Sequence[str] = (),
        source_name: str = "",
    ) -> list[EntitySpan]:
        """As ocorrencias de ID conhecido fora dos spans ja marcados (``taken``)."""

        if not self._originals and self._case is None:
            return []
        occupied = sorted((span.start, span.end) for span in taken)
        found: list[EntitySpan] = []
        listed: list[EntitySpan] = []

        def free(start: int, end: int) -> bool:
            for other_start, other_end in occupied:
                if other_start >= end:
                    break
                if other_end > start and other_start < end:
                    return False
            return not any(item.start < end and start < item.end for item in found)

        def take(start: int, end: int, key: tuple[str, str]) -> None:
            if free(start, end):
                found.append(
                    EntitySpan(
                        start=start, end=end, entity_type=key[0], score=_SCORE, recognizer=RECOGNIZER,
                        canonical_key=key[1],
                    )
                )

        if self._case is not None:
            for span in self._case.detect(text):
                if free(span.start, span.end):
                    found.append(span)
                    listed.append(span)
        for pattern in (_RUN, _CNJ, _NIRE_SPACED, _CDA_SPACED):
            for match in pattern.finditer(text):
                digits = re.sub(r"\D", "", match.group())
                keys = self._digits.get(digits)
                if keys is not None and len(keys) == 1:
                    take(match.start(), match.end(), next(iter(keys)))
        for key in self._originals:
            if key[0] not in _PATTERN_TYPES:
                continue
            pattern = self._pattern(key)
            if pattern is None:
                continue
            for match in pattern.finditer(text):
                take(match.start(), match.end(), key)
        for start, end, key in self._short_hits(text):
            take(start, end, key)
        # O numero de processo que o mapa ja conhece e o do caso: a regra do "precedente" (ementa, citacao) so vale
        # para numero novo, e foi ela que deixou o processo principal em claro nos rodapes. Os demais tipos seguem
        # as travas de fidelidade (CNPJ publico, CPF em contexto de NIRE ou protocolo, termo da lista de exclusoes).
        processes = [span for span in found if span.entity_type == "BR_PROCESSO" or span in listed]
        others = drop_protected_spans(
            text,
            [span for span in found if span.entity_type != "BR_PROCESSO" and span not in listed],
            exclusions,
            source_name=source_name,
        )
        found = sorted([*processes, *others], key=lambda span: span.start)
        self.counts.update(span.entity_type for span in found)
        return found

    def _unique(self, entity_type: str, canonical: str) -> tuple[str, str] | None:
        keys = self._short.get(entity_type, {}).get(canonical)
        return next(iter(keys)) if keys is not None and len(keys) == 1 else None

    def _short_hits(self, text: str) -> list[tuple[int, int, tuple[str, str]]]:
        """RG e CEP conhecidos (H9-k), nas formas descritas em ``_SHORT_TYPES``."""

        hits: list[tuple[int, int, tuple[str, str]]] = []
        ceps = self._short.get("BR_CEP")
        rgs = self._short.get("BR_RG")
        if ceps:
            for match in _CEP_FORMATTED.finditer(text):
                key = self._unique("BR_CEP", only_digits(match.group()))
                if key is not None:
                    hits.append((match.start(), match.end(), key))
        if rgs:
            for match in _RG_KNOWN.finditer(text):
                value = match.group()
                if _MONEY_BEFORE.search(text, max(0, match.start() - 4), match.start()):
                    continue
                canonical = canonicalize("BR_RG", value)
                key = self._unique("BR_RG", canonical)
                dotted = "." in value
                has_dv = re.search(r"-[0-9Xx]$|[Xx]$", value) is not None
                if key is None and dotted and not has_dv:
                    # A ocorrencia sem DV ("1.234.567") de um RG que o mapa tem com DV ("1.234.567-8").
                    found = {k for c, ks in rgs.items() if len(c) == len(canonical) + 1 and c.startswith(canonical) for k in ks}
                    key = next(iter(found)) if len(found) == 1 else None
                if key is None and has_dv and len(canonical) - 1 >= _SHORT_TYPES["BR_RG"]:
                    # A ocorrencia com DV ("1.234.567-8") de um RG que o mapa tem sem DV ("1.234.567").
                    key = self._unique("BR_RG", canonical[:-1])
                if key is None:
                    continue
                if not dotted and not has_dv and ceps and canonical in ceps:
                    continue  # 8 digitos corridos que tambem sao um CEP conhecido: ambiguo, fica para o CEP (contexto)
                hits.append((match.start(), match.end(), key))
        if ceps:
            for match in _CEP_PLAIN.finditer(text):
                key = self._unique("BR_CEP", only_digits(match.group()))
                if key is not None and _cep_in_address(text, match.start(), match.end()):
                    hits.append((match.start(), match.end(), key))
        return hits

    def _pattern(self, key: tuple[str, str]) -> re.Pattern[str] | None:
        if key not in self._patterns:
            self._patterns[key] = _oab_pattern(key[1]) if key[0] == "BR_OAB" else _id_pattern(*key)
        return self._patterns[key]

    def audit_line(self) -> str:
        total = sum(self.counts.values())
        detail = ", ".join(f"{name} {count}" for name, count in sorted(self.counts.items()))
        return (
            f"Formas conhecidas do mapa substituidas na passada final: {total}"
            + (f" ({detail})" if detail else "")
            + f"; formas conhecidas: {len(self)}"
        )

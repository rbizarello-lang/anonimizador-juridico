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
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import canonicalize
from anonimizador_juridico.recognizers import _CNJ_FORMATADO

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
}
_PATTERN_TYPES = frozenset({"BR_PLACA", "BR_CHASSI", "EMAIL_ADDRESS"})
_MIN_DIGITS = 9
# Digitos com a pontuacao do documento no meio; nunca espaco (dois numeros vizinhos nao viram um).
_RUN = re.compile(r"(?<!\d)\d(?:[.\-/]?\d){8,24}(?!\d)")
_CNJ = re.compile(rf"(?<!\d)(?:{_CNJ_FORMATADO})(?!\d)")
# NIRE escrito com espacos entre os grupos ("42 2 0123456 7").
_NIRE_SPACED = re.compile(r"(?<!\d)\d(?:[ .\-]?\d){10}(?!\d)")


class KnownForms:
    """IDs que o mapa (e os arquivos do lote) ja conhecem: ``(tipo, canonico)`` -> grafias vistas."""

    def __init__(self) -> None:
        self._originals: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._digits: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self._patterns: dict[tuple[str, str], re.Pattern[str] | None] = {}
        self.counts: Counter[str] = Counter()

    def add(self, entity_type: str, canonical: str, original: str = "") -> None:
        if entity_type not in _NUMERIC and entity_type not in _PATTERN_TYPES:
            return
        canonical = canonical or canonicalize(entity_type, original)
        if not canonical:
            return
        key = (entity_type, canonical)
        self._originals[key].add(original)
        if entity_type in _NUMERIC and canonical.isdigit() and len(canonical) >= max(_MIN_DIGITS, _NUMERIC[entity_type]):
            self._digits[canonical].add(key)

    def add_mapping(self, mapping: object | None) -> None:
        for record in getattr(mapping, "records", ()) or ():
            for original in record.originals or [""]:
                self.add(record.entity_type, record.canonical, original)

    def add_spans(self, text: str, spans: Iterable[EntitySpan]) -> None:
        for span in spans:
            if span.entity_type in _NUMERIC or span.entity_type in _PATTERN_TYPES:
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

        if not self._originals:
            return []
        occupied = sorted((span.start, span.end) for span in taken)
        found: list[EntitySpan] = []

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

        for pattern in (_RUN, _CNJ, _NIRE_SPACED):
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
        # O numero de processo que o mapa ja conhece e o do caso: a regra do "precedente" (ementa, citacao) so vale
        # para numero novo, e foi ela que deixou o processo principal em claro nos rodapes. Os demais tipos seguem
        # as travas de fidelidade (CNPJ publico, CPF em contexto de NIRE ou protocolo, termo da lista de exclusoes).
        processes = [span for span in found if span.entity_type == "BR_PROCESSO"]
        others = drop_protected_spans(
            text, [span for span in found if span.entity_type != "BR_PROCESSO"], exclusions, source_name=source_name
        )
        found = sorted([*processes, *others], key=lambda span: span.start)
        self.counts.update(span.entity_type for span in found)
        return found

    def _pattern(self, key: tuple[str, str]) -> re.Pattern[str] | None:
        if key not in self._patterns:
            self._patterns[key] = _id_pattern(*key)
        return self._patterns[key]

    def audit_line(self) -> str:
        total = sum(self.counts.values())
        detail = ", ".join(f"{name} {count}" for name, count in sorted(self.counts.items()))
        return (
            f"Formas conhecidas do mapa substituidas na passada final: {total}"
            + (f" ({detail})" if detail else "")
            + f"; formas conhecidas: {len(self)}"
        )

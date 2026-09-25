from __future__ import annotations

from collections.abc import Iterable

from presidio_analyzer import PatternRecognizer

from .recognizers import build_structured_recognizers
from .types import EntitySpan


_TYPE_PRIORITY = {
    "BR_CPF": 100,
    "BR_CNPJ": 100,
    "BR_PROCESSO": 100,
    "EMAIL_ADDRESS": 90,
    "BR_OAB": 85,
    "BR_RG": 85,
    "BR_MATRICULA_IMOVEL": 85,
    "BR_PIX": 82,
    "BR_CONTA_BANCARIA": 82,
    "BR_RENAVAM": 82,
    "BR_PLACA": 82,
    "BR_CHASSI": 82,
    "BR_NASCIMENTO": 82,
    "BR_TITULO_ELEITOR": 82,
    "BR_PIS": 82,
    "BR_EPROC": 82,
    "BR_CEP": 80,
    "BR_PHONE": 70,
    "PERSON": 55,
    "ORGANIZATION": 52,
    "LOCATION": 45,
}

# Desempate por FONTE antes de tipo/score (P13). IDs Presidio ficam acima desta lista.
SOURCE_RECOGNIZER_RANK = {
    "ListaDeAlvos": 500,
    "CnpjLookup": 400,
    "HeaderHeuristic": 300,
    "HeuristicaEndereco": 300,
    "FormFields": 300,
    "EprocCodes": 300,
    "NerLocal": 200,
    "GeminiContextual": 100,
}
SOURCE_RANK_STRUCTURED = 600
SOURCE_RANK_LABELED_FORM = 650
SOURCE_RANK_OTHER = 0
_CONTEXTUAL_TYPES = frozenset({"PERSON", "ORGANIZATION", "LOCATION"})
_FORM_ID_TYPES = frozenset({"BR_NASCIMENTO", "BR_TITULO_ELEITOR"})


class StructuredDetector:
    """Executa reconhecedores Presidio sem inicializar um motor NLP."""

    def __init__(self, recognizers: Iterable[PatternRecognizer] | None = None) -> None:
        self.recognizers = list(recognizers or build_structured_recognizers())

    def detect(self, text: str) -> list[EntitySpan]:
        candidates: list[EntitySpan] = []
        for recognizer in self.recognizers:
            entity_type = recognizer.supported_entities[0]
            for result in recognizer.analyze(
                text=text,
                entities=[entity_type],
                nlp_artifacts=None,
            ):
                candidates.append(
                    EntitySpan(
                        start=result.start,
                        end=result.end,
                        entity_type=result.entity_type,
                        score=result.score,
                        recognizer=recognizer.name,
                    )
                )
        return resolve_overlaps(candidates)


def source_rank(span: EntitySpan) -> int:
    """Prioridade da fonte: IDs > ListaDeAlvos > CnpjLookup > Header > NerLocal > Gemini."""

    if span.recognizer == "FormFields" and span.entity_type in _FORM_ID_TYPES:
        return SOURCE_RANK_LABELED_FORM
    named = SOURCE_RECOGNIZER_RANK.get(span.recognizer)
    if named is not None:
        return named
    if span.recognizer.endswith("Recognizer") or "Fallback" in span.recognizer:
        return SOURCE_RANK_STRUCTURED
    if span.entity_type not in _CONTEXTUAL_TYPES:
        return SOURCE_RANK_STRUCTURED
    return SOURCE_RANK_OTHER


def _rank(span: EntitySpan) -> tuple[int, int, float, int, int]:
    return (
        source_rank(span),
        _TYPE_PRIORITY.get(span.entity_type, 40),
        span.score,
        span.end - span.start,
        -span.start,
    )


def _overlap(left: EntitySpan, right: EntitySpan) -> bool:
    return left.start < right.end and right.start < left.end


def resolve_overlaps(candidates: Iterable[EntitySpan]) -> list[EntitySpan]:
    """Mantem a entidade de maior prioridade em cada regiao sobreposta.

    Ordem de desempate: fonte (IDs Presidio > ListaDeAlvos > CnpjLookup >
    HeaderHeuristic/HeuristicaEndereco > NerLocal > GeminiContextual > outros),
    depois tipo, score, comprimento e inicio. Empate na mesma fonte preserva o
    criterio antigo (tipo → score → comprimento → inicio).
    """

    selected: list[EntitySpan] = []
    unique = {
        (span.start, span.end, span.entity_type, span.recognizer): span
        for span in candidates
    }.values()

    for candidate in sorted(unique, key=_rank, reverse=True):
        if any(_overlap(candidate, existing) for existing in selected):
            continue
        selected.append(candidate)

    return sorted(selected, key=lambda span: (span.start, span.end, span.entity_type))

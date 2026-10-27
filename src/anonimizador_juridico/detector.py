from __future__ import annotations

import re
from bisect import bisect_left
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
    "BR_PIX": 82,
    "BR_CONTA_BANCARIA": 82,
    "BR_RENAVAM": 82,
    "BR_PLACA": 82,
    "BR_CHASSI": 82,
    "BR_NASCIMENTO": 82,
    "BR_TITULO_ELEITOR": 82,
    "BR_PIS": 82,
    "BR_CTPS": 82,
    # Identificador rotulado vence o CPF/telefone que os mesmos digitos poderiam ser (NIRE e CNH tem
    # 11 digitos como o CPF).
    "BR_NIRE": 101,
    "BR_CNH": 101,
    # H9-f: URL com chave vence processo CNJ e CPF que ela traga na query (senao sobra &chave= em claro).
    "BR_URL": 102,
    # H9-i: numero de CDA e de processo administrativo vence o que os mesmos digitos poderiam ser (telefone, CPF).
    "BR_CDA": 101,
    "BR_REGISTRO_CIVIL": 101,
    "BR_CADASTRO_RURAL": 101,
    "BR_REGISTRO_JUNTA": 103,
    "BR_IP": 101,
    "BR_PROCESSO_ADMINISTRATIVO": 101,
    "BR_EPROC": 82,
    "BR_CEP": 80,
    "BR_PHONE": 70,
    "PERSON": 55,
    "ORGANIZATION": 52,
    "LOCATION": 45,
}

# Desempate por FONTE antes de tipo/score (P13). IDs Presidio ficam acima desta lista.
SOURCE_RECOGNIZER_RANK = {
    "ListaDoCaso": 700,
    "ListaDeAlvos": 500,
    # H9-o: nome de pessoa (NER) que venceu o pedaco de um listado (``target_list.yield_partial_to_other_person``).
    "NerAncoradoLista": 450,
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
        """Roda os reconhecedores bloco a bloco (fronteira = ancora de pagina).

        O ``remove_duplicates`` do Presidio e quadratico no numero de achados
        de cada reconhecedor; no processo inteiro de uma vez (milhares de CPFs)
        custava minutos. Nenhum ID atravessa a ancora ``<!-- pagina-origem -->``,
        entao o resultado e o mesmo do texto inteiro.
        """

        candidates: list[EntitySpan] = []
        for offset, block in _page_blocks(text):
            for recognizer in self.recognizers:
                entity_type = recognizer.supported_entities[0]
                for result in recognizer.analyze(
                    text=block,
                    entities=[entity_type],
                    nlp_artifacts=None,
                ):
                    candidates.append(
                        EntitySpan(
                            start=offset + result.start,
                            end=offset + result.end,
                            entity_type=result.entity_type,
                            score=result.score,
                            recognizer=recognizer.name,
                            canonical_key=str((result.recognition_metadata or {}).get("canonical_key") or ""),
                        )
                    )
        return resolve_overlaps(candidates)


_PAGE_ANCHOR = re.compile(r"<!--\s*pagina-origem:\s*\d+\s*-->")
_BLOCK_CHARS = 50_000


def _page_blocks(text: str) -> list[tuple[int, str]]:
    """Blocos de ate ``_BLOCK_CHARS`` cortados so no inicio de uma ancora de pagina."""

    cuts = [match.start() for match in _PAGE_ANCHOR.finditer(text) if match.start() > 0]
    if len(text) <= _BLOCK_CHARS or not cuts:
        return [(0, text)]
    blocks: list[tuple[int, str]] = []
    start = 0
    for cut in cuts:
        if cut - start >= _BLOCK_CHARS:
            blocks.append((start, text[start:cut]))
            start = cut
    blocks.append((start, text[start:]))
    return blocks


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

    # Escolhidos ficam disjuntos e ordenados pelo inicio; o candidato so pode
    # colidir com o ultimo escolhido que comeca antes do fim dele. O(n log n):
    # a varredura contra todos os escolhidos custava minutos num processo de
    # milhares de folhas (dezenas de milhares de spans).
    starts: list[int] = []
    selected: list[EntitySpan] = []
    unique = {
        (span.start, span.end, span.entity_type, span.recognizer): span
        for span in candidates
    }.values()

    for candidate in sorted(unique, key=_rank, reverse=True):
        position = bisect_left(starts, candidate.end)
        if position and _overlap(candidate, selected[position - 1]):
            continue
        starts.insert(position, candidate.start)
        selected.insert(position, candidate)

    return sorted(selected, key=lambda span: (span.start, span.end, span.entity_type))

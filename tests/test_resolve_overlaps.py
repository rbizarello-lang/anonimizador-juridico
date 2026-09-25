from __future__ import annotations

import unittest

from anonimizador_juridico import EntitySpan, resolve_overlaps
from anonimizador_juridico.detector import source_rank


def _span(
    text: str,
    value: str,
    entity_type: str,
    score: float,
    recognizer: str,
) -> EntitySpan:
    start = text.index(value)
    return EntitySpan(
        start=start,
        end=start + len(value),
        entity_type=entity_type,
        score=score,
        recognizer=recognizer,
    )


class SourcePriorityTests(unittest.TestCase):
    def test_list_org_beats_ner_person(self) -> None:
        text = "NORTE INVENTADO COMERCIO"
        merged = resolve_overlaps(
            [
                _span(text, text, "PERSON", 0.72, "NerLocal"),
                _span(text, text, "ORGANIZATION", 0.95, "ListaDeAlvos"),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].entity_type, "ORGANIZATION")
        self.assertEqual(merged[0].recognizer, "ListaDeAlvos")
        self.assertGreater(
            source_rank(merged[0]),
            source_rank(_span(text, text, "PERSON", 0.72, "NerLocal")),
        )

    def test_structured_ids_beat_list_and_ner(self) -> None:
        text = "CPF 529.982.247-25"
        merged = resolve_overlaps(
            [
                _span(text, text, "PERSON", 0.99, "ListaDeAlvos"),
                _span(text, text, "PERSON", 0.90, "NerLocal"),
                _span(text, text, "BR_CPF", 0.75, "BrCpfRecognizer"),
            ]
        )
        self.assertEqual([span.entity_type for span in merged], ["BR_CPF"])
        self.assertEqual(merged[0].recognizer, "BrCpfRecognizer")

    def test_same_source_keeps_type_then_score(self) -> None:
        text = "Helena Ficticia Ltda"
        person = _span(text, "Helena Ficticia", "PERSON", 0.72, "NerLocal")
        org = _span(text, text, "ORGANIZATION", 0.95, "NerLocal")
        merged = resolve_overlaps([person, org])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].entity_type, "PERSON")
        self.assertEqual(merged[0].recognizer, "NerLocal")

    def test_header_beats_ner_same_type(self) -> None:
        text = "Carla Exemplo Souza"
        merged = resolve_overlaps(
            [
                _span(text, text, "PERSON", 0.88, "HeaderHeuristic"),
                _span(text, text, "PERSON", 0.91, "NerLocal"),
            ]
        )
        self.assertEqual(merged[0].recognizer, "HeaderHeuristic")

    def test_address_heuristic_same_rank_as_header(self) -> None:
        text = "Rua das Acacias, 100"
        merged = resolve_overlaps(
            [
                _span(text, text, "LOCATION", 0.90, "HeuristicaEndereco"),
                _span(text, text, "LOCATION", 0.88, "HeaderHeuristic"),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(source_rank(merged[0]), 300)
        self.assertEqual(
            source_rank(_span(text, text, "LOCATION", 0.90, "HeuristicaEndereco")),
            source_rank(_span(text, text, "LOCATION", 0.88, "HeaderHeuristic")),
        )


if __name__ == "__main__":
    unittest.main()

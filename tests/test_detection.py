from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from anonimizador_juridico import StructuredDetector


CORPUS_DIR = Path(__file__).parent / "corpus"


def _load_corpus_cases() -> list[tuple[str, str, dict]]:
    cases: list[tuple[str, str, dict]] = []
    for expected_path in sorted(CORPUS_DIR.glob("caso_*_expected.json")):
        prefix = expected_path.name.replace("_expected.json", "")
        text = (CORPUS_DIR / f"{prefix}_processo_sintetico.md").read_text(encoding="utf-8")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        cases.append((prefix, text, expected))
    return cases


class StructuredDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = [
            (prefix, text, expected, StructuredDetector().detect(text))
            for prefix, text, expected in _load_corpus_cases()
        ]

    def test_expected_entity_counts(self) -> None:
        self.assertGreaterEqual(len(self.cases), 2)
        for prefix, _text, expected, entities in self.cases:
            with self.subTest(corpus=prefix):
                counts = Counter(entity.entity_type for entity in entities)
                self.assertEqual(dict(counts), expected["expected_counts"])

    def test_exact_expected_values(self) -> None:
        for prefix, text, expected, entities in self.cases:
            with self.subTest(corpus=prefix):
                actual: dict[str, list[str]] = {}
                for entity in entities:
                    actual.setdefault(entity.entity_type, []).append(entity.value_from(text))
                self.assertEqual(actual, expected["expected_values"])

    def test_unlabeled_invalid_examples_are_not_detected(self) -> None:
        text = "123.456.789-00; 12.345.678/0001-90; 0001234-56.2023.8.26.0100."
        self.assertEqual(StructuredDetector().detect(text), [])

    def test_unlabeled_new_identifiers_are_not_detected(self) -> None:
        text = (
            "3456-7; 89012-3; 00345678901; ABC1D23; XYZ-1234; "
            "Evento 203; R$ 12.345,67."
        )
        self.assertEqual(StructuredDetector().detect(text), [])

    def test_labeled_invalid_examples_are_quarantined(self) -> None:
        text = "CPF 123.456.789-00; CNPJ 12.345.678/0001-90; processo 0001234-56.2023.8.26.0100."
        entities = StructuredDetector().detect(text)
        self.assertEqual(
            [entity.entity_type for entity in entities],
            ["BR_CPF", "BR_CNPJ", "BR_PROCESSO"],
        )
        self.assertTrue(all("Fallback" in entity.recognizer for entity in entities))

    def test_labeled_pix_conta_renavam_placa(self) -> None:
        text = (
            "chave aleatoria 9b2c3d4e-5f6a-4b7c-8d9e-0a1b2c3d4e5f; "
            "agencia e conta 1234/56789-0; RENAVAM 123456789; placa XYZ-1234."
        )
        entities = StructuredDetector().detect(text)
        self.assertEqual(
            [entity.entity_type for entity in entities],
            ["BR_PIX", "BR_CONTA_BANCARIA", "BR_RENAVAM", "BR_PLACA"],
        )

    def test_labeled_oab_eproc_variants(self) -> None:
        text = (
            "Patronos: OAB/RS n.º 54.321 e N.OAB- 11223. "
            "Evento 203 permanece. Uniao e SISBAJUD tambem."
        )
        entities = StructuredDetector().detect(text)
        self.assertEqual([entity.entity_type for entity in entities], ["BR_OAB", "BR_OAB"])
        values = [entity.value_from(text) for entity in entities]
        self.assertEqual(values, ["RS n.º 54.321", "11223"])
        self.assertIn("Evento 203", text)

    def test_labeled_cep_with_optional_thousand_dot(self) -> None:
        text = "CEP 86000-000 na CDA; outro CEP 86.000-000 na inicial."
        entities = StructuredDetector().detect(text)
        values = [entity.value_from(text) for entity in entities if entity.entity_type == "BR_CEP"]
        self.assertEqual(values, ["86000-000", "86.000-000"])

    def test_cpf_validator_still_applies_without_label(self) -> None:
        valid = StructuredDetector().detect("Identificador 52998224725 no Evento 203.")
        invalid = StructuredDetector().detect("Identificador 12345678900 no Evento 203.")
        self.assertEqual([entity.entity_type for entity in valid], ["BR_CPF"])
        self.assertEqual(invalid, [])

    def test_no_overlaps(self) -> None:
        for prefix, _text, _expected, entities in self.cases:
            with self.subTest(corpus=prefix):
                for previous, current in zip(entities, entities[1:]):
                    self.assertGreaterEqual(current.start, previous.end)


if __name__ == "__main__":
    unittest.main()

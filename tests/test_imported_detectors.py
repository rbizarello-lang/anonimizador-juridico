from __future__ import annotations

import unittest

from anonimizador_juridico import StructuredDetector
from anonimizador_juridico.pseudonymizer import canonicalize
from anonimizador_juridico.validators import is_valid_pis


def _pis_with_dv(body10: str) -> str:
    weights = (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    total = sum(int(digit) * weight for digit, weight in zip(body10, weights))
    remainder = total % 11
    check = 0 if remainder < 2 else 11 - remainder
    candidate = body10 + str(check)
    if not is_valid_pis(candidate):
        raise AssertionError(candidate)
    return candidate


class ImportedDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = StructuredDetector()

    def _values(self, text: str, entity_type: str) -> list[str]:
        return [
            span.value_from(text)
            for span in self.detector.detect(text)
            if span.entity_type == entity_type
        ]

    def test_cpf_ocr_letter_shares_canonical(self) -> None:
        text = "Qualificacao 529.982.247-2S no contrato."
        values = self._values(text, "BR_CPF")
        self.assertTrue(any("529.982.247-2S" in item for item in values))
        self.assertEqual(canonicalize("BR_CPF", values[0]), "52998224725")

    def test_unlabeled_ocr_letters_without_punctuation_are_ignored(self) -> None:
        self.assertEqual(self.detector.detect("Identificador 5299822472S no Evento 203."), [])

    def test_pis_labeled_with_dv(self) -> None:
        digits = _pis_with_dv("1204567890")
        formatted = f"{digits[:3]}.{digits[3:8]}.{digits[8:10]}-{digits[10]}"
        text = f"PIS {formatted} no cadastro. Uniao."
        values = self._values(text, "BR_PIS")
        self.assertEqual(values, [formatted])
        self.assertEqual(canonicalize("BR_PIS", values[0]), digits)

    def test_pis_without_label_not_detected(self) -> None:
        digits = _pis_with_dv("1204567890")
        formatted = f"{digits[:3]}.{digits[3:8]}.{digits[8:10]}-{digits[10]}"
        self.assertNotIn("BR_PIS", [span.entity_type for span in self.detector.detect(formatted)])

    def test_pis_labeled_invalid_dv_not_detected(self) -> None:
        self.assertEqual(self.detector.detect("PIS 111.11111.11-1 no cadastro."), [])

    def test_email_tld_glued_to_next_word(self) -> None:
        text = "contato vara@tjxx.jus.brVara no cabecalho."
        values = self._values(text, "EMAIL_ADDRESS")
        self.assertTrue(any(item.endswith("@tjxx.jus.br") for item in values))
        self.assertFalse(any("Vara" in item for item in values))

    def test_rg_span_keeps_label_and_drops_ssp_when_lookahead(self) -> None:
        text = "Identidade Civil R.G nº 2.246.861-0 SSP/PR"
        values = self._values(text, "BR_RG")
        self.assertTrue(values)
        self.assertTrue(any("2.246.861-0" in item for item in values))
        self.assertFalse(any("SSP" in item for item in values))


if __name__ == "__main__":
    unittest.main()

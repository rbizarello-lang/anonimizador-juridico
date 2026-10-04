from __future__ import annotations

import unittest

from anonimizador_juridico import (
    PseudonymMap,
    StructuredDetector,
    propagate_mapped_ids,
    pseudonymize,
)
from anonimizador_juridico.validators import is_valid_cpf


class Ciclo2PropagationTests(unittest.TestCase):
    def test_hamming_one_with_label_shares_token(self) -> None:
        valid = "529.982.247-25"
        self.assertTrue(is_valid_cpf(valid))
        text = (
            f"Cadastro CPF {valid} da administradora. "
            "Depois, em OCR, CPF. 529.982.147-25 na mesma folha."
        )
        structured = StructuredDetector().detect(text)
        merged = propagate_mapped_ids(text, structured)
        result = pseudonymize(text, merged)
        self.assertNotIn("529.982.247-25", result.text)
        self.assertNotIn("529.982.147-25", result.text)
        cpf_records = [item for item in result.mapping.records if item.entity_type == "BR_CPF"]
        self.assertEqual(len(cpf_records), 1)
        self.assertEqual(cpf_records[0].canonical, "52998224725")

    def test_one_deletion_with_label_shares_token(self) -> None:
        valid = "529.982.247-25"
        self.assertTrue(is_valid_cpf(valid))
        text = (
            f"Cadastro CPF {valid} da administradora. "
            "Depois, em OCR, CPF 529.982.24-25 na mesma folha."
        )
        structured = StructuredDetector().detect(text)
        merged = propagate_mapped_ids(text, structured)
        result = pseudonymize(text, merged)
        self.assertNotIn("529.982.24-25", result.text)
        cpf_records = [item for item in result.mapping.records if item.entity_type == "BR_CPF"]
        self.assertEqual(len(cpf_records), 1)
        self.assertEqual(cpf_records[0].canonical, "52998224725")

    def test_hamming_one_without_label_does_not_propagate(self) -> None:
        text = "Primeiro 529.982.247-25 e depois 529.982.147-25 solto."
        structured = StructuredDetector().detect(text)
        merged = propagate_mapped_ids(text, structured)
        result = pseudonymize(text, merged)
        self.assertIn("529.982.147-25", result.text)

    def test_cnpj_comma_shares_canonical(self) -> None:
        text = (
            "CNPJ 38.915.670/0001-00 da sede e, no OCR, "
            "CNPJ/MF: 38.915,670/0001-00 no contrato."
        )
        structured = StructuredDetector().detect(text)
        merged = propagate_mapped_ids(text, structured)
        result = pseudonymize(text, merged)
        self.assertNotIn("38.915,670/0001-00", result.text)
        cnpj_records = [item for item in result.mapping.records if item.entity_type == "BR_CNPJ"]
        self.assertEqual(len(cnpj_records), 1)
        self.assertEqual(cnpj_records[0].canonical, "38915670000100")

    def test_uses_existing_map_canonical(self) -> None:
        mapping = PseudonymMap()
        first = "CPF 529.982.247-25"
        first_spans = StructuredDetector().detect(first)
        first_spans = propagate_mapped_ids(first, first_spans, mapping=mapping)
        pseudonymize(first, first_spans, mapping=mapping)
        second = "Folha seguinte CPF. 529.982.147-25"
        second_spans = StructuredDetector().detect(second)
        second_spans = propagate_mapped_ids(second, second_spans, mapping=mapping)
        result = pseudonymize(second, second_spans, mapping=mapping)
        self.assertIn("[CPF_1]", result.text)
        self.assertNotIn("529.982.147-25", result.text)

    def test_filial_cnpj_shares_token_of_known_root(self) -> None:
        from anonimizador_juridico.validators import is_valid_cnpj

        sede = "61.738.402/0001-82"
        filial = ""
        for check in range(100):
            compact = f"617384020004{check:02d}"
            if is_valid_cnpj(compact):
                filial = (
                    f"{compact[:2]}.{compact[2:5]}.{compact[5:8]}/"
                    f"{compact[8:12]}-{compact[12:]}"
                )
                break
        self.assertTrue(filial)
        text = f"CNPJ {sede} da sede e a filial {filial} no mesmo grupo."
        spans = StructuredDetector().detect(text)
        spans = propagate_mapped_ids(text, spans)
        result = pseudonymize(text, spans)
        self.assertNotIn(sede, result.text)
        self.assertNotIn(filial, result.text)
        self.assertGreaterEqual(result.text.count("[CNPJ_1]"), 2)

    def test_table_word_is_not_a_filial_of_known_root(self) -> None:
        # Lote de 25/09: "CONTRIBUICAO" + dia virou CNPJ e cada outro dia virou "filial".
        mapping = PseudonymMap()
        mapping.resolve("BR_CNPJ", "CONTRIBUICAO23")
        text = "CONTRIBUICAO                    20/06/2023     01/07/2023"
        spans = propagate_mapped_ids(text, StructuredDetector().detect(text), mapping=mapping)
        self.assertEqual([span for span in spans if span.entity_type == "BR_CNPJ"], [])


if __name__ == "__main__":
    unittest.main()

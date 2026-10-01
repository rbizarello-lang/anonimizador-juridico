from __future__ import annotations

import unittest

from anonimizador_juridico import StructuredDetector
from anonimizador_juridico.pseudonymizer import canonicalize


class Ciclo2LabeledOcrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = StructuredDetector()

    def _types(self, text: str) -> list[str]:
        return [span.entity_type for span in self.detector.detect(text)]

    def _values(self, text: str, entity_type: str) -> list[str]:
        return [
            span.value_from(text)
            for span in self.detector.detect(text)
            if span.entity_type == entity_type
        ]

    def test_cpf_dot_instead_of_hyphen(self) -> None:
        text = "Qualificacao CPF. 279.457.589.91 no contrato."
        self.assertIn("BR_CPF", self._types(text))
        self.assertTrue(any("279.457.589.91" in item for item in self._values(text, "BR_CPF")))

    def test_cpf_ocr_digit_and_no_space_after_label(self) -> None:
        text = "CPF. 279.457.389.91 e outro CPF.328.172.009-30"
        values = self._values(text, "BR_CPF")
        self.assertTrue(any("279.457.389.91" in item for item in values))
        self.assertTrue(any("328.172.009-30" in item for item in values))

    def test_cpf_isolated_digit_group(self) -> None:
        text = "CPF 841.3.329-20 na testemunha."
        self.assertTrue(any("841.3.329-20" in item for item in self._values(text, "BR_CPF")))

    def test_cpf_line_break_after_hyphen(self) -> None:
        text = "e CPF 851.764.609-\n63, residente em Londrina."
        values = self._values(text, "BR_CPF")
        joined = " ".join(values)
        self.assertIn("851.764.609", joined)
        self.assertIn("63", joined)

    def test_cpf_mf_and_dotted_label(self) -> None:
        text = "C.P.F. n° 529.982.247-25 e CPF/MF 529.982.247-25"
        self.assertGreaterEqual(len(self._values(text, "BR_CPF")), 1)

    def test_unlabeled_cpf_keeps_checksum(self) -> None:
        valid = "Identificador 52998224725 no Evento 203."
        invalid = "Identificador 12345678900 no Evento 203."
        self.assertEqual(self._types(valid), ["BR_CPF"])
        self.assertEqual(self.detector.detect(invalid), [])

    def test_cnpj_comma_thousands(self) -> None:
        text = "CNPJ/MF: 01.294,953/0001-72"
        values = self._values(text, "BR_CNPJ")
        self.assertTrue(any("01.294,953/0001-72" in item for item in values))
        self.assertEqual(canonicalize("BR_CNPJ", values[0]), "01294953000172")

    def test_cnpj_line_break_in_middle(self) -> None:
        text = "inscrita no CNPJ/MF n° 01.294.953/0001-\n72, estabelecida"
        values = self._values(text, "BR_CNPJ")
        self.assertTrue(values)
        joined = "".join(values)
        self.assertIn("01.294.953/0001", joined)

    def test_cnpj_space_before_check_digits(self) -> None:
        text = "CNPJ 04.252.011/0001- 10 da sede."
        values = self._values(text, "BR_CNPJ")
        self.assertTrue(any("04.252.011/0001- 10" in item for item in values))

    def test_unlabeled_invalid_cnpj_not_detected(self) -> None:
        self.assertEqual(self.detector.detect("12.345.678/0001-90"), [])

    def test_table_word_and_day_are_not_alphanumeric_cnpj(self) -> None:
        # "CONTRIBUICAO" tem 12 letras; com o dia da data da coluna ao lado, o DV fechava
        # e o token comia a data ("[CNPJ_n]/12/2022").
        for text in (
            "CONTRIBUICAO                    23/12/2022     01/01/2023",
            "CONTRIBUICAO\n23/06/2023\n01/07/2023",
        ):
            with self.subTest(text=text):
                self.assertNotIn("BR_CNPJ", self._types(text))

    def test_alphanumeric_cnpj_with_digit_in_base_is_detected(self) -> None:
        values = self._values("sede 12.ABC.345/01DE-35 da empresa", "BR_CNPJ")
        self.assertEqual(values, ["12.ABC.345/01DE-35"])

    def test_cnpj_does_not_cross_column_gap(self) -> None:
        text = "01.294.953/0001        72/06/2023 na coluna"
        self.assertNotIn("BR_CNPJ", self._types(text))

    def test_labeled_cpf_old_check_digit_notation(self) -> None:
        # DV depois da barra (notacao antiga): o token leva o DV, nao deixa "/25" colado.
        text = "MATRICULA\nC.P.F. 529982247/25\nAPARTAMENTO"
        self.assertEqual(self._values(text, "BR_CPF"), ["529982247/25"])
        self.assertEqual(canonicalize("BR_CPF", "C.P.F. 529982247/25"), "52998224725")

    def test_rg_graphies(self) -> None:
        samples = [
            "RG. 1476.757-SSP-PR",
            "RG. 4921.619-0 - SSP-PR",
            "R.G n° 3.851.210 SSP-PR",
            "Identidade Civil R.G nº 5.860.113-6 SSP/PR",
            "Cédula de Identidade Civil R.G nº 6.538.991-6",
            "R.G n║ 5.860.113-6 SSP/PR",
            "R.G n░ 6.538.991-6 SPP/PR",
            "R.G. 13.524.564-0 e CPF",
            "RC 5.554.703-3 SSP-PR",
            "Cédula de Identidade Civil R.G n° 3.851.210",
            "Identidade Civil R.G n° 5.860.113-6",
            "Cédula de\nIdentidade Civil R.G nº 1.234.567-8 SSP-PR",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertIn("BR_RG", self._types(sample), msg=sample)

    def test_rg_negatives(self) -> None:
        self.assertNotIn("BR_RG", self._types("NIRE 4120352196 na junta."))
        self.assertNotIn("BR_RG", self._types("Valor R$ 1.234,56 pago."))
        self.assertNotIn("BR_RG", self._types("Nascido em 14/06/1996."))
        self.assertNotIn("BR_RG", self._types("RE 5009816-34.2021.4.04.0000"))

    def test_cep_labeled_variants(self) -> None:
        extra = "Vila Ipiranga – CEP: 86020-0000, Londrina"
        hyphen = "CEP-86039-000. Londrina"
        broken = "Centro, CEP: 86039-\n000 – Londrina"
        self.assertTrue(any("86020-0000" in item for item in self._values(extra, "BR_CEP")))
        self.assertTrue(any("86039-000" in item for item in self._values(hyphen, "BR_CEP")))
        broken_vals = self._values(broken, "BR_CEP")
        self.assertTrue(broken_vals)
        self.assertEqual(canonicalize("BR_CEP", "CEP: 86020-0000"), "86020000")

    def test_cep_after_uf_is_detected(self) -> None:
        text = "Buena Vista, Londrina - PR, 86032-010"
        values = self._values(text, "BR_CEP")
        self.assertTrue(any("86032-010" in item for item in values))
        self.assertFalse(any(item.lstrip().startswith("PR") for item in values))

    def test_unlabeled_cep_without_uf_not_detected(self) -> None:
        self.assertNotIn("BR_CEP", self._types("O endereco postal e 86032-010 na cidade."))

    def test_uf_cep_does_not_eat_lei_or_money(self) -> None:
        self.assertNotIn("BR_CEP", self._types("Aplicacao da Lei 8.666/93 e art. 186 do CTN."))
        self.assertNotIn("BR_CEP", self._types("Valor R$ 86.032,10 pago em 24/12/2025."))

    def test_conta_judicial_ag_op_conta(self) -> None:
        line = (
            "Depositante: ALTMANN COMERCIO R$2.971,28 em 22/12/2025 "
            "Ag./Op./Conta: 1271/635/8659-0 Ato cumprido"
        )
        values = self._values(line, "BR_CONTA_BANCARIA")
        self.assertTrue(any("1271/635/8659-0" in item for item in values))
        self.assertEqual(canonicalize("BR_CONTA_BANCARIA", values[0]), "127163586590")

    def test_conta_does_not_eat_dates(self) -> None:
        self.assertNotIn("BR_CONTA_BANCARIA", self._types("Pago em 24/12/2025 no Evento."))

    def test_labeled_phone_seven_local_digits(self) -> None:
        text = "Oscar Gonçalves Sobrinho - Tabelião - Tel. (43) 347-4377"
        values = self._values(text, "BR_PHONE")
        self.assertTrue(any("347-4377" in item for item in values))
        self.assertFalse(any(item.lower().startswith("tel") for item in values))

    def test_labeled_phone_does_not_split_telefone(self) -> None:
        text = "Telefone: (43) 3339-3132 no cabecalho."
        values = self._values(text, "BR_PHONE")
        self.assertTrue(any("3339-3132" in item for item in values))
        self.assertFalse(any("efone" in item.casefold() for item in values))

    def test_labeled_phone_same_line_with_junk(self) -> None:
        text = "Cep: E 6015-000-Eondrina - PR Telefone/Fox: (43) 343-4645"
        values = self._values(text, "BR_PHONE")
        self.assertTrue(any("343-4645" in item for item in values))

    def test_ddd_parentheses_is_phone_label(self) -> None:
        for sample in (
            "Contato (43) 9134-6500 no rodape.",
            "Celular\n(43) 8808-9099",
            "Fone (43)339-3132",
        ):
            with self.subTest(sample=sample):
                self.assertIn("BR_PHONE", self._types(sample), msg=sample)

    def test_unlabeled_placa_not_phone(self) -> None:
        self.assertEqual(self._types("Placa PR/SDS3A82 no DETRAN."), [])

    def test_negatives_date_and_money_not_cpf(self) -> None:
        text = "Data de Início de Atividade 14/06/1996. Valor R$ 1.234,56. NIRE 4120352196."
        types = set(self._types(text))
        self.assertNotIn("BR_CPF", types)
        self.assertNotIn("BR_RG", types)

    def test_conta_sispar_is_detected(self) -> None:
        text = "CONTA SISPAR 7019180 no parcelamento. Uniao."
        values = self._values(text, "BR_CONTA_BANCARIA")
        self.assertTrue(any("7019180" in item for item in values))

    def test_inscricao_sispar_is_detected(self) -> None:
        text = "INSCRICAO SISPAR 0000000000000701/364 no Debcad. Uniao."
        values = self._values(text, "BR_CONTA_BANCARIA")
        self.assertTrue(any("0000000000000701" in item for item in values))

    def test_matriculas_plural_list_is_detected(self) -> None:
        text = "as matrículas nº 294; nº 301 e o pedido. Uniao."
        types = self._types(text)
        self.assertIn("BR_MATRICULA_IMOVEL", types)


if __name__ == "__main__":
    unittest.main()

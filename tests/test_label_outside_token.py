"""Fila 3.x, E11: o rotulo fica fora do token e o valor inteiro fica dentro.

Dados inventados. "Banco/Agencia: 237/1234-5" virava "Banco/[CONTA_n]/1234-5" (o span comia o
rotulo e partia o numero); "Debcad:", "Matricula n" e "Ag./Op./Conta:" perdiam o rotulo; "executada X"
perdia o papel; CNPJ com a filial partida e CEP de 8 digitos em tabela escapavam.
"""

from __future__ import annotations

import unittest

from anonimizador_juridico import EntitySpan, StructuredDetector, pseudonymize

from anonimizador_app.span_hygiene import sanitize_spans


def _detect(text: str, entity_type: str | None = None) -> list[str]:
    spans = StructuredDetector().detect(text)
    return [span.value_from(text) for span in spans if entity_type in (None, span.entity_type)]


def _masked(text: str) -> str:
    return pseudonymize(text, StructuredDetector().detect(text)).text


class BankAccountLabelTests(unittest.TestCase):
    def test_banco_agencia_number_stays_whole_and_label_outside(self) -> None:
        text = "Pagamento\nBanco/Agência:\n237/1234-5\nNúmero do Pagamento: 11122233344"
        self.assertEqual(_detect(text, "BR_CONTA_BANCARIA"), ["237/1234-5"])
        masked = _masked(text)
        self.assertIn("Banco/Agência:\n[CONTA_1]\nNúmero", masked)
        self.assertNotIn("Banco/[CONTA", masked)
        self.assertNotIn("1234", masked)

    def test_banco_agencia_same_line_and_no_banco_prefix(self) -> None:
        self.assertEqual(
            _detect("Banco/Agência: 001/4321 liberado", "BR_CONTA_BANCARIA"), ["001/4321"]
        )
        self.assertEqual(
            _detect("Agência: 341/0987-6 liberado", "BR_CONTA_BANCARIA"), ["341/0987-6"]
        )

    def test_agencia_alone_keeps_its_label(self) -> None:
        text = "Agência: 1234-5 da CEF"
        self.assertEqual(_detect(text, "BR_CONTA_BANCARIA"), ["1234-5"])
        self.assertEqual(_masked(text), "Agência: [CONTA_1] da CEF")

    def test_debcad_label_stays(self) -> None:
        text = "DADOS GERAIS DO DEBCAD\nDebcad: \n123456789\nSituação: ativo"
        self.assertEqual(_detect(text, "BR_CONTA_BANCARIA"), ["123456789"])
        self.assertIn("Debcad: \n[CONTA_1]\nSituação", _masked(text))
        # "Debcad 1 / 6" (contagem) nao e numero de Debcad.
        self.assertEqual(_detect("Debcad 1 / 6", "BR_CONTA_BANCARIA"), [])

    def test_ag_op_conta_label_stays(self) -> None:
        text = "Depósito Ag./Op./Conta: 1234/005/12345-6 em 01/02/2020"
        self.assertEqual(_detect(text, "BR_CONTA_BANCARIA"), ["1234/005/12345-6"])
        self.assertEqual(_masked(text), "Depósito Ag./Op./Conta: [CONTA_1] em 01/02/2020")

    def test_agencia_e_conta_become_two_tokens_without_labels(self) -> None:
        text = "agencia 3456-7 e conta 89012-3 do executado"
        self.assertEqual(_detect(text, "BR_CONTA_BANCARIA"), ["3456-7", "89012-3"])
        self.assertEqual(_masked(text), "agencia [CONTA_1] e conta [CONTA_2] do executado")

    def test_conta_label_stays(self) -> None:
        text = "Conta Corrente nº 123456-7 e SISPAR: 12345678/01"
        self.assertEqual(_detect(text, "BR_CONTA_BANCARIA"), ["123456-7", "12345678/01"])
        masked = _masked(text)
        self.assertTrue(masked.startswith("Conta Corrente nº [CONTA_1]"))
        self.assertIn("SISPAR: [CONTA_2]", masked)


class RegistrationLabelTests(unittest.TestCase):
    """H9-i: a matricula de imovel fica SEMPRE em claro (vazamento controlado, decisao do Operador)."""

    def test_matricula_stays_in_clear_with_any_label(self) -> None:
        for text in (
            "imóvel da Matrícula nº 12.345 do 1º CRI",
            "matrículas nº 11.111; nº 22.222; 33.333 do cartório",
            "Matrícula 11.111. Depois, matrículas 11.111; 22.222.",
            "matricula n. 7777",
        ):
            with self.subTest(text=text):
                self.assertEqual(_detect(text, "BR_MATRICULA_IMOVEL"), [])
                self.assertEqual(_masked(text), text)

    def test_label_without_number_is_not_a_matricula(self) -> None:
        self.assertEqual(_detect("conforme a matrícula do imóvel", "BR_MATRICULA_IMOVEL"), [])


class OtherLabeledIdTests(unittest.TestCase):
    def test_cep_label_stays_in_every_notation(self) -> None:
        text = "CEP: 88000-000; CEP 88.000-000; CEP:88000000; CEP\n88000-000."
        self.assertEqual(
            _detect(text, "BR_CEP"), ["88000-000", "88.000-000", "88000000", "88000-000"]
        )
        self.assertTrue(_masked(text).startswith("CEP: [CEP_1]; CEP [CEP_1]; CEP:[CEP_1]"))

    def test_rg_label_stays(self) -> None:
        text = "cédula de identidade RG n.º 1.234.567-8 e R.G.: 9.876.543"
        self.assertEqual(_detect(text, "BR_RG"), ["1.234.567-8", "9.876.543"])
        self.assertEqual(
            _masked(text), "cédula de identidade RG n.º [RG_1] e R.G.: [RG_2]"
        )

    def test_rc_ocr_rg_needs_ssp_and_label_stays(self) -> None:
        self.assertEqual(_detect("RC 1.234.567-8 SSP/SC", "BR_RG"), ["1.234.567-8"])
        self.assertEqual(_detect("RC 1.234.567-8 sem orgao", "BR_RG"), [])

    def test_oab_label_stays_and_uf_stays_in_the_token(self) -> None:
        text = "OAB/SC 12.345-B, OAB SC123456 e OAB 4.321/PR"
        self.assertEqual(
            _detect(text, "BR_OAB"), ["SC 12.345-B", "SC123456", "4.321/PR"]
        )
        self.assertTrue(_masked(text).startswith("OAB/[OAB_1], OAB [OAB_2] e OAB [OAB_3]"))

    def test_labeled_cpf_and_cnpj_with_invalid_dv_keep_label_outside(self) -> None:
        self.assertEqual(_masked("CPF 123.456.789-00"), "CPF [CPF_1]")
        self.assertEqual(_masked("CNPJ: 12.345.678/0001-90"), "CNPJ: [CNPJ_1]")

    def test_labeled_process_renavam_placa_pix_keep_label_outside(self) -> None:
        text = (
            "Processo nº 0001234-56.2023.8.26.0100; RENAVAM: 00345678901; "
            "placa ABC1D23; chave Pix 9b2c3d4e-5f6a-4b7c-8d9e-0a1b2c3d4e5f"
        )
        masked = _masked(text)
        self.assertIn("Processo nº [PROCESSO_1]", masked)
        self.assertIn("RENAVAM: [RENAVAM_1]", masked)
        self.assertIn("placa [PLACA_1]", masked)
        self.assertIn("chave Pix [PIX_1]", masked)

    def test_label_does_not_change_the_identity(self) -> None:
        # "CEP: X" e "X" sozinho (CEP_UF) caem no mesmo token: a chave canonica e o numero.
        masked = _masked("CEP: 88000-000. Rua A, Centro, SC, 88000-000")
        self.assertEqual(masked.count("[CEP_1]"), 2)
        self.assertNotIn("[CEP_2]", masked)


class CnpjSplitBranchTests(unittest.TestCase):
    # 11.222.333/0001-81 tem DV valido.
    def test_branch_split_by_column_break_or_space_is_detected(self) -> None:
        for text in (
            "CNPJ da empresa\n11.222.333/00\n01-81\nEndereço",
            "devedor 11.222.333/00 01-81 principal",
        ):
            with self.subTest(text=text):
                self.assertEqual(len(_detect(text, "BR_CNPJ")), 1)
                self.assertNotIn("11.222", _masked(text))
                self.assertNotIn("01-81", _masked(text))

    def test_split_branch_shares_the_token_with_the_whole_cnpj(self) -> None:
        masked = _masked("A 11.222.333/0001-81 e B 11.222.333/00 01-81")
        self.assertEqual(masked, "A [CNPJ_1] e B [CNPJ_1]")

    def test_split_branch_still_needs_a_valid_check_digit(self) -> None:
        self.assertEqual(_detect("coluna 11.222.333/00 01-82 fim", "BR_CNPJ"), [])

    def test_split_branch_is_digits_only(self) -> None:
        # Base com letra e filial partida: nao e CNPJ (vao de coluna nao separa CNPJ).
        self.assertEqual(_detect("CONTRIBUICAO 00 01-81 coluna", "BR_CNPJ"), [])


class CepTableTests(unittest.TestCase):
    def test_eight_digit_cep_in_address_table_row(self) -> None:
        text = "RUA DAS FLORES 100 CENTRO\n12345678 CIDADE EXEMPLO SC\nPrincipal"
        self.assertEqual(_detect(text, "BR_CEP"), ["12345678"])
        masked = _masked(text)
        self.assertIn("CENTRO\n[CEP_1] CIDADE EXEMPLO SC\nPrincipal", masked)
        self.assertNotIn("12345678", masked)

    def test_cell_separated_row(self) -> None:
        text = "| RUA A | 12345678 CIDADE EXEMPLO SC | Principal |"
        self.assertEqual(_detect(text, "BR_CEP"), ["12345678"])

    def test_eight_digits_elsewhere_are_not_a_cep(self) -> None:
        for text in (
            "PORTARIA N 12345678 DA DF DA SJSC",  # UF no meio da frase, nao no fim da celula
            "Atualização 12345678 0\nValor",
            "protocolo 12345678 ate 87654321",
            "Data 31122020 SC",  # sem cidade em maiusculas
            "12345678 cidade exemplo sc",  # minusculas
        ):
            with self.subTest(text=text):
                self.assertEqual(_detect(text, "BR_CEP"), [])


class RoleWordHygieneTests(unittest.TestCase):
    @staticmethod
    def _clip(text: str, value: str, entity_type: str, recognizer: str) -> str | None:
        start = text.index(value)
        span = EntitySpan(start, start + len(value), entity_type, 0.9, recognizer)
        kept = sanitize_spans(text, [span])
        return text[kept[0].start : kept[0].end] if kept else None

    def test_role_word_stays_outside_target_list_organization(self) -> None:
        text = "A executada NORTE INVENTADO LTDA foi citada."
        self.assertEqual(
            self._clip(text, "executada NORTE INVENTADO LTDA", "ORGANIZATION", "ListaDeAlvos"),
            "NORTE INVENTADO LTDA",
        )

    def test_role_word_stays_outside_ner_spans(self) -> None:
        text = "Requerida: SUL FICTICIA S.A. e AGRAVANTE: Maria Inventada."
        self.assertEqual(
            self._clip(text, "Requerida: SUL FICTICIA S.A.", "ORGANIZATION", "NerLocal"),
            "SUL FICTICIA S.A.",
        )
        self.assertEqual(
            self._clip(text, "AGRAVANTE: Maria Inventada", "PERSON", "NerLocal"),
            "Maria Inventada",
        )

    def test_devedor_principal_label_with_line_break(self) -> None:
        text = "Devedor Principal: \nNORTE INVENTADO LTDA\nCNPJ"
        self.assertEqual(
            self._clip(text, "Devedor Principal: \nNORTE INVENTADO LTDA", "ORGANIZATION", "NerLocal"),
            "NORTE INVENTADO LTDA",
        )

    def test_company_name_that_starts_with_principal_is_kept(self) -> None:
        text = "A Principal Distribuidora Ltda vendeu."
        self.assertEqual(
            self._clip(text, "Principal Distribuidora Ltda", "ORGANIZATION", "NerLocal"),
            "Principal Distribuidora Ltda",
        )

    def test_role_only_span_and_other_types_are_untouched(self) -> None:
        text = "executada"
        self.assertEqual(self._clip(text, "executada", "ORGANIZATION", "NerLocal"), "executada")
        text = "Rua Executada Inventada, 10"
        self.assertEqual(
            self._clip(text, "Rua Executada Inventada, 10", "LOCATION", "NerLocal"),
            "Rua Executada Inventada, 10",
        )


class RosterRolePhraseTests(unittest.TestCase):
    def test_one_word_name_is_still_found_after_the_role_word(self) -> None:
        # A higiene tira "executada" do token; o elenco guarda "executada NORTEX" como forma
        # buscavel, para nao deixar o nome curto em claro onde so o papel o ancora.
        from anonimizador_app.batch_propagation import BatchRoster

        first = "A executada NORTEX respondeu."
        start = first.index("NORTEX")
        roster = BatchRoster()
        roster.add(first, [EntitySpan(start, start + 6, "ORGANIZATION", 0.9, "NerLocal")])
        second = "Consta que a executada NORTEX e a requerida SULEX; NORTEX sozinha nao."
        spans = roster.apply(second, [])
        found = [second[span.start : span.end] for span in spans]
        self.assertEqual(found, ["NORTEX"])  # so depois do papel; o nome solto nao se propaga
        self.assertEqual(spans[0].start, second.index("NORTEX"))


if __name__ == "__main__":
    unittest.main()

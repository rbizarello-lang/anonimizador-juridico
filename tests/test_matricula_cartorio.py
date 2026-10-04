"""Matricula por cartorio (endurecimento, deteccao 2.7): o mesmo numero em cartorios diferentes e imovel
diferente. Dados inventados."""

from __future__ import annotations

import unittest

from anonimizador_app.fidelity import drop_protected_spans
from anonimizador_app.gemini_detector import is_registry_office
from anonimizador_juridico import EntitySpan, PseudonymMap, StructuredDetector, pseudonymize
from anonimizador_juridico.registry_office import office_key


def _tokens(text: str) -> tuple[str, PseudonymMap]:
    spans = StructuredDetector().detect(text)
    mapping = PseudonymMap()
    return pseudonymize(text, spans, mapping=mapping).text, mapping


class OfficeKeyTests(unittest.TestCase):
    def _key(self, text: str, number: str = "12345") -> str:
        start = text.index(number)
        return office_key(text, start, start + len(number))

    def test_ordinal_and_place_after_the_number(self) -> None:
        self.assertEqual(self._key("Matrícula nº 12345 do 1º Registro de Imóveis de Joinville."), "1|joinville")

    def test_office_before_the_number(self) -> None:
        text = "Certidão do 2º Ofício de Registro de Imóveis da Comarca de Blumenau. Matrícula nº 12345."
        self.assertEqual(self._key(text), "2|blumenau")

    def test_written_ordinal_and_all_caps(self) -> None:
        self.assertEqual(self._key("MATRÍCULA 12345 - PRIMEIRO REGISTRO DE IMÓVEIS DE CRICIÚMA"), "1|criciuma")

    def test_place_stops_at_uf_and_label(self) -> None:
        self.assertEqual(self._key("Registro de Imóveis de Itajaí SC Matrícula 12345"), "|itajai")
        self.assertEqual(self._key("Registro de Imóveis de Itajaí/SC, matrícula 12345"), "|itajai")

    def test_certificate_header_with_the_place_on_the_next_line(self) -> None:
        # Layout da certidao: o lugar vem na linha de baixo e a linha seguinte e endereco, nao lugar.
        text = (
            "2º OFÍCIO DE REGISTRO DE IMÓVEIS\nCOMARCA DE BLUMENAU\nRUA FICTÍCIA DE TESTE - CENTRO\n"
            "Matrícula nº 12345\n"
        )
        self.assertEqual(self._key(text), "2|blumenau")
        same_office_other_page = "2º OFÍCIO DE REGISTRO DE IMÓVEIS\nCOMARCA DE BLUMENAU\nMatrícula nº 12345\n"
        self.assertEqual(self._key(same_office_other_page), "2|blumenau")

    def test_circumscription_number_is_the_order(self) -> None:
        self.assertEqual(self._key("Matrícula 12345. REGISTRO DE IMÓVEIS DA 8ª CIRCUNSCRIÇÃO"), "8|")

    def test_nearest_office_wins(self) -> None:
        text = "Matrícula nº 11111 (1º Registro de Imóveis de Alfa) e matrícula nº 22222 (2º Registro de Imóveis de Beta)."
        self.assertEqual(self._key(text, "11111"), "1|alfa")
        self.assertEqual(self._key(text, "22222"), "2|beta")

    def test_bare_mention_without_order_or_place_is_no_key(self) -> None:
        self.assertEqual(self._key("Matrícula 12345 do Registro de Imóveis competente."), "")
        self.assertEqual(self._key("Matrícula 12345."), "")

    def test_digits_before_the_office_are_not_an_ordinal(self) -> None:
        self.assertEqual(self._key("Protocolo 98712 Registro de Imóveis de Gama, matrícula 12345"), "|gama")


class MatriculaTokenTests(unittest.TestCase):
    def test_same_number_in_two_offices_gets_two_tokens(self) -> None:
        text = (
            "Imóvel da matrícula nº 12345 do 1º Registro de Imóveis de Joinville.\n"
            "Imóvel da matrícula nº 12345 do 2º Registro de Imóveis de Joinville.\n"
        )
        out, mapping = _tokens(text)
        first, second = out.splitlines()
        self.assertIn("[MATRICULA_IMOVEL_1]", first)
        self.assertIn("[MATRICULA_IMOVEL_2]", second)
        self.assertEqual(len(mapping.records), 2)

    def test_same_number_in_the_same_office_keeps_one_token(self) -> None:
        text = (
            "Matrícula nº 12345 do 1º Registro de Imóveis de Joinville.\n"
            "Averbada na matrícula nº 12345 do 1º Registro de Imóveis de Joinville.\n"
        )
        out, mapping = _tokens(text)
        self.assertEqual(out.count("[MATRICULA_IMOVEL_1]"), 2)
        self.assertEqual(len(mapping.records), 1)

    def test_without_an_office_the_behaviour_is_the_old_one(self) -> None:
        text = "Matrícula nº 12345.\nOutra vez a matrícula nº 12345, sem cartório.\nMatrícula nº 777.\n"
        out, mapping = _tokens(text)
        self.assertEqual(out.count("[MATRICULA_IMOVEL_1]"), 2)
        self.assertIn("[MATRICULA_IMOVEL_2]", out)
        self.assertEqual([record.canonical for record in mapping.records], ["12345", "777"])

    def test_the_office_name_is_never_part_of_the_token(self) -> None:
        out, _mapping = _tokens("Matrícula nº 12345 do 1º Registro de Imóveis de Joinville.")
        self.assertEqual(out, "Matrícula nº [MATRICULA_IMOVEL_1] do 1º Registro de Imóveis de Joinville.")


class OfficeNameInClearTests(unittest.TestCase):
    NAMES = (
        "1º Registro de Imóveis de Joinville",
        "Cartório de Registro de Imóveis da Comarca de Blumenau",
        "2º Ofício de Registro de Imóveis",
        "Cartório do 2º Ofício",
        "Tabelionato de Notas de Itajaí",
        "TABELIONATO DE PROTESTO DE TÍTULOS",
        "Serviço Registral de Imóveis",
        "Registro Geral de Imóveis",
    )

    def test_registry_office_names_are_recognised(self) -> None:
        for name in self.NAMES:
            self.assertTrue(is_registry_office(name), name)

    def test_company_spans_with_those_names_are_dropped_before_tokenising(self) -> None:
        for name in self.NAMES:
            text = f"Certidão do {name}."
            start = text.index(name)
            span = EntitySpan(start, start + len(name), "ORGANIZATION", 0.9, "NerLocal")
            self.assertEqual(drop_protected_spans(text, [span], ()), [], name)

    def test_ordinary_companies_are_not_protected(self) -> None:
        for name in ("Registrar Comércio de Veículos Ltda", "Imóveis Alfa Ltda", "Notas Fiscais Digitais Ltda"):
            self.assertFalse(is_registry_office(name), name)


if __name__ == "__main__":
    unittest.main()

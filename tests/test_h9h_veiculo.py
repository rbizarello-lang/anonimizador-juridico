"""H9-h, item H: veiculo em todas as origens (motor local, propagacao do elenco, mencao do LLM).

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_app.vehicle_names import is_vehicle_span, is_vehicle_text

from test_llm_hygiene import _Pipeline, mention, run
from test_pipeline_llm import FakeDiscoverer, FakeLocalEngine
from test_variant_propagation import VariantDiscoverer


def _doc(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


def _span(text: str, piece: str) -> bool:
    start = text.index(piece)
    return is_vehicle_span(text, start, start + len(piece))


class VehicleSpanTests(unittest.TestCase):
    def test_marca_e_modelo_de_uma_palavra_com_ano_ou_placa_ao_lado(self) -> None:
        self.assertTrue(_span("Veículo: Amarok 2014/2015, cor prata.", "Amarok"))
        self.assertTrue(_span("Reboque Librelato 2012 em nome de terceiro.", "Librelato"))
        self.assertTrue(_span("2013 Amarok disponível.", "Amarok"))
        self.assertTrue(_span("| ABC1D23 | Amarok | prata |", "Amarok"))
        self.assertTrue(_span("| ABC-1234 | LIBRELATO | reboque |", "LIBRELATO"))

    def test_modelo_com_marca_ou_com_trim(self) -> None:
        self.assertTrue(_span("| XYZ9K88 | FIAT/STRADA FREEDOM CD | 2020 |", "FIAT/STRADA FREEDOM CD"))
        self.assertTrue(_span("| XYZ9K88 | STRADA FREEDOM CD | 2020 |", "STRADA FREEDOM CD"))
        self.assertTrue(is_vehicle_text("Volkswagen Amarok V6 Highline"))

    def test_sem_prova_no_texto_a_palavra_continua_sendo_candidata_a_empresa(self) -> None:
        self.assertFalse(_span("O senhor Amarok foi ouvido.", "Amarok"))
        self.assertFalse(_span("A empresa Librelato foi citada.", "Librelato"))

    def test_empresa_com_a_marca_no_nome_continua_empresa(self) -> None:
        for text, piece in (
            ("| ABC1D23 | Librelato S/A | 2012 |", "Librelato S/A"),
            ("Librelato Implementos Rodoviários 2012", "Librelato"),
            ("Fiat Participações 2020 ABC1D23", "Fiat Participações"),
            ("Volkswagen Financial Services 2020", "Volkswagen Financial Services"),
            ("Guerra 2020 ABC1D23", "Guerra"),
            ("Toyota Leasing ABC1D23", "Toyota Leasing"),
        ):
            with self.subTest(piece=piece):
                self.assertFalse(_span(text, piece))

    def test_mencao_do_llm_com_ano_ao_lado_e_descartada_pela_higiene(self) -> None:
        text = "Veículos penhorados: Amarok 2014/2015 e Librelato 2012."
        for value in ("Amarok", "Librelato"):
            with self.subTest(value=value):
                out, stats = run([mention("empresa", value, text)], text)
                self.assertEqual((out, stats.veiculo), ([], 1))


class VehicleInThePipelineTests(_Pipeline):
    ROWS = _doc(
        "| Placa | Modelo | Ano |",
        "| ABC1D23 | Amarok | 2014 |",
        "| XYZ9K88 | FIAT/STRADA FREEDOM CD | 2020 |",
        "A executada Alfa Transportes Ltda pediu vista.",
    )

    def test_motor_local_nao_troca_modelo_por_empresa(self) -> None:
        engine = FakeLocalEngine(
            [("ORGANIZATION", "Amarok"), ("ORGANIZATION", "FIAT/STRADA FREEDOM CD"), ("ORGANIZATION", "Alfa Transportes Ltda")]
        )
        _result, body, _audit = self.go(self.ROWS, FakeDiscoverer([]), fallback_engine=engine, local_floor=True)
        self.assertIn("Amarok", body)
        self.assertIn("FIAT/STRADA FREEDOM CD", body)
        self.assertNotIn("Alfa Transportes", body)

    def test_marca_curta_propagada_nao_troca_o_implemento_da_listagem(self) -> None:
        full = "Librelato Implementos Rodoviarios Ltda"
        text = _doc(
            f"A {full} foi citada e a {full} apresentou defesa; a {full} recorreu.",
            "Listagem do RENAJUD:",
            "| ABC1D23 | LIBRELATO 2012 | reboque |",
            "Depois a Librelato pagou o débito.",
        )
        discoverer = VariantDiscoverer([("empresa", full, "parte")], [("empresa", "Librelato", "parte", full)])
        _result, body, audit = self.go(text, discoverer)
        self.assertIn("LIBRELATO 2012", body)
        self.assertRegex(body, r"Depois a \[EMPRESA_\d+\] pagou")
        self.assertNotIn(full, body)
        self.assertRegex(audit, r"retirado da troca final \(nao e empresa\): 1")


if __name__ == "__main__":
    unittest.main()

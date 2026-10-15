"""H9-g, item 5: veiculo so e descartado com modelo conhecido ou numero depois da marca.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from test_h9f_revisao import _kept
from test_llm_hygiene import mention, run


class VeiculoTests(unittest.TestCase):
    def test_marca_com_modelo_ou_numero_e_veiculo(self) -> None:
        text = "Veículos: Fiat Uno 2015, Honda CG 160, Fiat Strada, BMW 320i, Chevrolet S10."
        for value in ("Fiat Uno 2015", "Honda CG 160", "Fiat Strada", "BMW 320i", "Chevrolet S10"):
            with self.subTest(value=value):
                out, stats = run([mention("empresa", value, text)], text)
                self.assertEqual((out, stats.veiculo), ([], 1))

    def test_marca_com_palavra_de_empresa_continua_empresa(self) -> None:
        text = (
            "A Volkswagen Financial Services, a Toyota Leasing, a BMW Financeira, a Fiat Participações "
            "e a Scania Latin America foram citadas. A Fiat também."
        )
        for value in (
            "Volkswagen Financial Services", "Toyota Leasing", "BMW Financeira", "Fiat Participações",
            "Scania Latin America", "Fiat",
        ):
            with self.subTest(value=value):
                self.assertTrue(_kept("empresa", value, text))
                out, stats = run([mention("empresa", value, text)], text)
                self.assertEqual(stats.veiculo, 0)


if __name__ == "__main__":
    unittest.main()

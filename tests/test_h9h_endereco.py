"""H9-h, item J: endereco em rua numerada (o nome do logradouro e um numero).

Enderecos inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from anonimizador_app.address_heuristic import AddressHeuristicEngine, RECOGNIZER


def _found(text: str) -> list[str]:
    spans = AddressHeuristicEngine().detect(text)
    assert all(span.recognizer == RECOGNIZER and span.entity_type == "LOCATION" for span in spans)
    return [text[span.start : span.end] for span in spans]


class RuaNumeradaTests(unittest.TestCase):
    def test_exemplos_do_lote(self) -> None:
        self.assertEqual(_found("Reside na Rua 2070, 235, Centro, Balneário."), ["Rua 2070, 235"])
        self.assertEqual(_found("Fica na Rua 1500, nº 10."), ["Rua 1500, nº 10"])
        self.assertEqual(_found("Sede: Avenida 3, 45 – Sala 2."), ["Avenida 3, 45"])

    def test_abreviaturas_e_praca(self) -> None:
        self.assertEqual(_found("Mora em R. 3, n° 45; trabalha na Av. 7, 100."), ["R. 3, n° 45", "Av. 7, 100"])
        self.assertEqual(_found("Praça 3, nº 10"), ["Praça 3, nº 10"])

    def test_sem_o_numero_predial_nao_e_endereco(self) -> None:
        for text in ("Rua 12 e depois Rua 5.", "Rua 2070.", "Rua 2070 e Centro, 235 pessoas"):
            with self.subTest(text=text):
                self.assertEqual(_found(text), [])

    def test_texto_comum_com_numeros_nao_e_endereco(self) -> None:
        for text in ("Art. 5, 10 dias de prazo.", "Lei 8.112, 90 dias", "Evento 12, 14 folhas", "fls. 3, 45"):
            with self.subTest(text=text):
                self.assertEqual(_found(text), [])

    def test_rua_com_nome_continua_como_antes(self) -> None:
        self.assertEqual(_found("Reside na Rua Ernesto Garbelotto, 54, Jardim Universitário."), ["Rua Ernesto Garbelotto, 54"])


if __name__ == "__main__":
    unittest.main()

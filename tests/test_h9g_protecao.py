"""H9-g, item 6: protecao demais. "Socio" na frase nao prova que o nome e de particular; cargos de magistrado.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from anonimizador_app.batch_propagation import _ROLE_AFTER
from anonimizador_app.roster import KEEP, TOKENIZE

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer


def _doc(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


class ProtecaoDemaisTests(_Pipeline):
    X = "SICRANO DE QUALQUER FICTICIO"
    Y = "BELTRANO DE OUTRO FICTICIO"

    def test_procurador_da_fazenda_que_requereu_a_citacao_do_socio_fica_em_claro(self) -> None:
        text = _doc(f"Procurador da Fazenda Nacional {self.X} requereu a citação do sócio.")
        result, body, _audit = self.go(text, FakeDiscoverer([("pessoa", self.X, "parte")]))
        self.assertIn(self.X, body)
        self.assertEqual(result.roster.lookup("pessoa", self.X).action, KEEP)

    def test_juiza_que_determinou_a_citacao_do_socio_fica_e_o_socio_qualificado_e_trocado(self) -> None:
        text = _doc(f"A Juíza {self.X} determinou a citação do sócio {self.Y}, brasileiro, casado.")
        result, body, _audit = self.go(
            text, FakeDiscoverer([("pessoa", self.X, "parte"), ("pessoa", self.Y, "socio")])
        )
        self.assertIn(self.X, body)
        self.assertNotIn(self.Y, body)
        self.assertEqual(result.roster.lookup("pessoa", self.Y).action, TOKENIZE)

    def test_assinado_digitalmente_por_juiza_de_direito_fica_em_claro(self) -> None:
        for cargo in ("Juíza de Direito", "Juiz de Direito", "Juiz(a) Federal Substituto(a)", "Desembargador(a) Federal"):
            with self.subTest(cargo=cargo):
                text = _doc(f"Assinado digitalmente por {self.X}, {cargo}.")
                result, body, _audit = self.go(text, FakeDiscoverer([("pessoa", self.X, "parte")]))
                self.assertIn(self.X, body)
                self.assertEqual(result.roster.lookup("pessoa", self.X).action, KEEP)

    def test_qualificacao_logo_depois_do_nome_ou_cpf_perto_continuam_a_proteger_a_parte(self) -> None:
        for line in (
            f"Procurador da Fazenda Nacional {self.X} requereu vista.\n\n{self.X}, empresário, residente em Itajaí.",
            f"Procurador da Fazenda Nacional {self.X} requereu vista.\n\n{self.X}, CPF 529.982.247-25.",
        ):
            with self.subTest(line=line[-40:]):
                result, body, _audit = self.go(_doc(line), FakeDiscoverer([("pessoa", self.X, "socio")]))
                self.assertNotIn(self.X, body)
                self.assertEqual(result.roster.lookup("pessoa", self.X).action, TOKENIZE)


class RoleAfterTests(unittest.TestCase):
    def test_cargos_novos(self) -> None:
        for cargo in ("Juiz de Direito", "Juíza de Direito", "Juiz(a) Federal Substituto(a)", "Desembargador(a) Federal"):
            with self.subTest(cargo=cargo):
                self.assertTrue(_ROLE_AFTER.match(f", {cargo}"))


if __name__ == "__main__":
    unittest.main()

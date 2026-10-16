"""H9-h, item C: sobrenome so propaga sem homonimo no elenco; a extensao por adjacencia nao avanca sobre palavra de
outra entrada, municipio em contexto de endereco nem razao social em caixa alta.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_juridico import EntitySpan

from anonimizador_app.roster import Mention, Roster
from anonimizador_app.variant_propagation import VariantPropagator, company_name_follows

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer
from test_h9g_adjacencia import _setup, _span


def _doc(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


class SobrenomeComHomonimoTests(_Pipeline):
    FULL = "Joel Martinez Tavernard"
    HOMONYM = "Beatriz Helena Tavernard"

    def _text(self) -> str:
        return _doc(
            f"O sócio {self.FULL} foi citado e {self.FULL} apresentou defesa; {self.FULL} recorreu.",
            f"A Juíza {self.HOMONYM} decidiu o incidente.",
            "Depois o sócio Tavernard compareceu ao balcão.",
        )

    def test_sobrenome_com_pista_de_pessoa_propaga_quando_nao_ha_homonimo(self) -> None:
        text = _doc(
            f"O sócio {self.FULL} foi citado e {self.FULL} apresentou defesa; {self.FULL} recorreu.",
            "Depois o sócio Tavernard compareceu ao balcão.",
        )
        _result, body, _audit = self.go(text, FakeDiscoverer([("pessoa", self.FULL, "socio")]))
        self.assertNotIn("Tavernard", body)

    def test_sobrenome_nao_propaga_quando_outra_entrada_do_elenco_o_traz_mesmo_mantida(self) -> None:
        discoverer = FakeDiscoverer([("pessoa", self.FULL, "socio"), ("pessoa", self.HOMONYM, "magistrado")])
        result, body, audit = self.go(self._text(), discoverer)
        self.assertIn(self.HOMONYM, body)
        self.assertIn("sócio Tavernard compareceu", body)
        self.assertNotIn(self.FULL, body)
        self.assertRegex(audit, r"ambiguidade [1-9]")

    def test_sobrenome_nao_propaga_quando_o_homonimo_e_empresa(self) -> None:
        text = _doc(
            f"O sócio {self.FULL} foi citado e {self.FULL} apresentou defesa; {self.FULL} recorreu.",
            "A Tavernard Móveis Ltda foi intimada.",
            "Depois o sócio Tavernard compareceu ao balcão.",
        )
        discoverer = FakeDiscoverer([("pessoa", self.FULL, "socio"), ("empresa", "Tavernard Móveis Ltda", "parte")])
        _result, body, _audit = self.go(text, discoverer)
        self.assertIn("sócio Tavernard compareceu", body)

    def test_sobrenome_em_razao_social_por_extenso_nao_e_da_pessoa(self) -> None:
        text = _doc(
            f"O sócio {self.FULL} foi citado e {self.FULL} apresentou defesa; {self.FULL} recorreu.",
            "Segundo o sócio TAVERNARD AGROFLORESTAL LTDA pediu vista.",
        )
        _result, body, _audit = self.go(text, FakeDiscoverer([("pessoa", self.FULL, "socio")]))
        self.assertIn("TAVERNARD AGROFLORESTAL LTDA", body)

    def test_familia_com_homonimo_no_elenco_nao_recebe_o_token(self) -> None:
        text = _doc(
            f"O sócio {self.FULL} foi citado e {self.FULL} apresentou defesa; {self.FULL} recorreu.",
            f"A Juíza {self.HOMONYM} decidiu.",
            "A família Tavernard mora longe.",
        )
        discoverer = FakeDiscoverer([("pessoa", self.FULL, "socio"), ("pessoa", self.HOMONYM, "magistrado")])
        _result, body, _audit = self.go(text, discoverer)
        self.assertIn("família Tavernard", body)


class CompanyNameFollowsTests(unittest.TestCase):
    def test_sufixo_societario_depois_de_nomes_proprios(self) -> None:
        for text in (
            "SANTOS AGROFLORESTAL LTDA",
            "Santos Agroflorestal Ltda.",
            "Santos Agroflorestal S/A",
            "Santos Comércio de Grãos EIRELI",
            "Santos S.A.",
        ):
            with self.subTest(text=text):
                self.assertTrue(company_name_follows(text, len(text.split()[0])))

    def test_frase_comum_nao_e_razao_social(self) -> None:
        text = "Santos pagou a dívida, ltda. nada mais."
        self.assertFalse(company_name_follows(text, 6))
        self.assertFalse(company_name_follows("Santos, Ltda", 6))


class AdjacenciaTravasTests(unittest.TestCase):
    FULL = "Maria Domenico Ferreira"

    def _run(self, text: str, piece: str, *names: str):
        roster, propagator = _setup(*names)
        span = _span(roster, names[0], text, piece)
        out = propagator.extend_adjacent(text, [span])
        return text[out[0].start : out[0].end]

    def test_palavra_do_nome_de_outra_entrada_nao_entra_nem_sendo_parte_do_nome(self) -> None:
        # "Domenico" e do nome da pessoa E do nome de outra entrada: de quem e a palavra colada? De nenhuma.
        text = "O sócio Maria Domenico foi citado."
        self.assertEqual(self._run(text, "Maria", self.FULL, "Joao Domenico Lima"), "Maria")
        self.assertEqual(self._run(text, "Maria", self.FULL), "Maria Domenico")

    def test_palavra_de_empresa_do_elenco_nao_entra(self) -> None:
        roster = Roster()
        for kind, name in (("pessoa", "Maria Agroflorestal Souza"), ("empresa", "Agroflorestal Norte Ltda")):
            roster.add_mention(Mention(kind=kind, text=name, canonical=name, role="parte"), source="llm")
        propagator = VariantPropagator(roster)
        text = "A parte Maria Agroflorestal decidiu."
        span = _span(roster, "Maria Agroflorestal Souza", text, "Maria")
        out = propagator.extend_adjacent(text, [span])
        self.assertEqual(text[out[0].start : out[0].end], "Maria")

    def test_palavra_em_caixa_alta_seguida_de_sufixo_societario_nao_entra(self) -> None:
        names = ("Joel Agroflorestal Souza",)
        self.assertEqual(self._run("Joel AGROFLORESTAL LTDA pagou.", "Joel", *names), "Joel")
        self.assertEqual(self._run("Joel AGROFLORESTAL S/A pagou.", "Joel", *names), "Joel")
        # Sem sufixo, a palavra do proprio nome entra (como na H9-g).
        self.assertEqual(self._run("Joel AGROFLORESTAL pagou.", "Joel", *names), "Joel AGROFLORESTAL")

    def test_municipio_fora_do_nome_da_entrada_nao_entra(self) -> None:
        # "Pomerode" e municipio; a entrada nao o traz no nome, so uma grafia parecida.
        names = ("Maria Pomerodi Souza",)
        self.assertEqual(self._run("A parte Maria Pomerode disse.", "Maria", *names), "Maria")

    def test_municipio_que_e_sobrenome_so_nao_entra_em_contexto_de_endereco(self) -> None:
        names = ("Maria Pomerode Souza",)
        self.assertEqual(self._run("A parte Maria Pomerode disse.", "Maria", *names), "Maria Pomerode")
        self.assertEqual(self._run("A parte Maria Pomerode/SC disse.", "Maria", *names), "Maria")

    def test_rodape_com_bairro_nao_e_engolido(self) -> None:
        names = ("Maria Santos Lima",)
        text = "Endereço: Maria Santos, CEP 89000-000, Rua Um, 10."
        # "Santos" e municipio e a linha tem CEP: contexto de endereco.
        self.assertEqual(self._run(text, "Maria", *names), "Maria")


if __name__ == "__main__":
    unittest.main()

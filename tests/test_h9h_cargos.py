"""H9-h, item G: "SOCIO ADMINISTRADOR" e rotulo de cargo, nunca pessoa; cargo de cartorio (tabeliao, escrevente, oficial
de registro, oficial substituto) fica em claro como o de juiz.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from anonimizador_app.batch_propagation import _ROLE_AFTER, _ROLE_BEFORE, _public_role
from anonimizador_app.gemini_detector import is_too_generic
from anonimizador_app.llm_hygiene import LlmHygieneStats, filter_llm_mentions
from anonimizador_app.role_labels import is_role_label
from anonimizador_app.roster import KEEP, TOKENIZE

from test_llm_hygiene import _Pipeline, mention
from test_pipeline_llm import FakeDiscoverer, FakeLocalEngine


def _doc(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


ROTULOS = ("SÓCIO ADMINISTRADOR", "SÓCIA ADMINISTRADORA", "ADMINISTRADOR(ES)", "sócio-administrador", "Sócios Administradores", "administrador(a)")


class RotuloDeCargoTests(unittest.TestCase):
    def test_so_cargo_e_rotulo(self) -> None:
        for value in ROTULOS + ("Sócio", "Administradora"):
            with self.subTest(value=value):
                self.assertTrue(is_role_label(value))

    def test_nome_com_a_palavra_do_cargo_nao_e_so_rotulo(self) -> None:
        for value in ("Maria Sócia", "Administradora Alfa", "José Administrador Lima", "Alfa Participações"):
            with self.subTest(value=value):
                self.assertFalse(is_role_label(value))

    def test_higiene_do_llm_descarta_o_rotulo_como_pessoa_e_como_empresa(self) -> None:
        text = "O SÓCIO ADMINISTRADOR e a SÓCIA ADMINISTRADORA, ADMINISTRADOR(ES) e sócio-administrador assinaram."
        stats = LlmHygieneStats()
        mentions = [mention("pessoa", value, text) for value in ("SÓCIO ADMINISTRADOR", "SÓCIA ADMINISTRADORA", "ADMINISTRADOR(ES)")]
        mentions.append(mention("empresa", "SÓCIO ADMINISTRADOR", text))
        self.assertEqual(filter_llm_mentions(mentions, text, stats), [])
        self.assertEqual(stats.rotulo_formulario, 4)

    def test_motor_local_tambem_nao_troca_o_rotulo(self) -> None:
        for value in ROTULOS:
            with self.subTest(value=value):
                self.assertTrue(is_too_generic("PERSON", value))
                self.assertTrue(is_too_generic("ORGANIZATION", value))
        self.assertFalse(is_too_generic("PERSON", "Maria Antonia Souza"))


class RotuloNoLoteTests(_Pipeline):
    def test_rotulo_que_o_modelo_e_o_motor_local_acharam_fica_em_claro(self) -> None:
        text = _doc("Fulano Beltrano Ficticio, brasileiro, SÓCIO ADMINISTRADOR da empresa; ADMINISTRADOR(ES): Fulano Beltrano Ficticio.")
        engine = FakeLocalEngine([("PERSON", "SÓCIO ADMINISTRADOR"), ("PERSON", "ADMINISTRADOR(ES)")])
        discoverer = FakeDiscoverer(
            [("pessoa", "Fulano Beltrano Ficticio", "socio"), ("pessoa", "SÓCIO ADMINISTRADOR", "socio")]
        )
        _result, body, audit = self.go(text, discoverer, fallback_engine=engine, local_floor=True)
        self.assertIn("SÓCIO ADMINISTRADOR", body)
        self.assertIn("ADMINISTRADOR(ES)", body)
        self.assertNotIn("Fulano Beltrano", body)


class CartorioTests(_Pipeline):
    NAME = "Maria Antonia Silva Souza"

    def _kept(self, line: str, entities: list[tuple[str, str, str]], name: str | None = None) -> None:
        text = _doc(line)
        result, body, _audit = self.go(text, FakeDiscoverer(entities))
        value = name or entities[0][1]
        self.assertEqual(result.roster.lookup("pessoa", value).action, KEEP)
        self.assertIn(self.NAME, body)

    def test_cargo_depois_do_nome(self) -> None:
        for cargo in (
            "Tabeliã Substituta", "Tabelião", "Tabelião Substituto", "Escrevente", "Escrevente Autorizada",
            "Oficial de Registro", "Oficial Substituto", "Oficiala Substituta", "Registradora",
        ):
            with self.subTest(cargo=cargo):
                self._kept(f"Certifico. {self.NAME}, {cargo}.", [("pessoa", self.NAME, "terceiro")])

    def test_cargo_antes_do_nome(self) -> None:
        for cargo in ("Tabeliã Substituta", "Tabelião", "Escrevente Autorizada", "Oficial de Registro", "Oficial Substituto"):
            with self.subTest(cargo=cargo):
                self._kept(f"Certifico. {cargo} {self.NAME} lavrou o ato.", [("pessoa", self.NAME, "parte")])

    def test_nome_parcial_do_modelo_nao_deixa_o_ultimo_sobrenome_em_claro(self) -> None:
        # O modelo apontou so "Maria Antonia Silva"; o texto traz "... Silva Souza, Tabeliã Substituta".
        partial = "Maria Antonia Silva"
        text = _doc(f"Certifico. {self.NAME}, Tabeliã Substituta, recebeu o pedido.")
        result, body, _audit = self.go(text, FakeDiscoverer([("pessoa", partial, "terceiro")]))
        self.assertEqual(result.roster.lookup("pessoa", partial).action, KEEP)
        self.assertIn(self.NAME, body)

    def test_parte_privada_com_cpf_perto_continua_trocada(self) -> None:
        text = _doc(f"{self.NAME}, CPF 529.982.247-25, Escrevente de fato, residente em Itajaí.")
        result, body, _audit = self.go(text, FakeDiscoverer([("pessoa", self.NAME, "parte")]))
        self.assertEqual(result.roster.lookup("pessoa", self.NAME).action, TOKENIZE)
        self.assertNotIn(self.NAME, body)


class CueTests(unittest.TestCase):
    def test_cues_novas_depois_e_antes(self) -> None:
        for after in (", Tabeliã Substituta", " Silva, Oficial de Registro", ", Oficial Substituto", ", Escrevente"):
            self.assertTrue(_ROLE_AFTER.match(after), after)
        for before in ("Tabeliã Substituta ", "Oficial de Registro de Imóveis ", "Escrevente Autorizado "):
            self.assertTrue(_ROLE_BEFORE.search(before), before)

    def test_palavras_soltas_entre_o_nome_e_o_cargo_so_se_capitalizadas(self) -> None:
        self.assertTrue(_ROLE_AFTER.match(" Souza, Tabeliã"))
        self.assertTrue(_ROLE_AFTER.match(" de Souza Lima, Juiz de Direito"))
        self.assertFalse(_ROLE_AFTER.match(" alegou tabeliã"))
        self.assertFalse(_ROLE_AFTER.match(" Um Dois Tres, Tabeliã"))
        text = "Maria Silva Souza, Tabeliã Substituta"
        self.assertTrue(_public_role(text, 0, len("Maria Silva")))


if __name__ == "__main__":
    unittest.main()

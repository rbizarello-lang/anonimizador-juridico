"""H9-g, item 2: contagem separada das recusas e sobrenome de municipio com pista de pessoa.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer
from test_variant_propagation import _page


class SobrenomeDeMunicipioTests(_Pipeline):
    def _full(self, full: str, *lines: str):
        text = _page(f"O sócio {full}, {full} e {full} foram citados.", *lines)
        return self.go(text, FakeDiscoverer([("pessoa", full, "socio")]))

    def test_familia_e_socio_com_sobrenome_que_e_municipio_propagam(self) -> None:
        for surname in ("Fagundes", "Santos"):
            with self.subTest(surname=surname):
                full = f"Marcos Alvarenga {surname}"
                _result, body, audit = self._full(
                    full, f"A família {surname} reside aqui.", f"Ouvido o sócio {surname}, nada disse."
                )
                self.assertNotIn(f"família {surname}", body)
                self.assertNotIn(f"sócio {surname}", body)
                self.assertRegex(body, r"A família \[PESSOA_\d+\] reside")
                self.assertRegex(body, r"o sócio \[PESSOA_\d+\], nada")
                self.assertRegex(audit, r"sobrenome 2")

    def test_sem_pista_de_pessoa_o_municipio_fica(self) -> None:
        full = "Marcos Alvarenga Fagundes"
        _result, body, audit = self._full(full, "Moram em Fagundes desde 2010.")
        self.assertIn("em Fagundes desde", body)

    def test_outro_nome_antes_ou_depois_nao_e_o_socio(self) -> None:
        full = "Marcos Alvarenga Fagundes"
        _result, body, audit = self._full(full, "O sócio Pedro Fagundes veio. O sócio Fagundes Souza veio.")
        self.assertIn("Pedro Fagundes", body)
        self.assertIn("Fagundes Souza", body)

    def test_municipio_com_poucas_mencoes_fica_e_a_recusa_e_contada_a_parte(self) -> None:
        full = "Marcos Alvarenga Fagundes"
        text = _page(f"O sócio {full} foi citado.", "A família Fagundes reside aqui.")
        _result, body, audit = self.go(text, FakeDiscoverer([("pessoa", full, "socio")]))
        self.assertIn("A família Fagundes reside", body)
        self.assertRegex(
            audit, r"recusadas por dicionario \d+, geografia 1, protegido \d+, ambiguidade \d+, contexto \d+"
        )

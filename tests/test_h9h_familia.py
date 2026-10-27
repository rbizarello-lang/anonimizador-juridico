"""H9-h, item F: "familia De Domenico" (particula antes do sobrenome) cobre particula e sobrenome.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer


def _doc(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


class FamiliaComParticulaTests(_Pipeline):
    FULL = "Alessandro Luiz De Domenico"

    def _body(self, *lines: str) -> str:
        text = _doc(f"O sócio {self.FULL} foi citado e {self.FULL} apresentou defesa; {self.FULL} recorreu.", *lines)
        _result, body, _audit = self.go(text, FakeDiscoverer([("pessoa", self.FULL, "socio")]))
        return body

    def test_particula_maiuscula_entra_no_token(self) -> None:
        body = self._body("Na casa da família De Domenico moram quatro pessoas.")
        self.assertRegex(body, r"da família \[SOBRENOME_\d+\] moram")  # v0.4.11: token de sobrenome
        self.assertNotIn("Domenico", body)

    def test_particula_minuscula_e_varias_familias(self) -> None:
        body = self._body("As famílias Silva e de Domenico foram ouvidas.", "A família do Domenico também.")
        self.assertNotIn("Domenico", body)
        self.assertRegex(body, r"famílias Silva e \[SOBRENOME_\d+\] foram")
        self.assertRegex(body, r"A família \[SOBRENOME_\d+\] também")

    def test_sem_particula_continua_como_antes(self) -> None:
        body = self._body("A família Domenico mora longe.")
        self.assertRegex(body, r"A família \[SOBRENOME_\d+\] mora longe")

    def test_particula_sem_sobrenome_conhecido_fica_em_claro(self) -> None:
        body = self._body("A família De Souza mora longe.")
        self.assertIn("família De Souza", body)

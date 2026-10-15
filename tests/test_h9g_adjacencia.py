"""H9-g, item 1: extensao do token por adjacencia (``[PESSOA_2] Domenico``, ``Maria [PESSOA_4]``).

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_juridico import EntitySpan

from anonimizador_app.roster import Mention, Roster
from anonimizador_app.variant_propagation import VariantPropagator

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer
from test_variant_propagation import VariantDiscoverer, _page


def _setup(*names: str) -> tuple[Roster, VariantPropagator]:
    roster = Roster()
    for name in names:
        roster.add_mention(Mention(kind="pessoa", text=name, canonical=name, role="parte"), source="llm")
    return roster, VariantPropagator(roster)


def _span(roster: Roster, full: str, text: str, piece: str, nth: int = 0) -> EntitySpan:
    start = -1
    for _ in range(nth + 1):
        start = text.index(piece, start + 1)
    return EntitySpan(
        start=start, end=start + len(piece), entity_type="PERSON", score=0.9, recognizer="Teste",
        canonical_key=roster.lookup("pessoa", full).key,
    )


class AdjacenciaTests(unittest.TestCase):
    FULL = "Maria Domenico Ferreira"

    def _run(self, text: str, piece: str, full: str | None = None, *others: str):
        roster, propagator = _setup(full or self.FULL, *others)
        span = _span(roster, full or self.FULL, text, piece)
        out = propagator.extend_adjacent(text, [span])
        return text[out[0].start : out[0].end], propagator

    def test_sobrenome_em_claro_depois_do_token_entra_no_mesmo_span(self) -> None:
        got, propagator = self._run("O sócio Maria Domenico foi citado.", "Maria")
        self.assertEqual(got, "Maria Domenico")
        self.assertEqual(propagator.stats["adjacencia"], 1)

    def test_prenome_em_claro_antes_do_token(self) -> None:
        got, _ = self._run("O sócio Maria Ferreira foi citado.", "Ferreira")
        self.assertEqual(got, "Maria Ferreira")

    def test_dois_nomes_seguidos_com_conectivo(self) -> None:
        got, _ = self._run("A autora Maria de Domenico Ferreira recorreu.", "Maria")
        self.assertEqual(got, "Maria de Domenico Ferreira")

    def test_palavra_que_nao_e_do_nome_da_entrada_fica_em_claro(self) -> None:
        got, propagator = self._run("O sócio Maria Silva foi citado.", "Maria")
        self.assertEqual(got, "Maria")
        self.assertEqual(propagator.stats["adjacencia"], 0)
        got, _ = self._run("O sócio Maria de Souza foi citado.", "Maria")
        self.assertEqual(got, "Maria")

    def test_cargo_e_virgula_nao_estendem(self) -> None:
        got, _ = self._run("A parte Maria, Juíza Federal, decidiu.", "Maria")
        self.assertEqual(got, "Maria")
        # Nem com o cargo colado: a palavra nao e parte do nome.
        got, _ = self._run("A parte Maria Juíza Federal decidiu.", "Maria")
        self.assertEqual(got, "Maria")

    def test_termo_protegido_inteiro_nao_entra_mesmo_sendo_parte_do_nome(self) -> None:
        got, _ = self._run("A parte Maria União pagou.", "Maria", "Maria União Costa")
        self.assertEqual(got, "Maria")

    def test_palavra_de_outra_entrada_nao_entra(self) -> None:
        got, _ = self._run("A parte Maria Tavernard pagou.", "Maria", "Maria Ferreira Lopes", "Joel Tavernard")
        self.assertEqual(got, "Maria")

    def test_quebra_de_linha_com_hifen_do_ocr(self) -> None:
        got, _ = self._run("Falou Maria De-\nDomenico no ato.", "Maria", "Maria de Domenico")
        self.assertEqual(got, "Maria De-\nDomenico")
        got, _ = self._run("Falou Maria Dome-\nnico no ato.", "Maria")
        self.assertEqual(got, "Maria Dome-\nnico")

    def test_quebra_de_linha_sem_hifen_nao_estende(self) -> None:
        got, _ = self._run("Falou Maria\nDomenico no ato.", "Maria")
        self.assertEqual(got, "Maria")

    def test_grafia_de_ocr_da_parte_do_nome(self) -> None:
        got, _ = self._run("Falou Maria Domenica no ato.", "Maria")
        self.assertEqual(got, "Maria Domenica")

    def test_minuscula_e_outro_span_nao_estendem(self) -> None:
        got, _ = self._run("falou Maria domenico no ato.", "Maria")
        self.assertEqual(got, "Maria")
        roster, propagator = _setup(self.FULL)
        text = "Falou Maria Domenico no ato."
        first = _span(roster, self.FULL, text, "Maria")
        second = _span(roster, self.FULL, text, "Domenico")
        out = propagator.extend_adjacent(text, [first, second])
        self.assertEqual([text[item.start : item.end] for item in out], ["Maria", "Domenico"])


class AdjacenciaNoLoteTests(_Pipeline):
    def test_forma_colada_vira_um_token_so_e_a_auditoria_conta(self) -> None:
        full = "Joel Martinez Tavernard"
        text = _page(
            "Primeiro o executado Joel Martinez compareceu.",
            f"Depois o sócio {full} foi citado e {full} apresentou defesa.",
        )
        discoverer = VariantDiscoverer(
            [("pessoa", full, "socio")], [("pessoa", "Joel", "socio", full)]
        )
        _result, body, audit = self.go(text, discoverer)
        self.assertNotIn("Martinez", body)
        self.assertRegex(body, r"executado \[PESSOA_\d+\] compareceu")
        self.assertNotRegex(body, r"\[PESSOA_\d+\] \[PESSOA_\d+\]")
        self.assertRegex(audit, r"[Tt]okens estendidos por adjacencia: [1-9]")


if __name__ == "__main__":
    unittest.main()

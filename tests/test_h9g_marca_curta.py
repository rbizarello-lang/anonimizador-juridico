"""H9-g, item 3: a marca curta de uma palavra, entrada propria do elenco, e ligada a razao social.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_app.roster import Mention, Roster
from anonimizador_app.variant_propagation import VariantPropagator

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer
from test_variant_propagation import _page


def _roster(*names: str) -> Roster:
    roster = Roster()
    for name in names:
        roster.add_mention(Mention(kind="empresa", text=name, canonical=name, role="parte"), source="llm")
    return roster


def _run(roster: Roster, text: str):
    propagator = VariantPropagator(roster)
    propagator.prepare([(text, None, [])])
    return propagator, propagator.spans(text, [])


class MarcaCurtaLigadaTests(unittest.TestCase):
    FULL = "Alfa Comércio de Alimentos Ltda"

    def test_entrada_de_uma_palavra_e_ligada_a_unica_razao_social(self) -> None:
        roster = _roster(self.FULL, "Alfa")
        text = _page(f"A {self.FULL} e a {self.FULL} recorreram.", "Depois a Alfa pagou o débito.")
        propagator, spans = _run(roster, text)
        self.assertEqual([text[item.start : item.end] for item in spans], ["Alfa"])
        self.assertEqual({item.canonical_key for item in spans}, {roster.lookup("empresa", self.FULL).key})
        self.assertEqual(propagator.links, {roster.lookup("empresa", "Alfa").key: roster.lookup("empresa", self.FULL).key})
        relinked = propagator.relink([
            type(spans[0])(start=0, end=4, entity_type="ORGANIZATION", score=0.9, recognizer="x",
                           canonical_key=roster.lookup("empresa", "Alfa").key)
        ])
        self.assertEqual(relinked[0].canonical_key, roster.lookup("empresa", self.FULL).key)

    def test_palavra_generica_na_frente_da_razao_social_e_ignorada(self) -> None:
        full = "Comércio Beltrano Têxtil Ltda"
        roster = _roster(full, "Beltrano")
        text = _page(f"A {full} e a {full} recorreram.", "A Beltrano pagou.")
        _propagator, spans = _run(roster, text)
        self.assertEqual([text[item.start : item.end] for item in spans], ["Beltrano"])

    def test_duas_razoes_sociais_candidatas_nao_ligam_e_a_recusa_e_contada(self) -> None:
        roster = _roster("Gamma Industrial Ltda", "Gamma Têxtil Ltda", "Gamma")
        text = _page("A Gamma Industrial Ltda e a Gamma Têxtil Ltda.", "A Gamma pagou.")
        propagator, spans = _run(roster, text)
        self.assertEqual(spans, [])
        self.assertEqual(propagator.links, {})
        self.assertGreaterEqual(propagator.stats["ambiguo"], 1)

    def test_dicionario_bloqueia_com_menos_de_tres_mencoes(self) -> None:
        full = "Premium Comércio de Grãos Ltda"
        roster = _roster(full, "Premium")
        two = _page(f"A {full} e a {full}.", "A Premium pagou.")
        propagator, spans = _run(roster, two)
        self.assertEqual(spans, [])
        self.assertGreaterEqual(propagator.stats["dicionario"], 1)
        three = _page(f"A {full}, a {full} e a {full}.", "A Premium pagou.")
        _propagator, spans = _run(roster, three)
        self.assertEqual(len(spans), 1)

    def test_municipio_e_outro_nome_proprio_logo_depois_ficam(self) -> None:
        full = "Joinville Metais Ltda"
        roster = _roster(full, "Joinville")
        text = _page(f"A {full}, a {full} e a {full}.", "Em Joinville choveu.")
        _propagator, spans = _run(roster, text)
        self.assertEqual(spans, [])
        full = "Delta Móveis Ltda"
        roster = _roster(full, "Delta")
        text = _page(f"A {full} e a {full}.", "A Delta Importadora Ltda é outra.")
        _propagator, spans = _run(roster, text)
        self.assertEqual(spans, [])

    def test_entrada_mantida_nao_e_ligada(self) -> None:
        roster = _roster(self.FULL, "Alfa")
        roster.lock_keep(roster.lookup("empresa", "Alfa").key)
        text = _page(f"A {self.FULL} e a {self.FULL}.", "A Alfa pagou.")
        propagator, spans = _run(roster, text)
        self.assertEqual((spans, propagator.links), ([], {}))


class MarcaCurtaNoLoteTests(_Pipeline):
    def test_marca_e_razao_social_saem_com_o_mesmo_token(self) -> None:
        full = "Alfa Comércio de Alimentos Ltda"
        text = _page(
            f"A {full} foi citada e a {full} apresentou defesa.",
            "Depois a Alfa pagou o débito.",
        )
        result, body, _audit = self.go(text, FakeDiscoverer([("empresa", full, "parte"), ("empresa", "Alfa", "parte")]))
        # v0.4.11: a forma curta de UMA razao social do lote e unida a ela no elenco (fuse_short_forms).
        self.assertIsNone(result.roster.get("empresa:alfa"))
        tokens = set(re.findall(r"\[EMPRESA_\d+\]", body))
        self.assertEqual(len(tokens), 1, body)
        self.assertNotIn("Alfa", body)


if __name__ == "__main__":
    unittest.main()

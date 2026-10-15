"""H9-g, item 4: palavra do dicionario em uso comum ("Conselho Nacional de Justica") nao propaga.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from anonimizador_juridico import EntitySpan

from anonimizador_app.roster import Mention, Roster
from anonimizador_app.variant_propagation import VariantPropagator

from test_variant_propagation import _page


def _go(full: str, kind: str, *lines: str):
    roster = Roster()
    roster.add_mention(Mention(kind=kind, text=full, canonical=full, role="parte"), source="llm")
    # A variante de uma palavra que o modelo apontou uma vez (prenome ou marca curta).
    roster.add_mention(Mention(kind=kind, text=full.split()[0], canonical=full, role="parte"), source="llm")
    text = _page(f"A {full}, a {full} e a {full} compareceram.", *lines)
    propagator = VariantPropagator(roster)
    propagator.prepare([(text, None, [])])
    # O nome completo ja esta coberto no uso real; aqui so interessam as linhas que seguem.
    start = 0
    taken = []
    for _ in range(3):
        start = text.index(full, start)
        taken.append(EntitySpan(
            start=start, end=start + len(full), entity_type="ORGANIZATION", score=0.9, recognizer="x"))
        start += len(full)
    return text, propagator, propagator.spans(text, taken)


class UsoComumTests(unittest.TestCase):
    def test_palavra_anterior_capitalizada_ou_de_nome_capitalizado_bloqueia(self) -> None:
        text, propagator, spans = _go(
            "Nacional Metalúrgica Ltda", "empresa",
            "O Conselho Nacional decidiu. A Nacional pagou.",
        )
        self.assertEqual([text[item.start : item.end] for item in spans], ["Nacional"])
        self.assertEqual(text[spans[0].start - 2 : spans[0].start], "A ")
        self.assertEqual(propagator.stats["dicionario"], 1)

    def test_de_nome_capitalizado_antes_bloqueia(self) -> None:
        text, propagator, spans = _go(
            "Real Comércio de Grãos Ltda", "empresa",
            "Informou o Valor de Real apurado. A Real pagou.",
        )
        self.assertEqual([text[item.start - 2 : item.start] for item in spans], ["A "])
        self.assertEqual(propagator.stats["dicionario"], 1)

    def test_orgaos_e_instituicoes_com_a_palavra_no_fim(self) -> None:
        for phrase, full in (
            ("a Receita Federal do Brasil", "Brasil Têxtil Importadora Ltda"),
            ("a República Federativa do Brasil", "Brasil Têxtil Importadora Ltda"),
        ):
            with self.subTest(phrase=phrase):
                text, propagator, spans = _go(full, "empresa", f"Foi oficiada {phrase}.")
                self.assertEqual(spans, [])

    def test_sr_antes_do_sobrenome_comum_nao_bloqueia(self) -> None:
        full = "Pedro Alencar Silva"
        text, _propagator, spans = _go(full, "pessoa", "Compareceu o Sr. Silva.")
        self.assertEqual([text[item.start : item.end] for item in spans], ["Silva"])


if __name__ == "__main__":
    unittest.main()

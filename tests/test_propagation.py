from __future__ import annotations

import unittest

from anonimizador_app.propagation import (
    fold_with_index,
    longest_first,
    propagable_variants,
    protected_forms,
    spans_for_roster,
)
from anonimizador_app.roster import KEEP, Mention, Roster


def _values(text: str, spans) -> list[str]:
    return [text[span.start : span.end] for span in spans]


class FoldTests(unittest.TestCase):
    def test_index_maps_back_across_accents_and_line_breaks(self) -> None:
        text = "Sr.  JOÃO\n  DA   Silva"
        folded = fold_with_index(text)

        self.assertEqual(folded.text, "sr. joao da silva")
        start = folded.text.index("joao")
        end = len(folded.text)
        original_start, original_end = folded.to_original(start, end)
        self.assertEqual(text[original_start:original_end], "JOÃO\n  DA   Silva")


class PropagationTests(unittest.TestCase):
    def _roster(self, *mentions: Mention) -> Roster:
        roster = Roster()
        for mention in mentions:
            roster.add_mention(mention)
        return roster

    def test_every_occurrence_in_any_case_or_line_break(self) -> None:
        text = (
            "O sócio Paulo Roberto Souza assinou.\n"
            "PAULO ROBERTO\nSOUZA foi citado. paulo roberto souza pagou."
        )
        roster = self._roster(
            Mention(kind="pessoa", text="Paulo Roberto Souza", role="socio")
        )
        spans = spans_for_roster(text, roster.entries)

        self.assertEqual(len(spans), 3)
        self.assertEqual(
            _values(text, spans),
            ["Paulo Roberto Souza", "PAULO ROBERTO\nSOUZA", "paulo roberto souza"],
        )
        self.assertEqual({span.canonical_key for span in spans}, {roster.entries[0].key})

    def test_no_match_inside_a_longer_word(self) -> None:
        text = "ACME COMERCIOS LTDA nao e a ACME COMERCIO."
        roster = self._roster(Mention(kind="empresa", text="ACME COMERCIO", role="parte"))
        self.assertEqual(_values(text, spans_for_roster(text, roster.entries)), ["ACME COMERCIO"])

    def test_company_core_and_trailing_suffix(self) -> None:
        text = "Contra UNIÃO COMERCIAL DE GRÃOS LTDA. A União Comercial de Grãos EIRELI pagou."
        roster = self._roster(
            Mention(kind="empresa", text="UNIÃO COMERCIAL DE GRÃOS LTDA", role="terceiro")
        )
        spans = spans_for_roster(text, roster.entries)

        self.assertEqual(
            _values(text, spans),
            ["UNIÃO COMERCIAL DE GRÃOS LTDA", "União Comercial de Grãos EIRELI"],
        )
        # "União" sozinha nao e variante: e termo protegido.
        self.assertNotIn("uniao", propagable_variants(roster.entries[0], protected_forms()))

    def test_single_word_variant_only_where_anchored(self) -> None:
        text = "O sócio SOUZA assinou. O Min. Souza relatou o recurso."
        roster = self._roster(
            Mention(kind="pessoa", text="SOUZA", role="socio", canonical="Paulo Roberto Souza")
        )
        key = roster.entries[0].key
        start = text.index("SOUZA")
        spans = spans_for_roster(text, roster.entries, anchored=[(start, start + 5, key)])

        self.assertEqual(_values(text, spans), ["SOUZA"])
        self.assertEqual(spans[0].start, start)

    def test_kept_entry_and_protected_terms_never_become_spans(self) -> None:
        text = "A Juíza LUCIANA MARCHETTI decidiu. Fazenda Nacional. SISBAJUD."
        roster = self._roster(
            Mention(kind="pessoa", text="LUCIANA MARCHETTI", role="magistrado"),
            Mention(kind="empresa", text="Fazenda Nacional", role="parte"),
        )
        self.assertEqual(roster.entries[0].action, KEEP)
        self.assertEqual(spans_for_roster(text, roster.entries), [])

    def test_user_exclusion_is_protected(self) -> None:
        text = "Cooperativa Agro Sul pagou."
        roster = self._roster(Mention(kind="empresa", text="Cooperativa Agro Sul", role="parte"))
        spans = spans_for_roster(text, roster.entries, extra_keep=["Cooperativa Agro Sul"])
        self.assertEqual(spans, [])

    def test_longest_mention_wins_the_overlap(self) -> None:
        text = "JOSÉ CARLOS NUNES ME foi executada; JOSÉ CARLOS NUNES e o titular."
        roster = self._roster(
            Mention(kind="pessoa", text="JOSÉ CARLOS NUNES", role="socio"),
            Mention(kind="empresa", text="JOSÉ CARLOS NUNES ME", role="parte",
                    canonical="José Carlos Nunes ME"),
        )
        spans = longest_first(spans_for_roster(text, roster.entries))
        self.assertEqual(
            [(text[span.start : span.end], span.entity_type) for span in spans],
            [("JOSÉ CARLOS NUNES ME", "ORGANIZATION"), ("JOSÉ CARLOS NUNES", "PERSON")],
        )


if __name__ == "__main__":
    unittest.main()

"""Identidade no lote (fila 3.x E13): um token por entidade, sem juntar homonimos. Dados inventados."""

from __future__ import annotations

import unittest

from anonimizador_juridico import EntitySpan, PseudonymMap, pseudonymize
from anonimizador_app.batch_propagation import BatchRoster, norm_core
from anonimizador_app.propagation import fold_with_index


def _roster(*items: tuple[str, str, int]) -> BatchRoster:
    """(grafia, tipo, mencoes): cada mencao e uma captura da deteccao."""

    roster = BatchRoster()
    for value, entity_type, count in items:
        for _ in range(count):
            roster.add(value, [EntitySpan(0, len(value), entity_type, 0.9, "NerLocal")])
    return roster


def _same(roster: BatchRoster, entity_type: str, *values: str) -> bool:
    return len({roster.key_for(entity_type, value) for value in values}) == 1


P, O = "PERSON", "ORGANIZATION"


class HyphenAndPunctuationTests(unittest.TestCase):
    def test_hyphen_split_by_ocr_is_the_same_company(self) -> None:
        roster = _roster(
            ("ACME ME-TAIS LTDA", O, 1),
            ("ACME METAIS LTDA", O, 3),
            ("ACME ME TAIS LTDA", O, 1),
        )
        self.assertTrue(_same(roster, O, "ACME ME-TAIS LTDA", "ACME METAIS LTDA", "ACME ME TAIS LTDA"))
        self.assertEqual(len(roster.entities()), 1)

    def test_hyphen_at_the_end_of_the_line_is_undone(self) -> None:
        self.assertEqual(norm_core("empresa", "ACME ME-\nTAIS LTDA"), "acme metais")
        self.assertEqual(fold_with_index("ACME ME-\nTAIS LTDA").text, "acme metais ltda")
        # Hifen com espaco dos dois lados e separador, nao quebra de palavra.
        self.assertEqual(fold_with_index("ACME - MATRIZ").text, "acme - matriz")

    def test_name_split_over_two_lines_is_found_in_the_text(self) -> None:
        first = "A empresa ACME METAIS LTDA foi citada."
        second = "Citada: ACME ME-\nTAIS LTDA, na pessoa do socio."
        roster = BatchRoster()
        detected = [EntitySpan(first.index("ACME"), first.index(" foi"), O, 0.9, "NerLocal")]
        roster.add(first, detected)
        roster.review([first, second])
        mapping = PseudonymMap()
        pseudonymize(first, roster.apply(first, detected), mapping=mapping)
        out = pseudonymize(second, roster.apply(second, []), mapping=mapping).text
        self.assertEqual(out, "Citada: [EMPRESA_1], na pessoa do socio.")

    def test_stray_ocr_punctuation_does_not_make_a_new_company(self) -> None:
        roster = _roster(
            ("ACME FICTICIA DE FERRO LTDA", O, 4),
            ("ACME FICTICIA DE} FERRO LTDA", O, 1),
            ("ACME. FICTICIA DE FERRO LTDA", O, 1),
        )
        self.assertEqual(len(roster.entities()), 1)

    def test_lowercase_word_dragged_into_the_capture_is_dropped_at_the_edge(self) -> None:
        roster = _roster(
            ("Fulano Beltrano Ficticio", P, 3),
            ("a Fulano Beltrano Ficticio", P, 1),
            ("Fulano Beltrano Ficticio e", P, 1),
        )
        self.assertEqual(len(roster.entities()), 1)

    def test_all_lowercase_name_keeps_its_short_first_name(self) -> None:
        # "ana" e prenome, nao lixo de borda: so se corta em texto de caixa mista.
        self.assertEqual(norm_core("pessoa", "ana beltrana ficticia"), "ana beltrana ficticia")


class HomonymsMustNotMergeTests(unittest.TestCase):
    def test_gender_pair_of_first_names_stays_apart(self) -> None:
        roster = _roster(
            ("ADRIANO FICTICIO TAVARES", P, 5),
            ("ADRIANA FICTICIO TAVARES", P, 5),
        )
        self.assertEqual(len(roster.entities()), 2)

    def test_first_name_one_letter_apart_needs_one_of_them_to_be_rare(self) -> None:
        both_seen = _roster(("MARINA FICTICIA TAVARES", P, 6), ("MARITA FICTICIA TAVARES", P, 5))
        self.assertEqual(len(both_seen.entities()), 2)
        ocr_noise = _roster(("MARCELO FICTICIO TAVARES", P, 12), ("MARCELO FICTICIO TAVARES", P, 0))
        self.assertEqual(len(ocr_noise.entities()), 1)
        typo = _roster(("CAROLINA FICTICIA TAVARES", P, 12), ("CARQLINA FICTICIA TAVARES", P, 1))
        self.assertEqual(len(typo.entities()), 1)

    def test_son_and_father_are_two_people(self) -> None:
        roster = _roster(
            ("FULANO BELTRANO TAVARES", P, 8),
            ("FULANO BELTRANO TAVARES FILHO", P, 4),
            ("FULANO BELTRANO", P, 2),
        )
        self.assertEqual(len(roster.entities()), 3)
        self.assertFalse(_same(roster, P, "FULANO BELTRANO TAVARES", "FULANO BELTRANO TAVARES FILHO"))

    def test_short_form_is_not_given_to_one_of_two_real_people(self) -> None:
        # Os dois com apoio parecido: a forma curta cabe nos dois, nao e de nenhum.
        roster = _roster(
            ("FULANO BELTRANO TAVARES", P, 8),
            ("FULANO BELTRANO TAVARES SOUZA", P, 7),
            ("FULANO BELTRANO TAVARES LIMA", P, 7),
            ("FULANO BELTRANO TAVARES", P, 0),
        )
        roster.add("FULANO BELTRANO TAVARES", [EntitySpan(0, 23, P, 0.9, "NerLocal")])
        self.assertFalse(_same(roster, P, "FULANO BELTRANO TAVARES SOUZA", "FULANO BELTRANO TAVARES LIMA"))

    def test_company_branches_and_sister_activities_stay_apart(self) -> None:
        roster = _roster(
            ("ACME FICTICIA COMERCIO LTDA", O, 6),
            ("ACME FICTICIA COMERCIAL LTDA", O, 6),
            ("ACME FICTICIA INDUSTRIA LTDA", O, 6),
            ("ACME FICTICIA INDUSTRIAL LTDA", O, 6),
            ("ACME FICTICIA FILIAL01 LTDA", O, 6),
            ("ACME FICTICIA FILIAL02 LTDA", O, 6),
        )
        self.assertEqual(len(roster.entities()), 6)

    def test_sister_companies_with_the_same_brand_are_not_one_company(self) -> None:
        roster = _roster(
            ("ACME FICTICIA COMERCIO DE FERRO LTDA", O, 6),
            ("ACME FICTICIA INDUSTRIA DE FERRO LTDA", O, 6),
            ("ACME FICTICIA PARTICIPACOES LTDA", O, 6),
        )
        self.assertEqual(len(roster.entities()), 3)

    def test_two_word_prefix_shared_by_two_companies_stays_alone(self) -> None:
        roster = _roster(
            ("ACME FICTICIA DE FERRO LTDA", O, 8),
            ("ACME FICTICIA DE ACO LTDA", O, 7),
            ("ACME FICTICIA", O, 3),
        )
        self.assertEqual(len(roster.entities()), 3)


class IdentityGainsTests(unittest.TestCase):
    def test_short_form_follows_the_dominant_full_name_and_ignores_glued_captures(self) -> None:
        roster = _roster(
            ("FULANA BELTRANA FICTICIA TAVARES", P, 14),
            ("FULANA BELTRANA FICTICIA", P, 20),
            # Duas pessoas colhidas juntas na linha de assinaturas: nao e a identidade de ninguem.
            ("FULANA BELTRANA FICTICIA CICRANO FICTICIO", P, 1),
            ("CICRANO FICTICIO", P, 6),
            ("FULANA BELTRANA FICTICIA SOUZA", P, 1),
        )
        # v0.4.10: nome de 3+ palavras que e o comeco de outro mais longo fica separado (mae x filha com o sobrenome
        # do marido: matriz v0.4.9, sintetico).
        self.assertFalse(_same(roster, P, "FULANA BELTRANA FICTICIA", "FULANA BELTRANA FICTICIA TAVARES"))
        self.assertFalse(_same(roster, P, "FULANA BELTRANA FICTICIA CICRANO FICTICIO", "CICRANO FICTICIO"))
        self.assertFalse(_same(roster, P, "FULANA BELTRANA FICTICIA SOUZA", "FULANA BELTRANA FICTICIA TAVARES"))

    def test_short_form_with_weak_support_stays_apart_when_two_heads_are_alive(self) -> None:
        roster = _roster(
            ("FULANA BELTRANA FICTICIA TAVARES", P, 6),
            ("FULANA BELTRANA FICTICIA SOUZA", P, 4),
            ("FULANA BELTRANA FICTICIA", P, 9),
        )
        self.assertFalse(_same(roster, P, "FULANA BELTRANA FICTICIA", "FULANA BELTRANA FICTICIA TAVARES"))

    def test_word_split_in_two_by_ocr_is_the_same_person(self) -> None:
        roster = _roster(
            ("FULANO CICRANO FICTICIO TAVARES", P, 6),
            ("FULANO CICRANO FICTICIO TAVA RES", P, 1),
        )
        self.assertEqual(len(roster.entities()), 1)

    def test_ocr_typo_in_a_surname_is_the_same_person(self) -> None:
        roster = _roster(
            ("FULANO BELTRANO DE TAVARES", P, 6),
            ("FULANO BELTRANO DE TAVARFS", P, 1),
        )
        self.assertEqual(len(roster.entities()), 1)

    def test_connective_difference_is_the_same_company(self) -> None:
        roster = _roster(("ACME FICTICIA E FERRO LTDA", O, 4), ("ACME FICTICIA FERRO LTDA", O, 1))
        self.assertEqual(len(roster.entities()), 1)


class BrandTests(unittest.TestCase):
    """Marca de uma palavra usada sozinha: token proprio, sem fundir com empresa nenhuma."""

    FULL = "A executada ZYXWAVE COMERCIO DE FERRO LTDA foi citada. "

    def _batch(self, bare_text: str, repeat: int = 25) -> tuple[BatchRoster, str, str]:
        first = self.FULL * repeat
        second = bare_text
        roster = BatchRoster()
        spans = []
        for index in range(repeat):
            start = first.index("ZYXWAVE", index * len(self.FULL))
            spans.append(EntitySpan(start, start + len("ZYXWAVE COMERCIO DE FERRO LTDA"), O, 0.9, "NerLocal"))
        roster.add(first, spans)
        roster.review([first, second])
        return roster, first, second

    def test_bare_brand_of_a_single_company_takes_the_token_of_that_company(self) -> None:
        roster, first, second = self._batch("Intimada a ZYXWAVE, que nao pagou. Falou a ZYXWAVE.")
        spans = roster.apply(second, [])
        self.assertEqual([second[s.start : s.end] for s in spans], ["ZYXWAVE", "ZYXWAVE"])
        mapping = PseudonymMap()
        pseudonymize(first, roster.apply(first, []), mapping=mapping)
        out = pseudonymize(second, spans, mapping=mapping).text
        self.assertEqual(out, "Intimada a [EMPRESA_1], que nao pagou. Falou a [EMPRESA_1].")

    def test_bare_brand_shared_by_two_companies_gets_its_own_token_and_merges_nothing(self) -> None:
        first = ("A executada ZYXWAVE COMERCIO DE FERRO LTDA e a ZYXWAVE INDUSTRIA DE ACO LTDA foram citadas. ") * 14
        roster = BatchRoster()
        spans = []
        cursor = 0
        for _ in range(14):
            for name in ("ZYXWAVE COMERCIO DE FERRO LTDA", "ZYXWAVE INDUSTRIA DE ACO LTDA"):
                start = first.index(name, cursor)
                spans.append(EntitySpan(start, start + len(name), O, 0.9, "NerLocal"))
                cursor = start + 5
        roster.add(first, spans)
        second = "Intimada a ZYXWAVE."
        roster.review([first, second])
        mapping = PseudonymMap()
        pseudonymize(first, roster.apply(first, []), mapping=mapping)
        out = pseudonymize(second, roster.apply(second, []), mapping=mapping).text
        self.assertEqual(out, "Intimada a [EMPRESA_3].")  # nem a 1 nem a 2: de quem e a marca, o lote nao diz
        self.assertEqual(len(roster.entities()), 2)

    def test_brand_found_by_the_detection_and_by_the_propagation_share_the_token(self) -> None:
        roster, first, second = self._batch("Intimada a ZYXWAVE. Falou a ZYXWAVE.")
        roster.add(second, [EntitySpan(second.index("ZYXWAVE"), second.index("ZYXWAVE") + 7, O, 0.9, "NerLocal")])
        detected = [EntitySpan(second.index("ZYXWAVE"), second.index("ZYXWAVE") + 7, O, 0.9, "NerLocal")]
        mapping = PseudonymMap()
        pseudonymize(first, roster.apply(first, []), mapping=mapping)
        out = pseudonymize(second, roster.apply(second, detected), mapping=mapping).text
        self.assertEqual(out, "Intimada a [EMPRESA_1]. Falou a [EMPRESA_1].")

    def test_brand_written_in_lowercase_is_left_alone(self) -> None:
        roster, _first, second = self._batch("o zyxwave passou a administrar.")
        self.assertEqual(roster.apply(second, []), [])

    def test_word_that_the_batch_also_writes_in_lowercase_is_a_common_word(self) -> None:
        # "Aco" no meio da frase: palavra comum, nao marca.
        roster, _first, second = self._batch("Intimada a Zyxwave. O zyxwave comum. Outro zyxwave. Mais um zyxwave.")
        self.assertEqual(roster.apply(second, []), [])

    def test_word_used_alone_far_more_than_in_the_full_name_is_not_a_brand(self) -> None:
        roster, _first, second = self._batch("Zyxwave. " * 60)  # 60 > 2 x 25 mencoes do nome completo
        self.assertEqual(roster.apply(second, []), [])

    def test_brand_used_alone_a_fair_number_of_times_is_still_a_brand(self) -> None:
        roster, _first, second = self._batch("Zyxwave. " * 40)  # 40 <= 2 x 25
        self.assertEqual(len(roster.apply(second, [])), 40)

    def test_word_that_is_also_a_surname_in_the_batch_is_not_a_brand(self) -> None:
        first = self.FULL * 25 + "O socio FULANO ZYXWAVE assinou. "
        roster = BatchRoster()
        spans = [
            EntitySpan(first.index("ZYXWAVE", i * len(self.FULL)), first.index("ZYXWAVE", i * len(self.FULL)) + 30, O, 0.9, "NerLocal")
            for i in range(25)
        ]
        spans.append(EntitySpan(first.index("FULANO"), first.index("FULANO") + 14, P, 0.9, "NerLocal"))
        roster.add(first, spans)
        second = "Intimada a ZYXWAVE."
        roster.review([first, second])
        self.assertEqual(roster.apply(second, []), [])

    def test_brand_needs_enough_full_name_mentions(self) -> None:
        roster, _first, second = self._batch("Intimada a ZYXWAVE.", repeat=5)
        self.assertEqual(roster.apply(second, []), [])

    def test_activity_word_is_never_a_brand(self) -> None:
        first = "A executada COMERCIO FICTICIO DE FERRO LTDA foi citada. " * 30
        roster = BatchRoster()
        spans = []
        cursor = 0
        for _ in range(30):
            start = first.index("COMERCIO", cursor)
            spans.append(EntitySpan(start, start + len("COMERCIO FICTICIO DE FERRO LTDA"), O, 0.9, "NerLocal"))
            cursor = start + 10
        roster.add(first, spans)
        second = "O Comercio local."
        roster.review([first, second])
        self.assertEqual(roster.apply(second, []), [])


if __name__ == "__main__":
    unittest.main()

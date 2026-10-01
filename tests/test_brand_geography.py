"""Marca de uma palavra x cidade e estado (endurecimento B3): foro e endereco nao viram empresa.

Dados inventados. A lista de municipios e a do IBGE guardada no pacote (sem rede).
"""

from __future__ import annotations

import unittest

from anonimizador_app.batch_propagation import BatchRoster
from anonimizador_app.geo_names import (
    geo_context,
    geo_name_count,
    inside_geo_name,
    is_geo_word,
)
from anonimizador_juridico import EntitySpan, PseudonymMap, pseudonymize

O = "ORGANIZATION"


def _batch(full_name: str, other: str, repeat: int = 25) -> tuple[BatchRoster, str, str, PseudonymMap]:
    """Empresa citada `repeat` vezes pelo nome completo (so ela, no primeiro arquivo) + texto `other`."""

    line = f"A executada {full_name} foi citada. "
    first = line * repeat
    roster = BatchRoster()
    spans = []
    for index in range(repeat):
        start = first.index(full_name, index * len(line))
        spans.append(EntitySpan(start, start + len(full_name), O, 0.9, "NerLocal"))
    roster.add(first, spans)
    roster.review([first, other])
    mapping = PseudonymMap()
    pseudonymize(first, roster.apply(first, []), mapping=mapping)
    out = pseudonymize(other, roster.apply(other, []), mapping=mapping).text
    return roster, first, out, mapping


class GeoNamesTests(unittest.TestCase):
    def test_official_list_is_loaded(self) -> None:
        self.assertGreater(geo_name_count(), 5000)

    def test_municipality_and_state_names(self) -> None:
        self.assertTrue(is_geo_word("Joinville"))
        self.assertTrue(is_geo_word("JOINVILLE"))
        self.assertFalse(is_geo_word("São"))  # so "Sao Paulo" inteiro; a palavra sozinha nao
        self.assertTrue(is_geo_word("Bahia"))
        self.assertTrue(is_geo_word("Goiás"))
        self.assertFalse(is_geo_word("Zyxwave"))

    def test_compound_name_is_recognised_inside_text(self) -> None:
        text = "Estado de Santa Catarina."
        start = text.index("Santa")
        self.assertTrue(inside_geo_name(text, start, start + 5))
        text = "A Santa Fictícia pagou."
        start = text.index("Santa")
        self.assertFalse(inside_geo_name(text, start, start + 5))

    def test_court_and_address_context(self) -> None:
        def at(text: str, word: str) -> bool:
            start = text.index(word)
            return geo_context(text, start, start + len(word))

        self.assertTrue(at("Juízo da 3ª Vara Federal de Zyxwave", "Zyxwave"))
        self.assertTrue(at("1ª Vara de Execuções Fiscais de Zyxwave", "Zyxwave"))
        self.assertTrue(at("Subseção Judiciária de Zyxwave/SC", "Zyxwave"))
        self.assertTrue(at("Comarca de Zyxwave", "Zyxwave"))
        self.assertTrue(at("no Município de Zyxwave", "Zyxwave"))
        self.assertTrue(at("Cidade: Zyxwave", "Zyxwave"))
        self.assertTrue(at("Rua Fictícia, 10, Zyxwave - SC", "Zyxwave"))
        self.assertTrue(at("Zyxwave, 12 de março de 2026", "Zyxwave"))
        self.assertTrue(at("Zyxwave 88000-000 sede", "Zyxwave"))
        self.assertTrue(at("PODER JUDICIÁRIO SUBSEÇÃO JUDICIÁRIA ZYXWAVE", "ZYXWAVE"))
        self.assertTrue(at("SUBSEÇÃO JUDICIÁRIA DE ZYXWAVE", "ZYXWAVE"))
        self.assertFalse(at("A executada Zyxwave não pagou.", "Zyxwave"))
        self.assertFalse(at("Intimada a ZYXWAVE para pagar.", "ZYXWAVE"))
        self.assertFalse(at("O ofício da Zyxwave foi juntado.", "Zyxwave"))


class BrandGeographyTests(unittest.TestCase):
    def test_company_whose_first_word_is_a_city_does_not_make_the_court_a_company(self) -> None:
        other = (
            "Juizo da 3a Vara Federal de Joinville. Subsecao Judiciaria de Joinville/SC. "
            "Endereco: Rua Fict, 10, Joinville - SC. A executada Joinville foi citada. "
        ) * 8
        roster, _first, out, _mapping = _batch("JOINVILLE METAIS LTDA", other)
        self.assertEqual(roster._brands, {})
        self.assertIn("joinville", roster.brand_geo_words)
        self.assertEqual(out, other)  # nada foi trocado: nem foro, nem endereco

    def test_rejected_city_is_counted_for_the_audit(self) -> None:
        other = "A Joinville foi intimada. Vara Federal de Joinville. " * 4
        roster, *_rest = _batch("JOINVILLE METAIS LTDA", other)
        self.assertEqual(list(roster.brand_geo_words), ["joinville"])
        self.assertGreater(roster.brand_geo_words["joinville"], 0)

    def test_state_name_is_never_a_brand(self) -> None:
        other = "Estado da Bahia. A BAHIA foi citada. " * 5
        roster, *_rest = _batch("BAHIA FICTICIA LTDA", other)
        self.assertEqual(roster._brands, {})
        self.assertIn("bahia", roster.brand_geo_words)

    def test_name_shared_with_a_compound_state_keeps_the_place_and_tokenises_the_company(self) -> None:
        other = "Estado de Santa Catarina. Santa Catarina. Intimada a Santa, que nao pagou. " * 4
        roster, _first, out, _mapping = _batch("SANTA FICTICIA LTDA", other)
        self.assertIn("santa", roster._brands)
        self.assertIn("Estado de Santa Catarina. Santa Catarina.", out)
        self.assertIn("Intimada a [EMPRESA_1], que nao pagou.", out)

    def test_court_context_of_a_non_municipal_word_is_refused_even_in_caps(self) -> None:
        other = (
            "SUBSEÇÃO JUDICIÁRIA DE ZYXWAVE\nVara Federal de Zyxwave. Intimada a ZYXWAVE para pagar.\n"
        ) * 6
        roster, _first, out, _mapping = _batch("ZYXWAVE COMERCIO DE FERRO LTDA", other)
        self.assertIn("zyxwave", roster._brands)
        self.assertIn("SUBSEÇÃO JUDICIÁRIA DE ZYXWAVE", out)
        self.assertIn("Vara Federal de Zyxwave.", out)
        self.assertIn("Intimada a [EMPRESA_1] para pagar.", out)
        self.assertEqual(roster.brand_context_rejected["zyxwave"], 12)
        self.assertEqual(roster.brand_applied["zyxwave"], 6)

    def test_brand_inside_a_protected_term_is_left_alone(self) -> None:
        roster = BatchRoster(extra_keep=["Zyxwave Foundation"])
        line = "A executada ZYXWAVE COMERCIO DE FERRO LTDA foi citada. "
        first = line * 25
        spans = [
            EntitySpan(
                first.index("ZYXWAVE", index * len(line)),
                first.index("ZYXWAVE", index * len(line)) + len("ZYXWAVE COMERCIO DE FERRO LTDA"),
                O,
                0.9,
                "NerLocal",
            )
            for index in range(25)
        ]
        roster.add(first, spans)
        other = "Doou a Zyxwave Foundation. Intimada a ZYXWAVE. " * 3
        roster.review([first, other])
        found = roster.apply(other, [])
        self.assertEqual([other[span.start : span.end] for span in found], ["ZYXWAVE"] * 3)

    def test_audit_lines_never_carry_the_word_outside_the_private_report(self) -> None:
        other = "Intimada a ZYXWAVE. Vara Federal de Zyxwave. " * 4
        roster, _first, _out, mapping = _batch("ZYXWAVE COMERCIO DE FERRO LTDA", other)
        public = "\n".join(roster.brand_report(mapping))
        private = "\n".join(roster.brand_report(mapping, private=True))
        self.assertNotIn("zyxwave", public.casefold())
        self.assertIn("[EMPRESA_1]", public)
        self.assertIn("zyxwave", private)
        self.assertIn("[EMPRESA_1]", private)


if __name__ == "__main__":
    unittest.main()

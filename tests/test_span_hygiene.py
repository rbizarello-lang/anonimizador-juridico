from __future__ import annotations

import unittest

from anonimizador_juridico import EntitySpan

from anonimizador_app.span_hygiene import sanitize_spans


def _span(
    text: str,
    value: str,
    entity_type: str,
    recognizer: str = "GLiNER",
) -> EntitySpan:
    start = text.index(value)
    return EntitySpan(start, start + len(value), entity_type, 0.9, recognizer)


class SpanHygieneTests(unittest.TestCase):
    def test_html_comment_is_not_a_person(self) -> None:
        text = "Trecho. <!-- pagina-origem: 13 --> Uniao."
        comment = "<!-- pagina-origem: 13 -->"
        kept = sanitize_spans(text, [_span(text, comment, "PERSON")])
        self.assertEqual(kept, [])
        self.assertIn("<!-- pagina-origem: 13 -->", text)

    def test_eight_line_location_is_clipped(self) -> None:
        lines = [f"clausula contratual linha {index:02d} texto" for index in range(8)]
        block = "\n".join(lines)
        text = f"Sede: {block} e Uniao no polo."
        kept = sanitize_spans(text, [_span(text, block, "LOCATION")])
        self.assertEqual(len(kept), 1)
        value = text[kept[0].start : kept[0].end]
        self.assertLessEqual(value.count("\n"), 1)
        self.assertLess(len(value), len(block))
        self.assertNotIn("linha 07", value)

    def test_field_label_only_is_dropped(self) -> None:
        text = "Saldo Conta: 1271/635/8659-0 Uniao."
        kept = sanitize_spans(text, [_span(text, "Conta:", "BR_CONTA_BANCARIA")])
        self.assertEqual(kept, [])
        self.assertIn("Conta:", text)

    def test_one_newline_in_company_name_is_kept(self) -> None:
        name = "NORTE INVENTADO\nCOMERCIO LTDA"
        text = f"{name} no polo passivo. Uniao."
        kept = sanitize_spans(
            text,
            [_span(text, name, "ORGANIZATION", "ListaDeAlvos")],
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(text[kept[0].start : kept[0].end], name)

    def test_two_newlines_in_person_are_clipped(self) -> None:
        block = "Nome Ficticio\nsegunda linha\nterceira linha alheia"
        text = f"{block} Uniao."
        kept = sanitize_spans(text, [_span(text, block, "PERSON")])
        self.assertEqual(len(kept), 1)
        value = text[kept[0].start : kept[0].end]
        self.assertEqual(value, "Nome Ficticio\nsegunda linha")

    def test_date_is_not_swallowed_by_location(self) -> None:
        block = "Rua das Palmeiras, 100/12/1983"
        text = f"Sediada a {block}. Uniao."
        kept = sanitize_spans(text, [_span(text, block, "LOCATION")])
        self.assertEqual(len(kept), 1)
        value = text[kept[0].start : kept[0].end]
        self.assertEqual(value, "Rua das Palmeiras, 100")
        self.assertNotIn("1983", value)

    def test_cnpj_line_break_span_is_kept(self) -> None:
        text = "inscrita no CNPJ/MF n° 01.294.953/0001-\n72, estabelecida"
        start = text.index("01.294.953")
        end = text.index("72,") + 2
        kept = sanitize_spans(
            text,
            [EntitySpan(start, end, "BR_CNPJ", 0.9, "BrCnpjRecognizer")],
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].end, end)
        self.assertIn("\n", text[kept[0].start : kept[0].end])

    def test_long_target_company_is_not_size_capped(self) -> None:
        name = "A" * 200
        text = f"{name} Uniao."
        kept = sanitize_spans(
            text,
            [_span(text, name, "ORGANIZATION", "ListaDeAlvos")],
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].end - kept[0].start, 200)

    def test_pagina_origem_without_comment_is_dropped(self) -> None:
        text = "Trecho. pagina-origem: 13 Uniao."
        kept = sanitize_spans(text, [_span(text, "pagina-origem: 13", "PERSON")])
        self.assertEqual(kept, [])


if __name__ == "__main__":
    unittest.main()

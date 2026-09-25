from __future__ import annotations

import unittest

from anonimizador_juridico import ExtractedPage, pages_to_markdown


class ExtractionTests(unittest.TestCase):
    def test_page_boundaries_are_explicit(self) -> None:
        markdown = pages_to_markdown(
            [
                ExtractedPage(number=1, text="Evento 1 - Peticao"),
                ExtractedPage(number=2, text="Evento 2 - Decisao"),
            ]
        )
        self.assertIn("<!-- pagina-origem: 1 -->", markdown)
        self.assertIn("<!-- pagina-origem: 2 -->", markdown)
        self.assertLess(markdown.index("Evento 1"), markdown.index("Evento 2"))
        self.assertNotIn("arquivo.pdf", markdown)


if __name__ == "__main__":
    unittest.main()

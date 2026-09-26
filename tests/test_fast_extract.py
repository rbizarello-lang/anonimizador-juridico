from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.fast_extract import extract_fast

HAS_PYMUPDF = importlib.util.find_spec("pymupdf") is not None
HAS_DOCX = importlib.util.find_spec("docx") is not None


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class FastPdfTests(unittest.TestCase):
    def _pdf(self, directory: Path) -> Path:
        import pymupdf

        path = directory / "autos.pdf"
        with pymupdf.open() as document:
            first = document.new_page()
            first.insert_text((72, 72), "Peticao inicial da Uniao.")
            second = document.new_page()
            second.insert_text((72, 72), "Conforme o evento 12, a executada foi citada.")
            document.set_toc([[1, "Evento 1 - INIC1", 1], [1, "Evento 12 - CERT1", 2]])
            document.save(path)
        return path

    def test_page_anchors_and_bookmarks_become_geography(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extracted = extract_fast(self._pdf(Path(tmp)))

        text = extracted.markdown
        self.assertEqual(extracted.page_count, 2)
        self.assertEqual(extracted.extractor, "pymupdf")
        self.assertIn("<!-- pagina-origem: 1 -->\n## Evento 1 - INIC1", text)
        self.assertIn("<!-- pagina-origem: 2 -->\n## Evento 12 - CERT1", text)

    def test_event_mentioned_mid_sentence_is_not_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = extract_fast(self._pdf(Path(tmp))).markdown

        self.assertIn("Conforme o evento 12, a executada foi citada.", text)


@unittest.skipUnless(HAS_DOCX, "python-docx nao instalado")
class FastDocxTests(unittest.TestCase):
    def test_table_stays_where_it_is_cited(self) -> None:
        from docx import Document

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "peca.docx"
            document = Document()
            document.add_heading("Dos fatos", level=1)
            document.add_paragraph("Os debitos estao na tabela abaixo.")
            table = document.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "CDA"
            table.cell(0, 1).text = "Valor"
            table.cell(1, 0).text = "80 6 24 000001-01"
            table.cell(1, 1).text = "R$ 1.000,00"
            document.add_paragraph("Do direito.")
            document.save(path)
            text = extract_fast(path).markdown

        self.assertIn("# Dos fatos", text)
        self.assertLess(text.index("tabela abaixo"), text.index("| CDA | Valor |"))
        self.assertLess(text.index("| CDA | Valor |"), text.index("Do direito."))
        self.assertIn("| 80 6 24 000001-01 | R$ 1.000,00 |", text)


class FastTextTests(unittest.TestCase):
    def test_markdown_is_kept_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nota.md"
            path.write_text("# Titulo\n\nTexto do evento 3.\n", encoding="utf-8")
            text = extract_fast(path).markdown

        self.assertEqual(text, "# Titulo\n\nTexto do evento 3.\n")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.ocr_pages import apply_page_ocr, page_needs_ocr


class PageOcrTests(unittest.TestCase):
    def test_native_page_with_scan_is_not_ocr_target(self) -> None:
        self.assertFalse(page_needs_ocr(1800, 0.95))

    def test_splices_ocr_into_thin_page_only(self) -> None:
        import pymupdf

        markdown = (
            "<!-- pagina-origem: 1 -->\n"
            "Peticao nativa com bastante texto da Uniao e valores em R$.\n"
            "<!-- pagina-origem: 2 -->\n"
            "Processo Evento 1 CONTRSOCIAL9 Pagina 2\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "misto.pdf"
            document = pymupdf.open()
            first = document.new_page()
            first.insert_text((72, 72), "Peticao nativa com bastante texto da Uniao e valores em R$ " * 8)
            second = document.new_page()
            second.insert_text((72, 72), "Processo Evento 1 CONTRSOCIAL9 Pagina 2")
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 280), False)
            pix.clear_with(180)
            second.insert_image(second.rect, pixmap=pix)
            document.save(path)
            document.close()
            filled = apply_page_ocr(
                path,
                markdown,
                recognize=lambda _image: "CONTRATO SOCIAL da empresa ficticia",
            )
        self.assertEqual(filled.pages_read, 1)
        self.assertIn("CONTRATO SOCIAL da empresa ficticia", filled.markdown)
        self.assertIn("ocr-paginas: 1", filled.markdown)
        native_block = filled.markdown.split("<!-- pagina-origem: 2 -->")[0]
        self.assertNotIn("CONTRATO SOCIAL da empresa ficticia", native_block)

    def test_reader_failure_marks_markdown(self) -> None:
        import pymupdf

        def broken(_image: bytes) -> str:
            raise ValueError("modelo indisponivel")

        markdown = "<!-- pagina-origem: 1 -->\nEvento 1 OUT2\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((72, 72), "Evento 1 OUT2")
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 280), False)
            pix.clear_with(180)
            page.insert_image(page.rect, pixmap=pix)
            document.save(path)
            document.close()
            result = apply_page_ocr(path, markdown, recognize=broken)
        self.assertEqual(result.pages_read, 0)
        self.assertIn("ocr-falhas: 1", result.markdown)


if __name__ == "__main__":
    unittest.main()

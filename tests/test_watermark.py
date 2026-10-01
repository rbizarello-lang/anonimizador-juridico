from __future__ import annotations

import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.fast_extract import extract_fast

HAS_PYMUPDF = importlib.util.find_spec("pymupdf") is not None

# Cor do selo do editor (RGB 255, 200, 0), em fracao para o PyMuPDF.
GOLD = (1.0, 200 / 255, 0.0)
RING_TEXT = "PDF-XChange Editor www.tracker-software.com"


def _stamp_badge(page, cx: float, cy: float, *, radius: float = 36.0, ring_radius: float = 28.0) -> None:
    """Selo ficticio: disco dourado e preto, "Click to BUY NOW!" em diagonal e letras em circulo."""

    import pymupdf

    page.draw_circle((cx, cy), radius, color=None, fill=GOLD)
    page.draw_circle((cx, cy), radius - 7, color=None, fill=(0, 0, 0))
    page.draw_circle((cx, cy), radius - 18, color=None, fill=(1, 1, 1))
    origin = pymupdf.Point(cx - 20, cy + 20)
    page.insert_text(
        origin, "Click to BUY NOW!", fontsize=6.4, color=GOLD, morph=(origin, pymupdf.Matrix(45))
    )
    for index, char in enumerate(RING_TEXT):
        angle = 2 * math.pi * index / len(RING_TEXT)
        point = pymupdf.Point(cx + ring_radius * math.cos(angle), cy + ring_radius * math.sin(angle))
        # Cada letra girada no proprio ponto: uma linha por letra, como no selo real.
        page.insert_text(
            point, char, fontsize=5, color=GOLD,
            morph=(point, pymupdf.Matrix(-math.degrees(angle) - 90)),
        )


def _spans(page) -> list[dict]:
    return [
        span
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block["lines"]
        for span in line["spans"]
    ]


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class WatermarkPageTests(unittest.TestCase):
    def test_page_without_the_anchor_is_the_same_object(self) -> None:
        import pymupdf

        from anonimizador_app.watermark import without_watermark

        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), "Peticao inicial da Uniao.")
            # Letra pequena dourada, mas sem o texto do selo: nao e selo.
            page.insert_text((72, 100), "nota de rodape", fontsize=5, color=GOLD)
            self.assertIs(without_watermark(page), page)

    def test_badge_leaves_text_dict_and_words_but_real_text_stays(self) -> None:
        import pymupdf

        from anonimizador_app.watermark import without_watermark

        with pymupdf.open() as document:
            page = document.new_page()
            _stamp_badge(page, 60, 60)
            # Texto de verdade encostado no selo (mesmo corredor das letras) e longe dele.
            page.insert_text((20, 14), "Processo 0000000-00.2026.4.04.7000/PR, Evento 1", fontsize=9)
            page.insert_text((30, 70), "reu ficticio", fontsize=9)
            page.insert_text((72, 200), "Corpo da peticao inicial da Uniao.", fontsize=11)
            # Letra pequena de outra cor, dentro da margem do selo: e texto de verdade.
            page.insert_text((100, 62), "nota", fontsize=5, color=(0, 0, 0))

            before = page.get_text("text")
            self.assertIn("BUY NOW", before)
            clean = without_watermark(page)

            self.assertIsNot(clean, page)
            text = clean.get_text("text")
            for noise in ("BUY", "XChange", "tracker", "Editor"):
                self.assertNotIn(noise, text)
            for real in ("Processo 0000000-00.2026.4.04.7000/PR, Evento 1", "reu ficticio", "nota",
                         "Corpo da peticao inicial da Uniao."):
                self.assertIn(real, text)
            # Um span por vez: so os do selo saem; nenhum outro.
            gold = [s for s in _spans(clean) if s["color"] == 0xFFC800]
            self.assertEqual(gold, [])
            self.assertEqual(len(_spans(clean)), len(_spans(page)) - sum(
                1 for s in _spans(page) if s["color"] == 0xFFC800
            ))
            words = {word[4] for word in clean.get_text("words")}
            self.assertTrue({"reu", "ficticio", "nota", "Corpo"} <= words)
            self.assertFalse(words & {"BUY", "NOW!", "P", "D", "F"})
            # O que e da pagina original continua acessivel.
            self.assertEqual(clean.rect, page.rect)

    def test_badge_far_from_text_does_not_remove_other_small_gold_text(self) -> None:
        import pymupdf

        from anonimizador_app.watermark import without_watermark

        with pymupdf.open() as document:
            page = document.new_page()
            _stamp_badge(page, 60, 60)
            page.insert_text((300, 500), "legenda dourada", fontsize=5, color=GOLD)
            clean = without_watermark(page)
            self.assertIn("legenda dourada", clean.get_text("text"))

    def test_pixmap_blanks_the_seal_disc_and_keeps_what_is_outside(self) -> None:
        import pymupdf

        from anonimizador_app.watermark import without_watermark

        with pymupdf.open() as document:
            page = document.new_page()
            page.draw_rect(pymupdf.Rect(0, 0, 595, 842), color=None, fill=(0.6, 0.6, 0.6))  # "scan"
            _stamp_badge(page, 60, 60)
            clean = without_watermark(page)
            matrix = pymupdf.Matrix(2, 2)
            before = page.get_pixmap(matrix=matrix, alpha=False)
            after = clean.get_pixmap(matrix=matrix, alpha=False)
            # Centro do disco: o selo (ou o branco do miolo) vira branco; ao lado, o cinza fica.
            self.assertEqual(tuple(after.pixel(120, 120))[:3], (255, 255, 255))
            self.assertEqual(tuple(after.pixel(260, 260))[:3], tuple(before.pixel(260, 260))[:3])
            self.assertEqual(tuple(after.pixel(400, 400))[:3], tuple(before.pixel(400, 400))[:3])
            # O anel dourado do selo some.
            ring = tuple(before.pixel(2 * (60 + 33), 2 * 60))[:3]
            self.assertNotEqual(ring, (255, 255, 255))
            self.assertEqual(tuple(after.pixel(2 * (60 + 33), 2 * 60))[:3], (255, 255, 255))

    def test_pixmap_without_a_found_disc_blanks_only_the_badge_letters(self) -> None:
        import pymupdf

        from anonimizador_app.watermark import without_watermark

        with pymupdf.open() as document:
            page = document.new_page()
            page.draw_rect(pymupdf.Rect(0, 0, 595, 842), color=None, fill=(0.6, 0.6, 0.6))
            # So o texto do selo, sem o desenho do disco.
            origin = pymupdf.Point(46, 66)
            page.insert_text(origin, "Click to BUY NOW!", fontsize=5, color=GOLD,
                             morph=(origin, pymupdf.Matrix(45)))
            page.insert_text((46, 40), "PDF", fontsize=5, color=GOLD)
            clean = without_watermark(page)
            self.assertIsNot(clean, page)
            before = page.get_pixmap(alpha=False)
            after = clean.get_pixmap(alpha=False)
            self.assertEqual(tuple(after.pixel(400, 400))[:3], tuple(before.pixel(400, 400))[:3])
            self.assertEqual(tuple(after.pixel(300, 100))[:3], tuple(before.pixel(300, 100))[:3])


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class WatermarkExtractionTests(unittest.TestCase):
    def _pdf_with_badge(self, directory: Path, *, crowded: bool = False) -> Path:
        import pymupdf

        path = directory / "selo.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            _stamp_badge(page, 60, 60)
            if crowded:
                # Letras uma sobre a outra (como no selo real, a 5 pt): aciona a leitura por camada.
                for index in range(120):
                    page.insert_text((20 + (index % 6) * 2, 50 + (index % 7)), "hanagE"[index % 6],
                                     fontsize=5, color=GOLD)
            page.insert_text((72, 200), "Conforme o evento 12, a executada foi citada.", fontsize=11)
            page.insert_text((72, 230), "Peticao inicial da Uniao ficticia, para teste.", fontsize=11)
            document.save(path)
        return path

    def test_badge_text_does_not_reach_the_markdown(self) -> None:
        for crowded in (False, True):
            with self.subTest(crowded=crowded), tempfile.TemporaryDirectory() as tmp:
                text = extract_fast(
                    self._pdf_with_badge(Path(tmp), crowded=crowded),
                    read_page=lambda _page: self.fail("sem OCR aqui"),
                ).markdown
            for noise in ("BUY", "XChange", "tracker", "Editor", "|---"):
                self.assertNotIn(noise, text)
            self.assertIn("Conforme o evento 12, a executada foi citada.", text)
            self.assertIn("Peticao inicial da Uniao ficticia, para teste.", text)
            one_char = [line for line in text.splitlines() if len(line.strip()) == 1]
            self.assertEqual(one_char, [])

    def test_ruled_table_under_the_badge_keeps_its_cells_clean(self) -> None:
        import pymupdf

        rows = [
            ("Protocolo", "Tipo de ordem", "Resultado"),
            ("29/05/2026", "Bloqueio de valores", "R$ 1.000,00"),
            ("02/06/2026", "Reiteracao", "R$ 0,00"),
        ]
        above = (
            "Resposta das instituicoes financeiras ao bloqueio determinado pelo juizo, "
            "conforme a ordem protocolada no sistema e as respostas relacionadas abaixo por data."
        )
        xs, ys = [20, 190, 360, 530], [130, 170, 195, 220]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tabela.pdf"
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_textbox(pymupdf.Rect(20, 40, 570, 110), above, fontsize=11)
                for y in ys:
                    page.draw_line((xs[0], y), (xs[-1], y))
                for x in xs:
                    page.draw_line((x, ys[0]), (x, ys[-1]))
                for row, y in zip(rows, ys):
                    for cell, x in zip(row, xs):
                        page.insert_text((x + 4, y + 16), cell, fontsize=10)
                _stamp_badge(page, 60, 150)  # o selo cai em cima da primeira celula
                page.insert_text((20, 300), "Do que, para constar, lavrou-se a presente certidao.", fontsize=11)
                document.save(path)
            text = extract_fast(path, read_page=lambda _page: self.fail("sem OCR aqui")).markdown

        for noise in ("BUY", "XChange", "tracker", "Editor"):
            self.assertNotIn(noise, text)
        self.assertIn("| 29/05/2026 | Bloqueio de valores | R$ 1.000,00 |", text)
        self.assertIn("| 02/06/2026 | Reiteracao | R$ 0,00 |", text)
        self.assertIn("Do que, para constar, lavrou-se a presente certidao.", text)

    def test_scanned_page_is_read_without_the_seal(self) -> None:
        import pymupdf

        seen: dict[str, object] = {}

        def reader(page) -> str:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            seen["centre"] = tuple(pix.pixel(120, 120))[:3]
            seen["outside"] = tuple(pix.pixel(600, 600))[:3]
            seen["native"] = page.get_text("text")
            return "CONTRATO SOCIAL da empresa ficticia"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "escaneada.pdf"
            with pymupdf.open() as document:
                page = document.new_page()
                gray = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 200, 280), False)
                gray.set_rect(gray.irect, (150,))
                page.insert_image(page.rect, pixmap=gray)  # a folha escaneada
                _stamp_badge(page, 60, 60)
                page.insert_text((200, 20), "Processo 0000000-00.2026.4.04.7000/PR, Evento 2", fontsize=8)
                document.save(path)
            extracted = extract_fast(path, read_page=reader)

        self.assertEqual(seen["centre"], (255, 255, 255))
        self.assertEqual(seen["outside"][0], 150)
        self.assertNotIn("BUY", seen["native"])
        self.assertIn("<!-- ocr: escaneada -->\nCONTRATO SOCIAL da empresa ficticia", extracted.markdown)
        self.assertNotIn("BUY", extracted.markdown)
        self.assertIn("Processo 0000000-00.2026.4.04.7000/PR, Evento 2", extracted.markdown)


if __name__ == "__main__":
    unittest.main()

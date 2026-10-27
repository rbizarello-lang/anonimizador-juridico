"""H9-k: fonte quebrada feita de caractere de controle e folha hibrida (pouco texto nativo, corpo em imagem).

Todos os valores sao inventados: nomes, matriculas e processos nao existem.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.coverage import UNREAD_MARK, parse_coverage
from anonimizador_app.fast_extract import FAST_EXTRACTOR_VERSION, FAST_OCR_REUSABLE_FROM, extract_fast
from anonimizador_app.ocr_pages import (
    HYBRID_IMAGE_RATIO,
    HYBRID_MAX_CHARS,
    OCR_BROKEN_FONT,
    OCR_HYBRID,
    broken_glyph_ratio,
    ocr_reason,
)

HAS_PYMUPDF = importlib.util.find_spec("pymupdf") is not None

# Camada de texto de um acordao com a fonte sem mapa: so controle (U+0001-U+001F) e simbolos soltos.
_CONTROLS = "".join(chr(code) for code in (1, 2, 3, 5, 6, 7, 8, 14, 15, 16, 17, 18, 20, 21, 22, 23, 24, 25, 26, 27))
_GARBLED_LINE = ("A" + _CONTROLS + " ;" + _CONTROLS + " ") * 3

_HEADER = (
    "CARTORIO DE REGISTRO DE IMOVEIS FICTICIO DA COMARCA DE EXEMPLOPOLIS",
    "Oficial Registrador Fulano Ficticio de Tal - Rua das Acacias Imaginarias, 100",
    "CERTIDAO DE INTEIRO TEOR DA MATRICULA 99.999 - LIVRO 2 - REGISTRO GERAL",
    "Certifico e dou fe que a presente e reproducao autentica da matricula acima.",
    "Emolumentos: R$ 10,00 - Selo digital ficticio AAA00000-ZZZZ - consulta no sitio do tribunal.",
    "Processo 0000000-00.2026.4.04.7000/PR, Evento 9, MATRIMOVEL2, Pagina 1",
    "Exemplopolis, 01 de fevereiro de 2026. Documento assinado digitalmente pelo oficial.",
    "Valido somente com o selo de fiscalizacao. Pedido numero 123456 de 30/01/2026.",
)

_PROSE = (
    "A Uniao requer a penhora do imovel indicado na peticao, conforme a certidao juntada aos autos "
    "pela parte exequente, com a intimacao da executada ficticia para, querendo, apresentar embargos."
)


def _lines(page, lines, *, top: float = 50, size: float = 8) -> None:
    for index, line in enumerate(lines):
        page.insert_text((40, top + index * 12), line, fontsize=size)


def _image(page, rect) -> None:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 180), False)
    pix.clear_with(150)
    page.insert_image(rect, pixmap=pix)


def _page_block(markdown: str, number: int) -> str:
    start = markdown.index(f"<!-- pagina-origem: {number} -->")
    following = markdown.find("<!-- pagina-origem:", start + 10)
    return markdown[start:] if following < 0 else markdown[start:following]


class ImprobableCharacterTests(unittest.TestCase):
    def test_control_characters_count_as_improbable(self) -> None:
        self.assertGreater(broken_glyph_ratio(_GARBLED_LINE * 10), 0.5)

    def test_control_characters_that_python_splits_as_blank_still_count(self) -> None:
        # str.split() trata U+001C-U+001F como espaco: a conta antiga nao os via.
        separators = "".join(chr(code) for code in (28, 29, 30, 31))
        self.assertGreater(broken_glyph_ratio(("ab" + separators) * 50), 0.5)

    def test_layout_controls_and_plain_prose_are_not_improbable(self) -> None:
        tab, newline, carriage = chr(9), chr(10), chr(13)
        prose = f"Conta 12345678 em 01/02/2026 da Uniao,{tab}R$ 1,00{carriage}{newline}segunda linha{newline}"
        self.assertEqual(broken_glyph_ratio(prose * 20), 0.0)

    def test_private_use_outside_the_basic_plane_counts(self) -> None:
        self.assertGreater(broken_glyph_ratio(chr(0xF0001) * 30 + "texto"), 0.5)

    def test_short_unreadable_layer_is_broken_font(self) -> None:
        self.assertEqual(ocr_reason(120, 0.0, 0, broken_ratio=0.40), OCR_BROKEN_FONT)
        self.assertIsNone(ocr_reason(120, 0.0, 0, broken_ratio=0.0))


class HybridReasonTests(unittest.TestCase):
    def test_short_native_text_with_large_image_is_hybrid(self) -> None:
        self.assertEqual(ocr_reason(600, 0.0, 0, 0.0, image_ratio=0.50), OCR_HYBRID)

    def test_limits(self) -> None:
        self.assertIsNone(ocr_reason(600, 0.0, 0, 0.0, image_ratio=HYBRID_IMAGE_RATIO - 0.01))
        self.assertIsNone(ocr_reason(HYBRID_MAX_CHARS, 0.0, 0, 0.0, image_ratio=0.9))
        # Folha nativa corrida com imagem (timbre, assinatura) continua sem OCR (GOVERNANCA §8.3).
        self.assertIsNone(ocr_reason(1800, 0.95, broken_ratio=0.001, image_ratio=0.95))

    def test_extractor_version_moved_and_old_ocr_stays_reusable(self) -> None:
        self.assertTrue(FAST_EXTRACTOR_VERSION.endswith("|17"))  # v0.4.12: recortes e limpeza do OCR
        self.assertIn("pymupdf-rapido|16", FAST_OCR_REUSABLE_FROM)
        self.assertIn("pymupdf-rapido|15", FAST_OCR_REUSABLE_FROM)
        self.assertNotIn(FAST_EXTRACTOR_VERSION, FAST_OCR_REUSABLE_FROM)


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class H9kExtractionTests(unittest.TestCase):
    def _pdf(self, directory: Path, build) -> Path:
        import pymupdf

        path = directory / "autos_ficticios.pdf"
        with pymupdf.open() as document:
            build(document)
            document.save(path)
        return path

    def test_control_character_layer_is_broken_font_and_replaced_by_ocr(self) -> None:
        def build(document) -> None:
            page = document.new_page()
            _lines(page, [_GARBLED_LINE] * 40)

        calls: list[int] = []

        def reader(page) -> str:
            calls.append(page.number)
            return "ACORDAO ficticio: a Turma negou provimento ao recurso da executada."

        with tempfile.TemporaryDirectory() as tmp:
            extracted = extract_fast(self._pdf(Path(tmp), build), read_page=reader)

        text = extracted.markdown
        self.assertEqual(calls, [0])
        self.assertIn("<!-- ocr: fonte-quebrada -->\nACORDAO ficticio", text)
        self.assertNotIn(chr(1), text)
        coverage = parse_coverage(text)
        self.assertEqual((coverage.native, coverage.broken_font), (0, 1))

    def _hybrid_pdf(self, document) -> None:
        import pymupdf

        # Folha 1: certidao de matricula, cabecalho/carimbo/rodape nativos e o corpo em imagem de ~48%.
        certidao = document.new_page()
        _lines(certidao, _HEADER, top=40)
        _image(certidao, pymupdf.Rect(40, 160, 560, 620))
        _lines(certidao, _HEADER[4:], top=660)
        # Folha 2: peca nativa corrida com logotipo pequeno.
        peca = document.new_page()
        _image(peca, pymupdf.Rect(40, 20, 120, 60))
        _lines(peca, [_PROSE[:95], _PROSE[95:]] * 10, top=80)
        # Folha 3: pouco texto nativo e imagem pequena (assinatura).
        despacho = document.new_page()
        _lines(despacho, _HEADER, top=60)
        _image(despacho, pymupdf.Rect(400, 700, 520, 760))

    def test_hybrid_page_keeps_native_text_and_appends_ocr(self) -> None:
        calls: list[int] = []

        def reader(page) -> str:
            calls.append(page.number)
            return "R.1/99.999 - COMPRA E VENDA ficticia: Beltrana Imaginaria adquiriu o imovel."

        with tempfile.TemporaryDirectory() as tmp:
            extracted = extract_fast(self._pdf(Path(tmp), self._hybrid_pdf), read_page=reader)

        text = extracted.markdown
        self.assertEqual(calls, [0])  # so a certidao; peca com logotipo e despacho com assinatura, nao
        self.assertEqual(extracted.extractor, "pymupdf-ocr")
        certidao = _page_block(text, 1)
        for line in _HEADER:
            self.assertIn(line, certidao)  # nenhuma palavra nativa se perde
        self.assertIn("<!-- ocr: hibrida -->\nR.1/99.999 - COMPRA E VENDA ficticia", certidao)
        self.assertLess(certidao.index(_HEADER[0]), certidao.index("<!-- ocr: hibrida -->"))
        self.assertNotIn("<!-- ocr", _page_block(text, 2))
        self.assertNotIn("<!-- ocr", _page_block(text, 3))

        first_line = text.splitlines()[0]
        self.assertIn("; hibridas=1; nao-lidas=nenhuma -->", first_line)
        self.assertIn("<!-- ocr-paginas: 1 -->", text)
        coverage = parse_coverage(text)  # leitor antigo: o campo novo nao muda os outros
        self.assertEqual((coverage.pages, coverage.native, coverage.ocr, coverage.hybrid, coverage.unread), (3, 2, 1, 1, ()))
        # Indice do lote: "chave=valor" separados por "; ".
        fields = dict(item.split("=", 1) for item in first_line[len("<!-- cobertura: ") : -len(" -->")].split("; "))
        self.assertEqual((fields["hibridas"], fields["nativas"], fields["escaneadas"]), ("1", "2", "0"))

    def test_hybrid_image_without_text_stays_native_not_unread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extracted = extract_fast(self._pdf(Path(tmp), self._hybrid_pdf), read_page=lambda _page: "")

        text = extracted.markdown
        self.assertNotIn(UNREAD_MARK, text)
        self.assertIn("; hibridas=0;", text.splitlines()[0])
        coverage = parse_coverage(text)
        self.assertEqual((coverage.native, coverage.unread), (3, ()))
        self.assertIn(_HEADER[0], _page_block(text, 1))

    def test_hybrid_ocr_failure_is_unread_not_silence(self) -> None:
        def broken(_page) -> str:
            raise ValueError("motor de OCR indisponivel")

        with tempfile.TemporaryDirectory() as tmp:
            extracted = extract_fast(self._pdf(Path(tmp), self._hybrid_pdf), read_page=broken)

        certidao = _page_block(extracted.markdown, 1)
        self.assertIn(f"<!-- pagina-nao-lida: {OCR_HYBRID} -->", certidao)
        self.assertIn(_HEADER[0], certidao)
        self.assertEqual(parse_coverage(extracted.markdown).unread, (1,))


if __name__ == "__main__":
    unittest.main()

"""Fila 3.x E12: rotulo e valor pareados por posicao no extrato SISBAJUD, na lista do RENAJUD e na
capa do processo. Folhas sinteticas, dados inventados (nada de caso real)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from anonimizador_app import fast_extract, report_layouts
from anonimizador_app.extraction_cache import cache_key, load_or_extract
from anonimizador_app.fast_extract import FAST_EXTRACTOR_VERSION, FAST_OCR_REUSABLE_FROM, extract_fast

HAS_PYMUPDF = importlib.util.find_spec("pymupdf") is not None

_NOTICE = (
    "As ordens judiciais protocoladas ate as 19h00min dos dias uteis serao consolidadas, transformadas "
    "em arquivos de remessa e disponibilizadas simultaneamente para todas as instituicoes financeiras."
)


def _put(page, x: float, y: float, text: str, size: float = 8) -> None:
    page.insert_text((x, y), text, fontsize=size)


def _centered(page, center: float, y: float, text: str, size: float = 8) -> None:
    import pymupdf

    width = pymupdf.get_text_length(text, fontsize=size)
    page.insert_text((center - width / 2, y), text, fontsize=size)


def _page_text(pdf: Path, number: int = 1) -> str:
    markdown = extract_fast(pdf).markdown
    start = markdown.index(f"<!-- pagina-origem: {number} -->")
    following = markdown.find("<!-- pagina-origem:", start + 10)
    return markdown[start:] if following < 0 else markdown[start:following]


def _answers_header(page, y: float) -> None:
    for center, lines in (
        (61, ("Data/hora", "protocolo")),
        (140, ("Tipo de ordem",)),
        (227, ("Juiz solicitante",)),
        (300, ("Valor",)),
        (373, ("Resultado",)),
        (450, ("Saldo bloqueado", "remanescente")),
        (527, ("Data/hora", "resultado")),
    ):
        for offset, text in enumerate(lines):
            top = y + (10.5 if len(lines) == 1 else 10.5 * offset)
            _centered(page, center, top, text)


def _answer_row(page, y: float, order: str, blocked: str, result: tuple[str, ...]) -> None:
    for center, lines in (
        (61, ("14 AGO. 2026", "17:40")),
        (140, ("Bloqueio de Valores",)),
        (227, ("JUIZA FICTICIA", "SOUZA")),
        (300, (order,)),
        (373, result),
        (450, (blocked,)),
        (527, ("14 AGO. 2026 20:36",)),
    ):
        for offset, text in enumerate(lines):
            _centered(page, center, y + 9.3 * offset, text)


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class SisbajudReportTests(unittest.TestCase):
    def _pdf(self, directory: Path, *, labels_first: bool = True) -> Path:
        import pymupdf

        path = directory / "sisbajud.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            _put(page, 245, 54, "PODER JUDICIARIO")
            _put(page, 132, 106, "DETALHAMENTO DA ORDEM JUDICIAL DE BLOQUEIO DE VALORES")
            _put(page, 20, 135, "Dados do Bloqueio")
            _put(page, 110, 170, _NOTICE[:90])
            _put(page, 110, 180, _NOTICE[90:])
            labels = [
                (217, "Número do protocolo:", "20269990001111"),
                (235, "Data/hora de protocolamento:", "14/08/2026 17:40"),
                (253, "Número do processo:", "0001234-56.2026.4.04.7000"),
                (271, "Juiz solicitante do bloqueio:", "JUIZA FICTICIA SOUZA"),
                (289, "Tipo/natureza da ação:", "Execução Fiscal"),
                (307, "CPF/CNPJ do autor/exequente da ação:", "00000000000191"),
                (325, "Nome do autor/exequente da ação:", "UNIAO FAZENDA NACIONAL"),
            ]
            if labels_first:
                for y, label, _value in labels:
                    _put(page, 20, y, label)
                for y, _label, value in labels:
                    _put(page, 225, y, value)
            else:
                for y, label, value in labels:
                    _put(page, 20, y, label)
                    _put(page, 225, y, value)
            _put(page, 20, 343.6, "Protocolo de bloqueio agendado?")
            _put(page, 225, 343.8, "Não")
            _put(page, 20, 361.6, "Repetição programada?")
            _put(page, 225, 361.8, "Sim")
            _put(page, 280, 361.6, "Data limite da repetição:")
            _put(page, 438, 361.8, "25/08/2026")
            _put(page, 20, 379.6, "Ordem sigilosa?")
            _put(page, 225, 379.8, "Não")
            # Reu/executado e total bloqueado: cabecalho acima do valor.
            _put(page, 25, 430, "Réu/Executado")
            _put(page, 328, 431, "Total bloqueado pelo bloqueio original e reiterações")
            _put(page, 25, 441, "00000000000191: EMPRESA FICTICIA DE BENS LTDA")
            _put(page, 329, 441, "R$ 2.500,00")
            _put(page, 31, 481, "Respostas")
            _put(page, 30, 503, "BANCO FICTICIO S.A.")
            _answers_header(page, 538)
            _answer_row(
                page, 571, "R$ 100.000,00", "R$ 2.500,00",
                ("(25) Cumprida", "totalmente ou", "parcialmente."),
            )
            # Segundo cabecalho no fim da folha, sem linha de dados (a tabela segue na folha 2).
            _put(page, 30, 661, "OUTRO BANCO FICTICIO")
            _answers_header(page, 696)
            _put(page, 3.7, 12, "Processo 0001234-56.2026.4.04.7000/PR, Evento 7, SISBAJUD1, Página 1")
            document.save(path)
        return path

    def test_labels_get_their_values_even_when_values_come_later_in_the_pdf(self) -> None:
        for labels_first in (True, False):
            with self.subTest(labels_first=labels_first), tempfile.TemporaryDirectory() as tmp:
                text = _page_text(self._pdf(Path(tmp), labels_first=labels_first))
            for expected in (
                "Número do processo: 0001234-56.2026.4.04.7000",
                "Juiz solicitante do bloqueio: JUIZA FICTICIA SOUZA",
                "Nome do autor/exequente da ação: UNIAO FAZENDA NACIONAL",
                "CPF/CNPJ do autor/exequente da ação: 00000000000191",
                "Data/hora de protocolamento: 14/08/2026 17:40",
                "Número do protocolo: 20269990001111",
                "Protocolo de bloqueio agendado? Não",
                "Repetição programada? Sim",
                "Data limite da repetição: 25/08/2026",
                "Ordem sigilosa? Não",
            ):
                self.assertIn(expected + "\n", text + "\n")

    def test_order_value_and_blocked_value_each_sit_under_their_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = _page_text(self._pdf(Path(tmp)))

        self.assertIn(
            "| Data/hora protocolo | Tipo de ordem | Juiz solicitante | Valor | Resultado"
            " | Saldo bloqueado remanescente | Data/hora resultado |",
            text,
        )
        self.assertIn(
            "| 14 AGO. 2026 17:40 | Bloqueio de Valores | JUIZA FICTICIA SOUZA | R$ 100.000,00"
            " | (25) Cumprida totalmente ou parcialmente. | R$ 2.500,00 | 14 AGO. 2026 20:36 |",
            text,
        )
        # A instituicao fica acima da tabela dela.
        self.assertLess(text.index("BANCO FICTICIO S.A."), text.index("| Data/hora protocolo"))
        self.assertLess(text.index("BANCO FICTICIO S.A.\n\n| Data/hora"), text.index("| 14 AGO."))
        self.assertIn(
            "| Réu/Executado | Total bloqueado pelo bloqueio original e reiterações |\n|---|---|\n"
            "| 00000000000191: EMPRESA FICTICIA DE BENS LTDA | R$ 2.500,00 |",
            text,
        )

    def test_header_without_data_rows_stays_as_it_was(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = _page_text(self._pdf(Path(tmp)))

        # So a tabela do primeiro banco virou tabela; o cabecalho do segundo ficou solto.
        self.assertEqual(text.count("| Data/hora protocolo |"), 1)
        self.assertIn("OUTRO BANCO FICTICIO", text)
        self.assertEqual(text.count("Tipo de ordem"), 2)

    def test_stamp_above_the_header_is_not_taken_for_the_institution_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = _page_text(self._pdf(Path(tmp)))

        self.assertNotIn("Processo 0001234-56.2026.4.04.7000/PR, Evento 7, SISBAJUD1, Página 1\n\n|", text)
        self.assertIn("Evento 7, SISBAJUD1", text)

    def test_every_word_of_the_page_is_kept(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as tmp:
            pdf = self._pdf(Path(tmp))
            with pymupdf.open(pdf) as document:
                native = document[0].get_text("text")
            body = _page_text(pdf)
        words = fast_extract._WORD.findall
        self.assertEqual(sorted(words(native.casefold())), sorted(words(body.split("-->", 1)[1].casefold())))

    def test_failed_word_check_falls_back_to_the_original_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self._pdf(Path(tmp))
            with mock.patch.object(report_layouts, "_keeps_every_word", return_value=False):
                text = _page_text(pdf)

        self.assertNotIn("Número do processo: 0001234", text)
        self.assertIn("Número do processo:\n", text)
        self.assertNotIn("| Data/hora protocolo |", text)

    def test_page_without_the_fixed_labels_is_not_touched(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ficha.pdf"
            with pymupdf.open() as document:
                page = document.new_page()
                _put(page, 72, 80, "Ficha cadastral ficticia sem relacao com extrato de bloqueio " * 3)
                for index, (label, value) in enumerate(
                    [("Nome:", "FULANO FICTICIO"), ("Cidade:", "CIDADE FICTICIA"), ("Observacao:", "NENHUMA")]
                ):
                    _put(page, 20, 140 + 18 * index, label)
                    _put(page, 225, 140 + 18 * index, value)
                document.save(path)
            text = _page_text(path)

        self.assertIn("Nome:\n", text)
        self.assertNotIn("Nome: FULANO", text)


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class RenajudListTests(unittest.TestCase):
    def test_vehicle_list_becomes_a_table_with_the_owner_wrapped_in_its_cell(self) -> None:
        import pymupdf

        columns = [
            (96, "Placa"), (134, "Placa Anterior"), (189, "UF"), (208, "Marca/Modelo"),
            (293, "Ano Fabricação"), (351, "Ano Modelo"), (398, "Proprietário"),
            (464, "Restrições Existentes"), (541, "Ações"),
        ]
        rows = [
            (364, ["ABC1D23", "XYZ9876", "SC", "FIAT/UNO MILLE", "2014", "2015"], ["FULANO DE TAL", "COMERCIO", "DE BENS LTDA"], "Não"),
            (392, ["DEF2G34", "", "SC", "VW/GOL 1.0", "2001", "2001"], ["BELTRANO", "TRANSPORTES", "LTDA"], "Sim"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "renajud.pdf"
            with pymupdf.open() as document:
                page = document.new_page()
                _put(page, 245, 333, "Lista de Veículos - Total: 2")
                for x, name in columns:
                    _put(page, x, 345, name, size=6.5)
                for y, cells, owner, restricted in rows:
                    for (x, _name), value in zip(columns[:6], cells):
                        if value:
                            _put(page, x, y, value, size=6.5)
                    for offset, text in enumerate(owner):
                        _put(page, 398, y - 6.8 + 6.8 * offset, text, size=6.5)
                    _put(page, 464, y, restricted, size=6.5)
                _put(page, 78, 253, "Placa")
                _put(page, 146, 253, "Chassi")
                _put(page, 20, 600, "Rodape do sistema sem relacao com a lista de veiculos cadastrados " * 2)
                document.save(path)
            text = _page_text(path)

        self.assertIn(
            "| Placa | Placa Anterior | UF | Marca/Modelo | Ano Fabricação | Ano Modelo | Proprietário"
            " | Restrições Existentes | Ações |",
            text,
        )
        self.assertIn(
            "| ABC1D23 | XYZ9876 | SC | FIAT/UNO MILLE | 2014 | 2015 | FULANO DE TAL COMERCIO DE BENS LTDA | Não |  |",
            text,
        )
        self.assertIn(
            "| DEF2G34 |  | SC | VW/GOL 1.0 | 2001 | 2001 | BELTRANO TRANSPORTES LTDA | Sim |  |",
            text,
        )


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class ProcessCoverTests(unittest.TestCase):
    def test_additional_information_grid_pairs_wrapped_labels_and_values(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capa.pdf"
            with pymupdf.open() as document:
                page = document.new_page()
                _put(page, 40, 40, "Capa do Processo", size=10)
                _put(page, 39, 60, "Nº do processo 0001234-56.2026.4.04.7000")
                _put(page, 39, 69, "Classe da ação:")
                _put(page, 150, 70, "Execução Fiscal de teste")
                _put(page, 39, 78, "Competência")
                _put(page, 150, 79, "Civel ficticia")
                _put(page, 39, 87, "Situação")
                _put(page, 150, 88, "EM ANDAMENTO")
                _put(page, 40, 321, "Informações Adicionais", size=10)
                # Rotulos alinhados a direita (x1 fixo), valores a esquerda; rotulo e valor em 2 linhas.
                for right, label_lines, left, value_lines, y in (
                    (183, ["Chave Processo:"], 188, ["999900001111"], 340),
                    (344, ["Valor da Causa:"], 349, ["R$ 1.000,00"], 340),
                    (480, ["Nivel de Sigilo do", "Processo:"], 484, ["Sem Sigilo (Nivel", "0)"], 334),
                    (183, ["Autor manifesta desinteresse na", "conciliacao:"], 188, ["Nao"], 380),
                ):
                    for offset, text in enumerate(label_lines):
                        width = pymupdf.get_text_length(text, fontsize=8)
                        _put(page, right - width, y + 9 * offset, text)
                    for offset, text in enumerate(value_lines):
                        _put(page, left, y + 2 + 6 * offset if len(value_lines) == 2 else y + 2, text)
                _put(page, 20, 600, "Rodape da capa sem relacao com os campos acima, apenas para dar volume ao texto da folha. " * 2)
                document.save(path)
            text = _page_text(path)

        for expected in (
            "Competência: Civel ficticia",
            "Situação: EM ANDAMENTO",
            "Classe da ação: Execução Fiscal de teste",
            "Chave Processo: 999900001111",
            "Valor da Causa: R$ 1.000,00",
            "Nivel de Sigilo do Processo: Sem Sigilo (Nivel 0)",
            "Autor manifesta desinteresse na conciliacao: Nao",
        ):
            self.assertIn(expected, text)
        # O numero do processo nao engole o rotulo seguinte.
        self.assertIn("Nº do processo 0001234-56.2026.4.04.7000\n", text)


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class OcrReuseTests(unittest.TestCase):
    """A |14 so mudou o texto nativo: o OCR da |13 nao roda de novo."""

    def _pdf(self, directory: Path) -> Path:
        import pymupdf

        path = directory / "misto.pdf"
        with pymupdf.open() as document:
            native = document.new_page()
            native.insert_text((72, 72), "Peticao inicial da Uniao, valor da causa R$ 1.000,00.")
            scan = document.new_page()
            scan.insert_text((20, 20), "Processo 0000000-00.2026.4.04.7000/PR, Evento 2, OUT2, Pagina 1", fontsize=6)
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 280), False)
            pix.clear_with(180)
            scan.insert_image(scan.rect, pixmap=pix)
            document.set_toc([[1, "Evento 1 - INIC1", 1], [1, "Evento 2 - OUT2", 2]])
            document.save(path)
        return path

    def test_previous_ocr_page_is_copied_and_native_page_is_redone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self._pdf(Path(tmp))
            first = extract_fast(pdf, read_page=lambda _page: "TEXTO LIDO POR OCR da folha escaneada ficticia")
            calls: list[int] = []
            second = extract_fast(
                pdf,
                read_page=lambda page: calls.append(page.number) or "outro texto",
                previous=first.markdown,
            )

        self.assertEqual(calls, [])
        self.assertEqual(second.markdown, first.markdown)
        self.assertEqual(second.extractor, "pymupdf-ocr")

    def test_page_that_was_not_read_before_is_read_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self._pdf(Path(tmp))
            def broken(_page) -> str:
                raise ValueError("modelo indisponivel")

            unread = extract_fast(pdf, read_page=broken)
            self.assertIn("pagina-nao-lida", unread.markdown)
            calls: list[int] = []
            again = extract_fast(
                pdf,
                read_page=lambda page: calls.append(page.number) or "TEXTO LIDO POR OCR da folha escaneada",
                previous=unread.markdown,
            )

        self.assertEqual(calls, [1])
        self.assertIn("TEXTO LIDO POR OCR", again.markdown)

    def test_cache_of_the_compatible_version_feeds_the_new_one(self) -> None:
        self.assertIn("pymupdf-rapido|13", FAST_OCR_REUSABLE_FROM)
        self.assertNotIn(FAST_EXTRACTOR_VERSION, FAST_OCR_REUSABLE_FROM)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = self._pdf(root)
            read = extract_fast(pdf, read_page=lambda _page: "TEXTO LIDO POR OCR da folha escaneada ficticia")
            cache = root / "cache"
            cache.mkdir()
            old_key = cache_key(pdf.read_bytes(), FAST_OCR_REUSABLE_FROM[0])
            (cache / f"{old_key}.md").write_text(read.markdown, encoding="utf-8")
            (cache / f"{old_key}.json").write_text(json.dumps({"versao": FAST_OCR_REUSABLE_FROM[0]}), encoding="utf-8")

            def never(_page) -> str:
                raise AssertionError("o OCR nao deveria rodar")

            with mock.patch("anonimizador_app.ocr_pages.recognize_page", never):
                migrated = load_or_extract(pdf, cache_dir=cache)
                fresh = load_or_extract(pdf, cache_dir=cache, use_cache=False)  # sem cache: OCR de novo
            self.assertFalse(migrated.cached)
            self.assertIn("TEXTO LIDO POR OCR", migrated.document.markdown)
            self.assertTrue((cache / f"{cache_key(pdf.read_bytes(), FAST_EXTRACTOR_VERSION)}.md").is_file())
        self.assertIn("pagina-nao-lida", fresh.document.markdown)


if __name__ == "__main__":
    unittest.main()

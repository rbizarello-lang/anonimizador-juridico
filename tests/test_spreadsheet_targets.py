from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from anonimizador_app.settings import AppSettings
from anonimizador_app.spreadsheet_targets import (
    merge_unique,
    parse_target_spreadsheet,
)
from anonimizador_app.ui import Session, _import_spreadsheet_into_session


NEXUS_SAMPLE = Path(r"C:\Users\User\Downloads\nexus_export_2026-09-03.xlsx")


def _write_xlsx_simple(path: Path, sheets: dict[str, list[list[str]]]) -> None:
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(value: str) -> int:
        if value not in index:
            index[value] = len(strings)
            strings.append(value)
        return index[value]

    items = list(sheets.items())
    sheet_files: list[tuple[str, str, str]] = []
    for position, (name, rows) in enumerate(items, start=1):
        body = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
        ]
        for row_number, row in enumerate(rows, start=1):
            body.append(f'<row r="{row_number}">')
            for col, cell in enumerate(row):
                letter = chr(ord("A") + col)
                body.append(
                    f'<c r="{letter}{row_number}" t="s"><v>{sid(str(cell))}</v></c>'
                )
            body.append("</row>")
        body.append("</sheetData></worksheet>")
        rel_id = f"rId{position}"
        target = f"worksheets/sheet{position}.xml"
        sheet_files.append((rel_id, target, "\n".join(body)))

    shared = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">',
    ]
    for value in strings:
        shared.append(f"<si><t>{escape(value)}</t></si>")
    shared.append("</sst>")

    workbook_sheets = []
    rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for position, (name, _rows) in enumerate(items, start=1):
        rel_id = f"rId{position}"
        workbook_sheets.append(
            f'<sheet name="{escape(name)}" sheetId="{position}" r:id="{rel_id}"/>'
        )
        rels.append(
            f'<Relationship Id="{rel_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{position}.xml"/>'
        )
    rels.append(
        '<Relationship Id="rIdSs" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
    )
    rels.append("</Relationships>")

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(items) + 1)
        )
        + "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", "\n".join(rels))
        archive.writestr("xl/sharedStrings.xml", "\n".join(shared))
        for position, (_rel_id, target, xml) in enumerate(sheet_files, start=1):
            archive.writestr(f"xl/{target}", xml)


class SpreadsheetTargetTests(unittest.TestCase):
    def test_csv_splits_pj_and_pf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alvos.csv"
            path.write_text(
                "Nome;Tipo;CPF;CNPJ\n"
                "Empresa Alfa LTDA;PJ;;12.345.678/0001-95\n"
                "Joao Silva;PF;123.456.789-09;\n"
                "Uniao;PJ;;00.000.000/0001-91\n",
                encoding="utf-8",
            )
            parsed = parse_target_spreadsheet(path)
        self.assertEqual(parsed.companies, ["Empresa Alfa LTDA"])
        self.assertEqual(parsed.people, ["Joao Silva"])
        self.assertEqual(parsed.ids, ["12.345.678/0001-95", "123.456.789-09"])

    def test_xlsx_nexus_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nexus_export.xlsx"
            _write_xlsx_simple(
                path,
                {
                    "Pessoas": [
                        ["Nome", "Tipo", "CPF", "CNPJ", "Papel", "Posição", "Operação"],
                        [
                            "Comercial Fachada Norte LTDA",
                            "PJ",
                            "",
                            "12.345.678/0001-90",
                            "Devedora",
                            "Alvo Direto",
                            "Norte",
                        ],
                        [
                            "João Almeida Souza",
                            "PF",
                            "123.456.789-00",
                            "",
                            "Sócio",
                            "Alvo Direto",
                            "Norte",
                        ],
                    ],
                    "CNPJs": [
                        ["CNPJ", "Nome", "Operação", "Origem"],
                        [
                            "11.222.333/0001-66",
                            "Imobiliária Norte Prime LTDA",
                            "Norte",
                            "Pessoa",
                        ],
                    ],
                },
            )
            parsed = parse_target_spreadsheet(path)
        self.assertIn("Comercial Fachada Norte LTDA", parsed.companies)
        self.assertIn("Imobiliária Norte Prime LTDA", parsed.companies)
        self.assertEqual(parsed.people, ["João Almeida Souza"])

    def test_merge_keeps_existing_and_skips_duplicate(self) -> None:
        merged = merge_unique(["Alfa Ltda"], ["ALFA LTDA", "Beta SA"])
        self.assertEqual(merged, ["Alfa Ltda", "Beta SA"])

    def test_import_updates_session_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sheet = folder / "caso.csv"
            sheet.write_text(
                "Nome,CPF,CNPJ\n"
                "Marina Ferreira Norte,529.982.247-25,\n"
                "Nova Metal Sul LTDA,,12.345.678/0001-95\n",
                encoding="utf-8",
            )
            session = Session.__new__(Session)
            session.private_dir = folder
            session.settings = AppSettings(target_companies=["Alfa Ltda"])
            session.target_companies_text = "Alfa Ltda"
            session.target_people_text = ""
            session.target_ids_text = ""
            message = _import_spreadsheet_into_session(session, sheet)
            self.assertIn("empresa", message)
            self.assertIn("pessoa", message)
            self.assertEqual(
                session.settings.target_companies,
                ["Alfa Ltda", "Nova Metal Sul LTDA"],
            )
            self.assertEqual(session.settings.target_people, ["Marina Ferreira Norte"])
            self.assertEqual(
                session.settings.target_ids,
                ["529.982.247-25", "12.345.678/0001-95"],
            )
            self.assertEqual(
                session.target_ids_text,
                "529.982.247-25\n12.345.678/0001-95",
            )
            self.assertTrue((folder / "config.json").is_file())

    @unittest.skipUnless(NEXUS_SAMPLE.is_file(), "planilha de exemplo ausente")
    def test_real_nexus_export(self) -> None:
        parsed = parse_target_spreadsheet(NEXUS_SAMPLE)
        self.assertGreater(parsed.total, 0)
        self.assertTrue(parsed.companies or parsed.people)


if __name__ == "__main__":
    unittest.main()

"""Importa nomes e CPF/CNPJ de uma planilha (Nexus xlsx/csv) para a lista de alvos.

O usuario anexa um arquivo com Nome e CPF ou CNPJ. Nao e o dump da Receita.
Abas no formato do Nexus (Pessoas / CNPJs) e planilhas genericas com esses
cabecalhos sao aceitas. Nomes vao para as caixas de empresa/pessoa; documentos
validos vao para a caixa de CPF/CNPJ da carteira. Numero sem DV ou CNPJ de
orgao publico nao entra.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from anonimizador_juridico.pseudonymizer import fold_lexical
from anonimizador_juridico.validators import only_digits

from .gemini_detector import is_kept_term
from .target_ids import id_canonical, parse_target_ids

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_MIN_NAME_CHARS = 3
_NAME_KEYS = frozenset(
    {"nome", "name", "razao", "razao social", "razao_social", "empresa", "pessoa"}
)
_CPF_KEYS = frozenset({"cpf"})
_CNPJ_KEYS = frozenset({"cnpj"})
_TIPO_KEYS = frozenset({"tipo", "subtype"})
_DOC_KEYS = frozenset(
    {"cpf/cnpj", "cpf_cnpj", "cpfcnpj", "documento", "doc", "cpf ou cnpj"}
)
_PJ_TIPOS = frozenset({"pj", "pessoa juridica", "pessoa jurídica", "empresa"})
_PF_TIPOS = frozenset({"pf", "pessoa fisica", "pessoa física"})


@dataclass(frozen=True, slots=True)
class SpreadsheetTargets:
    companies: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    skipped: int = 0
    sheets: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.companies) + len(self.people) + len(self.ids)


def parse_target_spreadsheet(path: Path | str) -> SpreadsheetTargets:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Arquivo nao encontrado: {source}")
    suffix = source.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        tables = _read_xlsx_tables(source)
    elif suffix == ".csv":
        tables = [(source.stem, _read_csv_rows(source))]
    else:
        raise ValueError(
            "Use uma planilha .xlsx ou .csv com colunas de Nome e CPF/CNPJ."
        )
    companies: list[str] = []
    people: list[str] = []
    ids: list[str] = []
    skipped = 0
    seen_company: set[str] = set()
    seen_person: set[str] = set()
    seen_id: set[str] = set()
    used_sheets: list[str] = []
    for sheet_name, rows in tables:
        mapping = _header_map(rows[0] if rows else [])
        if mapping is None:
            continue
        used_sheets.append(sheet_name)
        sheet_is_cnpj = fold_lexical(sheet_name) in {"cnpjs", "cnpj"}
        for raw in rows[1:]:
            names, kind, ignore, documents = _row_targets(raw, mapping, sheet_is_cnpj)
            skipped += ignore
            kept_row = bool(names) and all(is_kept_term(name) for name in names)
            if not kept_row:
                for formatted in parse_target_ids(documents):
                    key = id_canonical(formatted)
                    if key in seen_id:
                        skipped += 1
                        continue
                    seen_id.add(key)
                    ids.append(formatted)
            bucket = companies if kind == "company" else people
            seen = seen_company if kind == "company" else seen_person
            for name in names:
                key = fold_lexical(name)
                if not key or key in seen or is_kept_term(name):
                    if key in seen or is_kept_term(name):
                        skipped += 1
                    continue
                seen.add(key)
                bucket.append(name)
    if not used_sheets:
        raise ValueError(
            "Nao achei colunas de Nome e CPF/CNPJ nesta planilha. "
            "No Nexus, exporte Pessoas e/ou CNPJs."
        )
    if not companies and not people and not ids:
        raise ValueError("A planilha nao trouxe nenhum nome ou CPF/CNPJ aproveitavel.")
    return SpreadsheetTargets(
        companies=companies,
        people=people,
        ids=ids,
        skipped=skipped,
        sheets=tuple(used_sheets),
    )


def merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    seen = {fold_lexical(item) for item in existing if item.strip()}
    out = [item.strip() for item in existing if item.strip()]
    for item in incoming:
        name = item.strip()
        key = fold_lexical(name)
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _fold_header(value: str) -> str:
    return re.sub(r"\s+", " ", fold_lexical(value).replace("_", " ")).strip()


def _header_map(header: list[str]) -> dict[str, int] | None:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = _fold_header(str(cell or ""))
        if not key:
            continue
        if key in _NAME_KEYS and "name" not in mapping:
            mapping["name"] = index
        elif key in _CPF_KEYS and "cpf" not in mapping:
            mapping["cpf"] = index
        elif key in _CNPJ_KEYS and "cnpj" not in mapping:
            mapping["cnpj"] = index
        elif key in _TIPO_KEYS and "tipo" not in mapping:
            mapping["tipo"] = index
        elif key in _DOC_KEYS and "doc" not in mapping:
            mapping["doc"] = index
    if "name" not in mapping:
        return None
    if not any(key in mapping for key in ("cpf", "cnpj", "doc", "tipo")):
        return None
    return mapping


def _cell(row: list[str], mapping: dict[str, int], key: str) -> str:
    index = mapping.get(key)
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _split_names(raw: str) -> list[str]:
    parts = re.split(r"\s*;\s*", raw)
    names: list[str] = []
    for part in parts:
        name = re.sub(r"\s+", " ", part).strip(" \t-–—")
        if len(name) < _MIN_NAME_CHARS:
            continue
        names.append(name)
    return names


def _kind_from_tipo(tipo: str) -> str | None:
    folded = fold_lexical(tipo)
    if folded in _PJ_TIPOS or "juridic" in folded:
        return "company"
    if folded in _PF_TIPOS or "fisic" in folded:
        return "person"
    return None


def _kind_from_digits(value: str) -> str | None:
    digits = only_digits(value)
    compact = re.sub(r"[^0-9A-Za-z]", "", value).upper()
    if len(digits) == 11:
        return "person"
    if len(digits) in {12, 13, 14} or len(compact) in {12, 14}:
        return "company"
    return None


def _row_targets(
    row: list[str], mapping: dict[str, int], sheet_is_cnpj: bool
) -> tuple[list[str], str, int, list[str]]:
    names = _split_names(_cell(row, mapping, "name"))
    documents = _row_documents(row, mapping)
    if not names:
        return [], "person", 0 if documents else 1, documents
    tipo_kind = _kind_from_tipo(_cell(row, mapping, "tipo"))
    cnpj = _cell(row, mapping, "cnpj")
    cpf = _cell(row, mapping, "cpf")
    doc = _cell(row, mapping, "doc")
    if sheet_is_cnpj or tipo_kind == "company" or cnpj:
        return names, "company", 0, documents
    if tipo_kind == "person" or cpf:
        return names, "person", 0, documents
    inferred = _kind_from_digits(doc or cnpj or cpf)
    if inferred:
        return names, inferred, 0, documents
    return names, "person", 0, documents


def _row_documents(row: list[str], mapping: dict[str, int]) -> list[str]:
    values: list[str] = []
    for key in ("cpf", "cnpj", "doc"):
        cell = _cell(row, mapping, key)
        if cell:
            values.append(cell)
    return values


def _read_csv_rows(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    if "\x00" in text[:200]:
        text = raw.decode("utf-16")
    sample = text[:2048]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[str(cell or "").strip() for cell in row] for row in reader if any(str(c).strip() for c in row)]


def _col_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    number = 0
    for char in letters.upper():
        number = number * 26 + (ord(char) - 64)
    return max(0, number - 1)


def _read_xlsx_tables(path: Path) -> list[tuple[str, list[list[str]]]]:
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        sheets = _sheet_targets(archive)
        tables: list[tuple[str, list[list[str]]]] = []
        for name, target in sheets:
            member = _sheet_member(target)
            if member not in archive.namelist():
                continue
            rows = _sheet_rows(archive.read(member), shared)
            if rows:
                tables.append((name, rows))
        return tables


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(name))
    values: list[str] = []
    for item in root.findall("m:si", _NS):
        texts = [
            node.text or ""
            for node in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        ]
        values.append("".join(texts))
    return values


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rels = {
        rel.get("Id"): rel.get("Target", "")
        for rel in rels_root.findall(f"{{{_PKG_REL}}}Relationship")
    }
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall("m:sheets/m:sheet", _NS):
        name = sheet.get("name") or "Planilha"
        rel_id = sheet.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        target = rels.get(rel_id or "", "")
        if target:
            sheets.append((name, target))
    return sheets


def _sheet_member(target: str) -> str:
    cleaned = target.replace("\\", "/").lstrip("/")
    if cleaned.startswith("xl/"):
        return cleaned
    return "xl/" + cleaned


def _sheet_rows(payload: bytes, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(payload)
    rows: list[list[str]] = []
    for row in root.findall("m:sheetData/m:row", _NS):
        cells: dict[int, str] = {}
        max_index = -1
        for cell in row.findall("m:c", _NS):
            ref = cell.get("r") or ""
            index = _col_index(ref) if ref else (max_index + 1)
            cells[index] = _cell_text(cell, shared)
            max_index = max(max_index, index)
        if max_index < 0:
            continue
        line = [cells.get(i, "") for i in range(max_index + 1)]
        if any(item.strip() for item in line):
            rows.append(line)
    return rows


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "s":
        node = cell.find("m:v", _NS)
        if node is None or node.text is None:
            return ""
        try:
            return shared[int(node.text)]
        except (ValueError, IndexError):
            return ""
    if kind == "inlineStr":
        texts = [
            node.text or ""
            for node in cell.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
            )
        ]
        return "".join(texts)
    node = cell.find("m:v", _NS)
    if node is None or node.text is None:
        return ""
    raw = node.text.strip()
    if re.fullmatch(r"-?\d+\.0+", raw):
        return raw.split(".", 1)[0]
    if re.fullmatch(r"-?\d+\.\d+E\+\d+", raw, re.IGNORECASE):
        try:
            return str(int(float(raw)))
        except ValueError:
            return raw
    return raw

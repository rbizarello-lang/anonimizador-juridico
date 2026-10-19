"""Importa nomes e CPF/CNPJ de uma planilha (Nexus xlsx/csv) para a lista de alvos.

O usuario anexa um arquivo com Nome e CPF ou CNPJ. Nao e o dump da Receita.
Abas no formato do Nexus (Pessoas / CNPJs) e planilhas genericas com esses
cabecalhos sao aceitas. Nomes vao para as caixas de empresa/pessoa; documentos
validos vao para a caixa de CPF/CNPJ da carteira. Numero sem DV ou CNPJ de
orgao publico nao entra.

H9-i lote B1: o formato combinado "Para o Anonimizador" (``saidas/privados/regras/formato_exportacao_nexus.txt``)
le todas as abas de uma vez: Pessoas (apelidos, papel), Empresas (nome_fantasia, papel, nire), Processos, CDAs
(processo_adm), Contatos (titular, tipo, valor) e Veiculos (placa, renavam). Coluna ausente ou vazia e aceita;
``vinculo``, ``situacao``, ``classe``, ``relacao`` e ``papel_original`` sao ignorados (nao fundem nada). Cada linha
descartada entra em ``discarded`` com o motivo, para o resumo antes de salvar. A planilha antiga continua valendo.
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

from .case_list import classify_cda, classify_contact, classify_process, format_alias_line
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
_ALIAS_KEYS = ("nome fantasia", "nomes fantasia", "apelidos", "apelido", "outras grafias")
_ROLE_KEYS = frozenset({"papel"})
_NIRE_KEYS = frozenset({"nire"})
_COMPANY_SHEETS = frozenset({"cnpjs", "cnpj", "empresas", "empresa"})
# O papel do Nexus traduzido para o elenco, so para papel de particular: "procurador" no Nexus e o procurador DA
# PARTE (advogado), nunca o da Fazenda; papel fora da tabela nao entra (o nome continua sendo trocado).
_NEXUS_ROLES = {
    "devedor": "parte",
    "devedora": "parte",
    "grupo": "parte",
    "sucessora": "parte",
    "socio": "socio",
    "socia": "socio",
    "administrador": "socio",
    "administradora": "socio",
    "responsavel": "socio",
    "conjuge": "terceiro",
    "parente": "terceiro",
    "terceiro": "terceiro",
    "terceira": "terceiro",
    "laranja": "terceiro",
    "interposta": "terceiro",
    "credora": "terceiro",
    "procurador": "advogado",
    "procuradora": "advogado",
    "advogado": "advogado",
    "advogada": "advogado",
}


@dataclass(frozen=True, slots=True)
class SpreadsheetTargets:
    companies: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    skipped: int = 0
    sheets: tuple[str, ...] = ()
    # H9-i lote B1 (formato combinado): linhas da aba Caso e o papel de cada nome.
    aliases: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    cdas: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)
    # motivo -> linhas descartadas (para o resumo antes de salvar).
    discarded: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return (
            len(self.companies)
            + len(self.people)
            + len(self.ids)
            + len(self.aliases)
            + len(self.processes)
            + len(self.cdas)
            + len(self.contacts)
        )

    def summary_lines(self) -> list[str]:
        """O resumo mostrado antes de salvar: quanto entra de cada tipo e o que foi descartado, e por que."""

        lines = [
            f"Pessoas: {len(self.people)}",
            f"Empresas: {len(self.companies)}",
            f"CPF/CNPJ/NIRE: {len(self.ids)}",
            f"Outras grafias: {len(self.aliases)} linha(s)",
            f"Processos: {len(self.processes)}",
            f"CDAs: {len(self.cdas)}",
            f"Contatos e veículos: {len(self.contacts)}",
        ]
        if self.discarded:
            detail = "; ".join(f"{reason}: {count}" for reason, count in sorted(self.discarded.items()))
            lines.append(f"Descartadas: {sum(self.discarded.values())} ({detail})")
        else:
            lines.append("Descartadas: 0")
        return lines


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
    out = _Collector()
    used_sheets: list[str] = []
    for sheet_name, rows in tables:
        header = rows[0] if rows else []
        reader = _CASE_SHEETS.get(fold_lexical(sheet_name).strip())
        if reader is not None:
            columns = _columns(header)
            if reader(out, columns, rows[1:]):
                used_sheets.append(sheet_name)
            continue
        mapping = _header_map(header)
        if mapping is None:
            continue
        used_sheets.append(sheet_name)
        sheet_is_cnpj = fold_lexical(sheet_name).strip() in _COMPANY_SHEETS
        columns = _columns(header)
        for raw in rows[1:]:
            _read_name_row(out, raw, mapping, columns, sheet_is_cnpj)
    if not used_sheets:
        raise ValueError(
            "Nao achei colunas de Nome e CPF/CNPJ nesta planilha. "
            "No Nexus, exporte Pessoas e/ou CNPJs, ou o arquivo \"Para o Anonimizador\"."
        )
    result = out.result(tuple(used_sheets))
    if not result.total:
        raise ValueError("A planilha nao trouxe nenhum nome, documento, processo, CDA ou contato aproveitavel.")
    return result


class _Collector:
    """O que a planilha traz, sem repeticao, e as linhas descartadas por motivo."""

    def __init__(self) -> None:
        self.companies: list[str] = []
        self.people: list[str] = []
        self.ids: list[str] = []
        self.aliases: list[str] = []
        self.processes: list[str] = []
        self.cdas: list[str] = []
        self.contacts: list[str] = []
        self.roles: dict[str, str] = {}
        self.discarded: dict[str, int] = {}
        self.skipped = 0
        self._seen: dict[str, set[str]] = {}

    def drop(self, reason: str, count: int = 1) -> None:
        if count <= 0:
            return
        self.discarded[reason] = self.discarded.get(reason, 0) + count
        self.skipped += count

    def add(self, bucket: str, value: str, key: str, *, repeated: str = "") -> bool:
        seen = self._seen.setdefault(bucket, set())
        if not key:
            return False
        if key in seen:
            if repeated:
                self.drop(repeated)
            return False
        seen.add(key)
        getattr(self, bucket).append(value)
        return True

    def add_ids(self, documents: list[str]) -> None:
        filled = [item for item in documents if item.strip()]
        parsed = parse_target_ids(filled)
        self.drop("documento sem dígito verificador válido ou de órgão público", len(filled) - len(parsed))
        for formatted in parsed:
            self.add("ids", formatted, id_canonical(formatted), repeated="documento repetido")

    def result(self, sheets: tuple[str, ...]) -> SpreadsheetTargets:
        return SpreadsheetTargets(
            companies=self.companies,
            people=self.people,
            ids=self.ids,
            skipped=self.skipped,
            sheets=sheets,
            aliases=self.aliases,
            processes=self.processes,
            cdas=self.cdas,
            contacts=self.contacts,
            roles=self.roles,
            discarded=self.discarded,
        )


def _columns(header: list[str]) -> dict[str, int]:
    """Cabecalho dobrado -> indice (a primeira coluna com aquele nome vale)."""

    found: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = _fold_header(str(cell or ""))
        if key and key not in found:
            found[key] = index
    return found


def _value(row: list[str], columns: dict[str, int], *keys: str) -> str:
    for key in keys:
        index = columns.get(key)
        if index is not None and index < len(row) and str(row[index] or "").strip():
            return str(row[index]).strip()
    return ""


def _read_name_row(
    out: _Collector,
    raw: list[str],
    mapping: dict[str, int],
    columns: dict[str, int],
    sheet_is_cnpj: bool,
) -> None:
    names, kind, ignore, documents = _row_targets(raw, mapping, sheet_is_cnpj)
    if ignore:
        out.drop("linha sem nome", ignore)
    kept_row = bool(names) and all(is_kept_term(name) for name in names)
    if kept_row:
        out.drop("termo protegido (União, PGFN...)")
        return
    nire = only_digits(_value(raw, columns, *_NIRE_KEYS))
    if nire:
        documents = [*documents, f"NIRE {nire}"]
    out.add_ids(documents)
    bucket = "companies" if kind == "company" else "people"
    for name in names:
        if is_kept_term(name):
            out.drop("termo protegido (União, PGFN...)")
            continue
        out.add(bucket, name, fold_lexical(name), repeated="nome repetido")
    if not names:
        return
    principal = names[0]
    # Todas as colunas de grafia da linha (a aba Empresas do Nexus traz nome_fantasia E apelidos).
    aliases = [
        alias
        for key in _ALIAS_KEYS
        for alias in _split_names(_value(raw, columns, key))
        if fold_lexical(alias) != fold_lexical(principal) and not is_kept_term(alias)
    ]
    # Varias razoes na mesma celula (troca de razao social): as demais sao grafias da primeira.
    aliases = [*names[1:], *aliases]
    if aliases:
        line = format_alias_line(principal, list(dict.fromkeys(aliases)))
        out.add("aliases", line, fold_lexical(line))
    role = _NEXUS_ROLES.get(fold_lexical(_value(raw, columns, *_ROLE_KEYS)).replace(" ", "_"))
    if role:
        out.roles.setdefault(principal, role)


def _read_processes(out: _Collector, columns: dict[str, int], rows: list[list[str]]) -> bool:
    if "numero" not in columns:
        return False
    for raw in rows:
        value = _value(raw, columns, "numero")
        parsed = classify_process(value) if value else None
        if parsed is None:
            out.drop("processo sem número válido")
            continue
        out.add("processes", value, only_digits(value), repeated="processo repetido")
    return True


def _read_cdas(out: _Collector, columns: dict[str, int], rows: list[list[str]]) -> bool:
    if "numero" not in columns:
        return False
    for raw in rows:
        value = _value(raw, columns, "numero")
        cda = classify_cda(value) if value else None
        if cda is None:
            out.drop("CDA sem número válido")
        else:
            out.add("cdas", cda, re.sub(r"[^0-9A-Za-z]", "", cda).upper(), repeated="CDA repetida")
        administrative = _value(raw, columns, "processo adm", "processo administrativo")
        if administrative:
            line = f"PA: {administrative}"
            if classify_process(line) is None:
                out.drop("processo administrativo sem número válido")
            else:
                out.add("processes", line, only_digits(administrative), repeated="processo repetido")
    return True


def _read_contacts(out: _Collector, columns: dict[str, int], rows: list[list[str]]) -> bool:
    if "valor" not in columns:
        return False
    for raw in rows:
        value = _value(raw, columns, "valor")
        kind = fold_lexical(_value(raw, columns, "tipo"))
        label = {"endereco": "endereço", "telefone": "telefone", "email": "e-mail", "e-mail": "e-mail"}.get(kind, "")
        line = f"{label}: {value}" if label else value
        if not value or classify_contact(line) is None:
            out.drop("contato vazio ou fora do formato")
            continue
        out.add("contacts", line, fold_lexical(line), repeated="contato repetido")
    return True


def _read_vehicles(out: _Collector, columns: dict[str, int], rows: list[list[str]]) -> bool:
    if "placa" not in columns and "renavam" not in columns:
        return False
    for raw in rows:
        for kind in ("placa", "renavam"):
            value = _value(raw, columns, kind)
            if not value:
                continue
            line = f"{kind}: {value}"
            if classify_contact(line) is None:
                out.drop(f"{kind} fora do formato")
                continue
            out.add("contacts", line, fold_lexical(line), repeated="veículo repetido")
    return True


_CASE_SHEETS = {
    "processos": _read_processes,
    "cdas": _read_cdas,
    "cda": _read_cdas,
    "contatos": _read_contacts,
    "veiculos": _read_vehicles,
}


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

"""Indice local dos Dados Abertos do CNPJ (Receita Federal), recorte Sul.

O arquivo de empresas nao tem UF. O recorte RS/SC/PR cruza estabelecimentos
e depois busca a razao social. Natureza 1xxx (administracao publica) nao vira
empresa privada. QSA fica para depois.
"""

from __future__ import annotations

import csv
import re
import sqlite3
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from anonimizador_juridico.validators import is_valid_cnpj, normalize_cnpj

SOUTHERN_UFS = frozenset({"RS", "SC", "PR"})
DEFAULT_RELATIVE_PATH = Path("dados") / "cnpj_sul.sqlite"

# Mesmo padrao do Presidio (CNPJ numerico ou alfanumerico).
_CNPJ_IN_TEXT = re.compile(
    r"(?<![0-9A-Za-z])"
    r"[0-9A-Za-z]{2}\.?[0-9A-Za-z]{3}\.?[0-9A-Za-z]{3}"
    r"/?[0-9A-Za-z]{4}-?\d{2}"
    r"(?![0-9A-Za-z])"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS empresas (
    cnpj TEXT PRIMARY KEY,
    cnpj_basico TEXT NOT NULL,
    razao_social TEXT NOT NULL,
    nome_fantasia TEXT,
    natureza TEXT,
    uf TEXT,
    publica INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_empresas_basico ON empresas(cnpj_basico);
CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    cnpj: str
    cnpj_basico: str
    razao_social: str
    nome_fantasia: str
    natureza: str
    uf: str
    publica: bool

    def private_names(self) -> list[str]:
        if self.publica:
            return []
        names: list[str] = []
        razao = self.razao_social.strip()
        if razao:
            names.append(razao)
        fantasia = self.nome_fantasia.strip()
        if fantasia and fantasia.casefold() != razao.casefold():
            names.append(fantasia)
        return names


def default_index_path(project_root: Path | None = None) -> Path:
    root = project_root or Path(__file__).resolve().parents[2]
    return root / DEFAULT_RELATIVE_PATH


def is_public_natureza(code: str) -> bool:
    """Administracao publica na tabela da Receita: grupo 1xxx."""

    digits = re.sub(r"\D+", "", str(code))
    return bool(digits) and digits.startswith("1")


def extract_valid_cnpjs(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _CNPJ_IN_TEXT.finditer(text):
        raw = match.group(0)
        if not is_valid_cnpj(raw):
            continue
        key = normalize_cnpj(raw)
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


class CnpjIndex:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file() and self.path.stat().st_size > 0

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def lookup(self, cnpj: str) -> CompanyRecord | None:
        if not self.exists():
            return None
        key = normalize_cnpj(cnpj)
        if len(key) != 14:
            return None
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM empresas WHERE cnpj = ? LIMIT 1",
                (key,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM empresas WHERE cnpj_basico = ? LIMIT 1",
                    (key[:8],),
                ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return _row_to_record(row)

    def stats(self) -> dict[str, str]:
        if not self.exists():
            return {"presente": "nao"}
        connection = self.connect()
        try:
            count = connection.execute("SELECT COUNT(*) FROM empresas").fetchone()[0]
            meta = {
                str(item[0]): str(item[1])
                for item in connection.execute("SELECT chave, valor FROM meta")
            }
        finally:
            connection.close()
        meta["presente"] = "sim"
        meta["registros"] = str(count)
        return meta


def write_records(
    path: Path,
    records: Iterable[CompanyRecord],
    *,
    meta: dict[str, str] | None = None,
) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_SCHEMA)
        rows = [
            (
                item.cnpj,
                item.cnpj_basico,
                item.razao_social,
                item.nome_fantasia,
                item.natureza,
                item.uf,
                1 if item.publica else 0,
            )
            for item in records
        ]
        connection.executemany(
            "INSERT OR REPLACE INTO empresas "
            "(cnpj, cnpj_basico, razao_social, nome_fantasia, natureza, uf, publica) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        payload = {
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
            "regiao": "RS,SC,PR",
            **(meta or {}),
        }
        connection.executemany(
            "INSERT OR REPLACE INTO meta (chave, valor) VALUES (?, ?)",
            list(payload.items()),
        )
        connection.commit()
        return len(rows)
    finally:
        connection.close()


def import_southern_dump(source: Path, destination: Path) -> int:
    """Le CSVs/ZIPs da Receita e grava o indice so com estabelecimentos do Sul."""

    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"Pasta ou arquivo nao encontrado: {source}")

    establishments: dict[str, tuple[str, str, str]] = {}
    for row in _iter_csv_rows(source, kind="estabelecimento"):
        if len(row) < 20:
            continue
        uf = row[19].strip().upper()
        if uf not in SOUTHERN_UFS:
            continue
        basico = normalize_cnpj(row[0])
        ordem = normalize_cnpj(row[1])
        dv = re.sub(r"\D+", "", row[2])
        cnpj = f"{basico}{ordem}{dv}"
        if len(cnpj) != 14:
            continue
        fantasia = row[4].strip()
        establishments[cnpj] = (basico, fantasia, uf)

    if not establishments:
        raise ValueError(
            "Nenhum estabelecimento de RS/SC/PR encontrado. "
            "Confira se a pasta tem os arquivos ESTABELE da Receita."
        )

    wanted = {basico for basico, _fantasia, _uf in establishments.values()}
    companies: dict[str, tuple[str, str]] = {}
    for row in _iter_csv_rows(source, kind="empresa"):
        if len(row) < 3:
            continue
        basico = normalize_cnpj(row[0])
        if basico not in wanted:
            continue
        razao = row[1].strip()
        natureza = row[2].strip()
        if razao:
            companies[basico] = (razao, natureza)

    records: list[CompanyRecord] = []
    for cnpj, (basico, fantasia, uf) in establishments.items():
        info = companies.get(basico)
        if info is None:
            continue
        razao, natureza = info
        records.append(
            CompanyRecord(
                cnpj=cnpj,
                cnpj_basico=basico,
                razao_social=razao,
                nome_fantasia=fantasia,
                natureza=natureza,
                uf=uf,
                publica=is_public_natureza(natureza),
            )
        )
    if not records:
        raise ValueError(
            "Estabelecimentos do Sul encontrados, mas nenhuma razao social "
            "correspondente nos arquivos EMPRECSV."
        )
    return write_records(
        destination,
        records,
        meta={"fonte": str(source.resolve()), "origem": "dados_abertos_rfb"},
    )


def _row_to_record(row: sqlite3.Row) -> CompanyRecord:
    return CompanyRecord(
        cnpj=str(row["cnpj"]),
        cnpj_basico=str(row["cnpj_basico"]),
        razao_social=str(row["razao_social"] or ""),
        nome_fantasia=str(row["nome_fantasia"] or ""),
        natureza=str(row["natureza"] or ""),
        uf=str(row["uf"] or ""),
        publica=bool(row["publica"]),
    )


def _iter_csv_rows(source: Path, *, kind: str) -> Iterator[list[str]]:
    needle = "ESTABELE" if kind == "estabelecimento" else "EMPRECSV"
    for path in _candidate_files(source, needle):
        yield from _rows_from_path(path, needle)


def _candidate_files(source: Path, needle: str) -> list[Path]:
    if source.is_file():
        return [source]
    matches: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.upper()
        if needle in name or path.suffix.lower() == ".zip":
            matches.append(path)
    return sorted(matches)


def _rows_from_path(path: Path, needle: str) -> Iterator[list[str]]:
    name = path.name.upper()
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or needle not in info.filename.upper():
                    continue
                with archive.open(info) as handle:
                    yield from _csv_rows(handle)
        return
    if needle not in name:
        return
    with path.open("rb") as handle:
        yield from _csv_rows(handle)


def _csv_rows(handle) -> Iterator[list[str]]:
    text = _TextWrapper(handle)
    reader = csv.reader(text, delimiter=";")
    for row in reader:
        if row:
            yield row


class _TextWrapper:
    """Decodifica latin-1 em streaming para o csv.reader."""

    def __init__(self, binary) -> None:
        self._binary = binary

    def __iter__(self) -> Iterator[str]:
        for raw in self._binary:
            if isinstance(raw, bytes):
                yield raw.decode("latin-1")
            else:
                yield str(raw)

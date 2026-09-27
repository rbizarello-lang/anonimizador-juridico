"""INDICE.md do lote (fila 3.x E3): por arquivo, eventos e documentos com faixa de folhas.

Monta-se do Markdown ja tokenizado (nada do mapa privado): o carimbo do eproc de cada
folha ("Processo X, Evento 1, CONTRSOCIAL4, Pagina 7") da a citacao; o marcador do PDF
("Evento 1 - Doc. 4 - CONTRATO SOCIAL") da o titulo e cobre a folha sem carimbo (capa,
separacao, extrato). Folha = pagina fisica do arquivo (``<!-- pagina-origem: N -->``).
Documento que termina num PARTE e continua no seguinte fica marcado nos dois.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

INDEX_NAME = "INDICE.md"

_ANCHOR = re.compile(r"<!-- pagina-origem: (\d+) -->")
_ANCHOR_LINE = re.compile(r"<!-- pagina-origem: (\d+) -->(?!\n\[fl\. )")
_STAMP = re.compile(r"Processo\s+(\S+?),\s*Evento\s+(\d+)\s*,\s*(\S+?)\s*,\s*P[áa]gina\s+(\d+)")
_BOOKMARK = re.compile(r"^#{1,6} Evento\.?\s+(\d+)\s*-\s*Doc\.?\s*(\d+)\s*-\s*(.+?)\s*$", re.M)
_PAGE_HEADING = re.compile(r"^#{1,6} P[áa]g\.\s*(\d+)\s*$", re.M)
_OTHER_HEADING = re.compile(r"^#{1,6} (?!P[áa]g\.)(?!Evento\.?\s+\d)(.+?)\s*$", re.M)
_TRAILING_NUMBER = re.compile(r"(\d+)$")
_PART = re.compile(r"^(.*)_PARTE_(\d+)$", re.I)
_COVERAGE = re.compile(r"<!-- cobertura: ([^>]*) -->")
_SEQUENCE = re.compile(r"Sequência Evento:\s*(\d+)")
_MOVEMENT = re.compile(r"(?m)^Evento: (.+?) — Data: (\d{2}/\d{2}/\d{4})")
_DOCUMENT_DATE = re.compile(r"(?m)^Tipo documento: .*? — Data: (\d{2}/\d{2}/\d{4})")


@dataclass
class _Group:
    event: str
    doc: str
    title: str
    code: str = ""
    sheets: list[int] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    ocr: int = 0
    tables: int = 0
    continues_from: str = ""
    continues_to: str = ""
    date: str = ""
    # Folha -> (codigo do carimbo, pagina do documento), para a ancora visivel.
    sheet_pages: dict[int, tuple[str, int | None]] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.event, self.doc)


def build_index(documents: Sequence[tuple[str, str]]) -> str:
    """``documents``: (nome do .md, texto tokenizado), na ordem do lote."""

    parsed = [(name, *_file_groups(text)) for name, text in documents]
    _mark_continuations(parsed)
    lines = [
        "# Índice do lote",
        "",
        "Como citar: pelo carimbo do eproc, “Evento N, CÓDIGO, Página P”. Folha = página física "
        "do arquivo (marcada no texto como `<!-- pagina-origem: N -->`). Os nomes seguem os "
        "tokens do texto.",
        "",
    ]
    for name, process, coverage, groups in parsed:
        lines.append(f"## {name}")
        lines.append("")
        facts = [f"processo {process}" if process else "", coverage]
        lines.append(" · ".join(item for item in facts if item))
        lines.append("")
        if not any(group.event for group in groups):
            total = sum(len(group.sheets) for group in groups)
            lines.append(f"Sem carimbo nem marcador de evento: documento avulso, folhas 1–{total}.")
            lines.append("")
            continue
        lines.append("| Evento | Documento | Folhas | Páginas do documento | OCR | Tabelas |")
        lines.append("|---|---|---|---|---|---|")
        for group in groups:
            lines.append(_row(group))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_lote_index(export_dir: Path, markdown_paths: Sequence[Path]) -> Path:
    documents = [(path.name, path.read_text(encoding="utf-8")) for path in markdown_paths]
    target = Path(export_dir) / INDEX_NAME
    target.write_text(build_index(documents), encoding="utf-8")
    return target


def _file_groups(text: str) -> tuple[str, str, list[_Group]]:
    parts = _ANCHOR.split(text)
    titles = {(event, doc): title for event, doc, title in _BOOKMARK.findall(text)}
    process = ""
    groups: list[_Group] = []
    current: tuple[str, str, str] = ("", "", "")
    for index in range(1, len(parts), 2):
        sheet, body = int(parts[index]), parts[index + 1]
        page: int | None = None
        code = ""
        for event, doc, title in _BOOKMARK.findall(body):
            current = (event, doc, title)
        if not current[0]:
            other = _OTHER_HEADING.findall(body)
            if other:
                current = ("", "", other[-1])
        movement = _movement(body)
        if movement is not None:
            current = movement
        stamp = _STAMP.search(body)
        if stamp is not None:
            process = process or stamp.group(1)
            number = _TRAILING_NUMBER.search(stamp.group(3))
            event, doc = stamp.group(2), number.group(1) if number else stamp.group(3)
            current = (event, doc, titles.get((event, doc), current[2] if current[:2] == (event, doc) else ""))
            code, page = stamp.group(3), int(stamp.group(4))
        else:
            heading = _PAGE_HEADING.findall(body)
            page = int(heading[-1]) if heading and current[0] else None
        event, doc, title = current
        if not groups or groups[-1].key != (event, doc):
            groups.append(_Group(event=event, doc=doc, title=title))
        elif not event and title and title not in groups[-1].title.split(" / "):
            # Capa e paginas iniciais sem evento: uma linha so.
            groups[-1].title = f"{groups[-1].title} / {title}" if groups[-1].title else title
        group = groups[-1]
        group.code = group.code or code
        dated = _DOCUMENT_DATE.search(body)
        if dated is not None and group.doc:
            # Data do evento, da folha de separacao que abre o documento.
            group.date = group.date or dated.group(1)
        group.sheets.append(sheet)
        group.sheet_pages[sheet] = (code, page)
        if page is not None:
            group.pages.append(page)
        group.ocr += "<!-- ocr:" in body
        group.tables += len(re.findall(r"(?m)^\|---", body)) + len(re.findall(r"(?m)^```text", body))
    coverage = _coverage_label(text)
    return process, coverage, groups


def add_sheet_anchors(text: str) -> str:
    """Linha visivel no topo de cada folha: "[fl. 84 · Evento 1 · CONTRSOCIAL4 · p. 1]".

    O comentario ``<!-- pagina-origem: N -->`` continua (as reguas e o indice o leem); a
    linha visivel da a citacao do eproc sem a IA ter de achar o carimbo no meio da folha.
    """

    _process, _coverage, groups = _file_groups(text)
    labels: dict[int, str] = {}
    for group in groups:
        for sheet in group.sheets:
            code, page = group.sheet_pages.get(sheet, ("", None))
            parts = [f"fl. {sheet}"]
            if group.event:
                parts.append(f"Evento {group.event}")
                if group.doc:
                    parts.append(code or f"Doc. {group.doc}")
                    if page is not None:
                        parts.append(f"p. {page}")
                else:
                    parts.append("movimentação")
            labels[sheet] = "[" + " · ".join(parts) + "]"

    def anchor(match: re.Match[str]) -> str:
        label = labels.get(int(match.group(1)))
        return f"{match.group(0)}\n{label}" if label else match.group(0)

    return _ANCHOR_LINE.sub(anchor, text)


def _movement(body: str) -> tuple[str, str, str] | None:
    """Folha de separacao de evento sem documento (intimacao, prazo, ciencia, juntada)."""

    if "PÁGINA DE SEPARAÇÃO" not in body or "Tipo documento:" in body:
        return None
    sequence = _SEQUENCE.search(body)
    if sequence is None:
        return None
    described = _MOVEMENT.search(body)
    title = "movimentação sem documento"
    if described is not None:
        title += f": {described.group(1).strip()} ({described.group(2)})"
    return (sequence.group(1), "", title)


def _coverage_label(text: str) -> str:
    match = _COVERAGE.search(text)
    if match is None:
        return ""
    fields = dict(item.split("=", 1) for item in match.group(1).split("; ") if "=" in item)
    ocr = sum(int(fields.get(key, 0) or 0) for key in ("escaneadas", "vetoriais", "fonte-quebrada"))
    unread = fields.get("nao-lidas", "nenhuma")
    return (
        f"{fields.get('paginas', '?')} folhas ({fields.get('nativas', '?')} nativas, {ocr} por OCR, "
        f"não lidas: {unread})"
    )


def _mark_continuations(parsed: list[tuple[str, str, str, list[_Group]]]) -> None:
    by_base: dict[str, list[tuple[int, str, list[_Group]]]] = {}
    for name, _process, _coverage, groups in parsed:
        match = _PART.match(Path(name).stem)
        if match is not None:
            by_base.setdefault(match.group(1).casefold(), []).append((int(match.group(2)), name, groups))
    for parts in by_base.values():
        parts.sort()
        for (_n, name, groups), (_m, next_name, next_groups) in zip(parts, parts[1:]):
            # A capa do PARTE seguinte nao interrompe o documento.
            last = next((group for group in reversed(groups) if group.doc), None)
            first = next((group for group in next_groups if group.doc), None)
            if last is not None and first is not None and last.key == first.key:
                last.continues_to = next_name
                first.continues_from = name


def _row(group: _Group) -> str:
    doc = f"Doc. {group.doc} — {group.title}" if group.doc else (group.title or "—")
    if group.code:
        doc += f" ({group.code})"
    if group.date:
        doc += f", {group.date}"
    if group.continues_from:
        doc += f" · continuação de {group.continues_from}"
    if group.continues_to:
        doc += f" · continua em {group.continues_to}"
    return (
        f"| {group.event or '—'} | {doc.replace('|', '/')} | {_span(group.sheets)} | "
        f"{_span(group.pages)} | {group.ocr or ''} | {group.tables or ''} |"
    )


def _span(numbers: Sequence[int]) -> str:
    if not numbers:
        return "—"
    low, high = min(numbers), max(numbers)
    return str(low) if low == high else f"{low}–{high}"

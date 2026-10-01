"""INDICE.md do lote (fila 3.x E3): por arquivo, eventos e documentos com faixa de folhas.

Monta-se do Markdown ja tokenizado (nada do mapa privado): o carimbo do eproc de cada
folha ("Processo X, Evento 1, CONTRSOCIAL4, Pagina 7") da a citacao; o marcador do PDF
("Evento 1 - Doc. 4 - CONTRATO SOCIAL") da o titulo e cobre a folha sem carimbo (capa,
separacao, extrato). Folha = pagina fisica do arquivo (``<!-- pagina-origem: N -->``).
Documento que termina num PARTE e continua no seguinte fica marcado nos dois.

Arquivo sem carimbo nem marcador de evento (contrato social avulso, extrato): o codigo vem do
nome do arquivo ("..._1_CONTRSOCIAL13.pdf"), a ancora visivel fica "[CONTRSOCIAL13 · fl. 4]" (so
"fl. 4" seria ambiguo entre dezenas de arquivos) e o indice traz, quando o texto diz, a empresa
(token), o ato (constituicao, alteracao, consolidacao) e a data do registro na Junta Comercial.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

INDEX_NAME = "INDICE.md"

_ANCHOR = re.compile(r"<!-- pagina-origem: (\d+) -->")
_ANCHOR_LINE = re.compile(r"<!-- pagina-origem: (\d+) -->(?!\n\[(?:fl\. |[A-ZÀ-Ý]{2,}\d{1,3} · fl\. ))")
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

# Documento avulso: codigo no fim do nome do arquivo ("<processo>_<evento>_CONTRSOCIAL13", "11_RENAJUD1").
_FILE_CODE = re.compile(r"_([A-ZÀ-Ý]{2,}\d{1,3})(?:_\d+)?$")
_DATE = r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})"
# Carimbo da Junta Comercial, em tres formas vistas: "Certifico o registro sob o nº N em DD/MM/AAAA da
# empresa X"; "Certifico o registro em: DD/MM/AAAA sob nº: N"; "Certifico que o ato ... foi arquivado na
# Junta Comercial sob o nº N, em DD/MM/AAAA" (a 1ª data, nesta, e a do protocolo).
_REGISTRY_DATES = (
    re.compile(
        r"(?is)certifico\s+o\s+registro\s*,?\s*(?:sob\s+o\s+n[^\s\d]{0,2}\s*[:.]?\s*[\w./-]{1,20}\s+)?"
        rf"em\s*[:;.]?\s*{_DATE}"
    ),
    re.compile(
        r"(?is)certifico\s+que\s+o\s+ato\b.{0,500}?arquivad[oa]\s+na\s+junta\s+comercial"
        rf"\s+sob\s+o\s+n[^\s\d]{{0,2}}\s*[:.]?\s*[\w./-]{{1,20}}\s*,?\s*em\s*[:;.]?\s*{_DATE}"
    ),
)
_STAMP_COMPANY = re.compile(
    r"(?is)certifico\s+(?:o\s+registro|que\s+o\s+ato)[^\[]{0,220}?\bda\s+empresa\s*:?\s*(\[EMPRESA_\d+\])"
)
_COMPANY_TOKEN = re.compile(r"\[EMPRESA_\d+\]")
# Ficha cadastral da Junta (nao e ato de registro): "Ficha Cadastral" no topo, "Nome da Empresa: [EMPRESA_n]".
_FICHA = re.compile(r"(?is)\A(?:\s*<!--.*?-->\s*|\s*\[[^\]\n]*\]\s*)*ficha\s+cadastral[ \t]*(?:\n[ \t]*(\d{2}/\d{2}/\d{4}))?")
_FICHA_COMPANY = re.compile(r"(?i)nome\s+da\s+empresa:\s*(\[EMPRESA_\d+\])")
_CONTRACT_CODE = "CONTRSOCIAL"
_ACT_HEAD_SHEETS = 4
_ACT_CONSOLIDATION = re.compile(r"(?i)consolida[çc][ãa]o")
_ACT_AMENDMENT = re.compile(r"(?i)altera[çc][ãa]o|altera[çc][õo]es\s+contratuais")
_ACT_CONSTITUTION = re.compile(
    r"(?i)contrato\s+(?:social\s+)?de\s+constitui[çc][ãa]o|constitui[çc][ãa]o\s+d[ea]\s+(?:sociedade|empresa|firma)|"
    r"\bconstituir\s+(?:entre\s+si\s+)?(?:uma\s+)?(?:sociedade|empresa)"
)


@dataclass
class _Standalone:
    """O que o texto de um documento avulso diz de si: empresa (token), ato, data do registro."""

    code: str = ""
    companies: list[str] = field(default_factory=list)
    act: str = ""
    dates: list[str] = field(default_factory=list)
    issued: str = ""  # ficha cadastral: data de emissao


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
    standalone: _Standalone | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.event, self.doc)


def build_index(documents: Sequence[tuple[str, str]]) -> str:
    """``documents``: (nome do .md, texto tokenizado), na ordem do lote."""

    parsed = [(name, *_file_groups(text)) for name, text in documents]
    text_of = dict(documents)
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
            lines.append(_standalone_line(name, text_of[name], groups))
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


def add_sheet_anchors(text: str, name: str = "") -> str:
    """Linha visivel no topo de cada folha: "[fl. 84 · Evento 1 · CONTRSOCIAL4 · p. 1]".

    Arquivo sem nenhum carimbo do eproc cujo nome termina em codigo ("..._1_CONTRSOCIAL13.md"):
    "[CONTRSOCIAL13 · fl. 4]". Com carimbo em qualquer folha, o formato eproc fica como era.

    O comentario ``<!-- pagina-origem: N -->`` continua (as reguas e o indice o leem); a
    linha visivel da a citacao do eproc sem a IA ter de achar o carimbo no meio da folha.
    """

    _process, _coverage, groups = _file_groups(text)
    labels: dict[int, str] = {}
    code = document_code(name)
    if code and not any(group.event for group in groups):
        for group in groups:
            for sheet in group.sheets:
                labels[sheet] = f"[{code} · fl. {sheet}]"
        groups = []
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


def document_code(name: str) -> str:
    """Codigo do documento no fim do nome do arquivo ("50059_1_CONTRSOCIAL13.md" -> "CONTRSOCIAL13")."""

    match = _FILE_CODE.search(Path(name).stem)
    return match.group(1) if match else ""


def is_standalone(text: str) -> bool:
    """Sem carimbo nem marcador de evento do eproc em nenhuma folha."""

    return not any(group.event for group in _file_groups(text)[2])


def standalone_facts(text: str, name: str = "") -> _Standalone:
    """Empresa, ato e datas de registro de um documento avulso, so do que o texto diz."""

    facts = _Standalone(code=document_code(name))
    for match in _STAMP_COMPANY.finditer(text):
        if match.group(1) not in facts.companies:
            facts.companies.append(match.group(1))
    found: list[tuple[int, str]] = []
    for pattern in _REGISTRY_DATES:
        for match in pattern.finditer(text):
            day, month, year = (int(match.group(i)) for i in (1, 2, 3))
            if 1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100:
                found.append((match.start(), f"{day:02d}/{month:02d}/{year}"))
    for _position, date in sorted(found):
        if date not in facts.dates:
            facts.dates.append(date)
    ficha = _FICHA.match(text)
    if ficha is not None:
        facts.act = "ficha cadastral da Junta Comercial"
        facts.issued = ficha.group(1) or ""
        for match in _FICHA_COMPANY.finditer(text):
            if match.group(1) not in facts.companies:
                facts.companies.append(match.group(1))
    elif facts.dates or facts.companies or facts.code.startswith(_CONTRACT_CODE):
        # Ato so de documento que se apresenta como contrato (carimbo da Junta ou codigo eproc de
        # contrato social): a peticao que cita "alteracao e consolidacao" nao ganha ato.
        facts.act = _act(text)
    return facts


def _act(text: str) -> str:
    parts = _ANCHOR.split(text)
    head = "\n".join(parts[i + 1] for i in range(1, len(parts), 2) if int(parts[i]) <= _ACT_HEAD_SHEETS)

    def says(pattern: re.Pattern[str]) -> bool:
        return bool(pattern.search(head)) or len(pattern.findall(text)) >= 2

    acts = []
    if _ACT_CONSTITUTION.search(text):
        acts.append("constituição")
    if says(_ACT_AMENDMENT):
        acts.append("alteração")
    if says(_ACT_CONSOLIDATION):
        acts.append("consolidação")
    if len(acts) < 2:
        return "".join(acts)
    return ", ".join(acts[:-1]) + " e " + acts[-1]


def _standalone_line(name: str, text: str, groups: list[_Group]) -> str:
    total = sum(len(group.sheets) for group in groups)
    facts = standalone_facts(text, name)
    if not (facts.code or facts.companies or facts.act or facts.dates):
        return f"Sem carimbo nem marcador de evento: documento avulso, folhas 1–{total}."
    code = f" {facts.code}" if facts.code else ""
    parts = [f"Sem carimbo nem marcador de evento: documento avulso{code}, folhas 1–{total}"]
    if facts.companies or facts.act or facts.dates:
        # Contrato com parte dos dados: o que faltou vai como "—" (nao e "nao se aplica").
        parts.append("empresa " + (" / ".join(facts.companies) if facts.companies else "—"))
        parts.append("ato " + (facts.act or "—"))
        if facts.issued:
            parts.append(f"emitida em {facts.issued}")
        else:
            parts.append("registro na Junta Comercial " + ("; ".join(facts.dates) if facts.dates else "—"))
    return " · ".join(parts) + "."


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

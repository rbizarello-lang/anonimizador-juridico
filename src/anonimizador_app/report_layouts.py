"""Relatorios com rotulo e valor: extrato SISBAJUD, lista do RENAJUD e capa do processo.

Fila 3.x E12 (defeito D4). Na camada de texto desses relatorios os rotulos vem todos juntos e os
valores depois ("Numero do processo:" ... "Juiz solicitante do bloqueio:" ... e so entao os
numeros): as duas cifras de uma ordem de bloqueio (valor da ordem e valor bloqueado) ficavam sem
rotulo, e a tabela de respostas de cada banco saia em uma coluna so. Aqui rotulo e valor se
pareiam pela posicao na folha, como o E2 (separacao do eproc) e o E9 (quadro da CDA) ja faziam.

So atua em layout reconhecido pelos rotulos fixos dele (``_kinds``); folha de outro tipo nao e
tocada. O plano devolvido so vale se nenhuma palavra da folha se perder, se repetir ou mudar de
numero (conferencia exata do multiconjunto de palavras); se a conferencia falhar, a folha sai como
antes (``None``).

Duas operacoes, ambas por geometria (``page.get_text("dict")``):

- **Pares rotulo/valor** ("Numero do processo: 5000..."): o rotulo e a celula que termina em dois
  pontos ou interrogacao (celula = linhas vizinhas alinhadas, porque o rotulo e o valor tambem
  quebram em duas linhas); o valor e a primeira celula a direita dele, na mesma altura. Cada par
  vira uma linha, no lugar do rotulo.
- **Tabelas de cabecalho fixo** (respostas das instituicoes no SISBAJUD, "Reu/Executado | Total
  bloqueado", lista de veiculos do RENAJUD): o cabecalho e reconhecido pelo texto exato das
  colunas; cada linha de dados cai na coluna mais proxima e vira uma linha de tabela Markdown. O
  nome da instituicao, que fica acima do cabecalho, vai antes da tabela. Cabecalho sem linha de
  dados (a tabela continua na folha seguinte) fica como estava.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

_WORD = re.compile(r"\w+")
_END_OF_LABEL = (":", "?")
# Rotulos sem dois-pontos, por layout: a capa ("Competencia" / "Situacao" ao lado do valor) e o
# detalhamento de ordens do SISBAJUD ("Total bloqueado  16.909,04"). Fora do layout, "Situação"
# e cabecalho de coluna.
_EXTRA_LABELS = {
    "capa": frozenset({"Competência", "Situação"}),
    "sisbajud": frozenset({"Situação da Ordem", "Total bloqueado", "Valor a bloquear"}),
}
_DATE_ROW = re.compile(r"^\d{2}\s+[A-Za-zÇç]{3}\.?\s+\d{4}$")
# Fileira de resposta: data do protocolo ou, na fileira de desbloqueio, so um traco na 1a coluna.
_ANSWER_ROW = re.compile(r"^(?:\d{2}\s+[A-Za-zÇç]{3}\.?\s+\d{4}|-)$")
_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}$")
_DEBTOR_ROW = re.compile(r"^\d[\d.\-/]{7,17}\s*:")
_PLATE_ROW = re.compile(r"^[A-Za-z]{3}\d[A-Za-z0-9]\d{2}$")

# Celula: linha seguinte alinhada (esquerda ou direita) e a ate 1,3 altura de linha da anterior.
_CELL_GAP = 1.3
_ALIGN_TOLERANCE = 2.5
# Valor: a direita do rotulo, a no maximo isto, com 40% de sobreposicao vertical.
_VALUE_MAX_GAP = 260.0
_ROW_OVERLAP = 0.4
# Tabela: vao maximo entre linhas de dados e margem de rodape desprezada.
_TABLE_MAX_GAP = 25.0
_FOOTER_MARGIN = 45.0
# Nome da instituicao: a 25 a 45 pt acima do topo do cabecalho (distancia fixa do modelo do SISBAJUD).
_NAME_RANGE = (45.0, 25.0)


@dataclass(frozen=True, slots=True)
class PageLine:
    """Linha de texto da folha; ``index`` conta todas as linhas de blocos de texto, na ordem do PDF."""

    index: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def clean(self) -> str:
        return " ".join(self.text.split())


def page_lines(page: object) -> list[PageLine]:
    """Todas as linhas de texto (inclusive as vazias) com o indice que ``_text_with_tables`` usa."""

    lines: list[PageLine] = []
    index = 0
    for block in page.get_text("dict").get("blocks", ()):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            text = "".join(span.get("text", "") for span in line.get("spans", ()))
            x0, y0, x1, y1 = line["bbox"]
            lines.append(PageLine(index, x0, y0, x1, y1, text))
            index += 1
    return lines


@dataclass(slots=True)
class ReportPlan:
    """O que mudar na folha: linhas trocadas por texto novo e linhas que ja entraram em outra."""

    replaced: dict[int, str] = field(default_factory=dict)
    removed: set[int] = field(default_factory=set)
    tables: int = 0
    pairs: int = 0


@dataclass(frozen=True, slots=True)
class _TableSpec:
    anchor: str
    vocabulary: frozenset[str]
    headers: tuple[str, ...]
    align: str  # "center": cabecalho e dados centrados; "left": alinhados a esquerda
    row_start: re.Pattern | None
    row_mode: str  # "top": a linha pertence a ultima ancora acima; "nearest": a mais proxima
    window: tuple[float, float]
    named: bool = False
    skip: frozenset[str] = frozenset()
    max_gap: float = _TABLE_MAX_GAP
    lead_index: bool = False  # numero de ordem solto a esquerda da 1a coluna, sem cabecalho


_RESPOSTAS = _TableSpec(
    anchor="Tipo de ordem",
    vocabulary=frozenset(
        {
            "Data/hora", "protocolo", "Tipo de ordem", "Juiz solicitante", "Valor", "Resultado",
            "Saldo bloqueado", "remanescente", "resultado",
        }
    ),
    headers=(
        "Data/hora protocolo", "Tipo de ordem", "Juiz solicitante", "Valor", "Resultado",
        "Saldo bloqueado remanescente", "Data/hora resultado",
    ),
    align="center",
    row_start=_ANSWER_ROW,
    row_mode="top",
    window=(-12.0, 2.0),
    named=True,
    skip=frozenset({"Respostas"}),
    # Entre a fileira da resposta e a de desbloqueio ha ate ~50 pt de vazio.
    max_gap=70.0,
)
_ORDENS = _TableSpec(
    anchor="Nr. Protocolo",
    vocabulary=frozenset(
        {
            "Data", "Protocolam", "Situação", "Valor a", "bloquear", "Nr. Protocolo", "Processo",
            "Juiz/Assessor",
        }
    ),
    headers=("Data Protocolam", "Situação", "Valor a bloquear", "Nr. Protocolo", "Processo", "Juiz/Assessor"),
    align="center",
    row_start=_DATE_ROW,
    row_mode="nearest",
    window=(-8.0, 8.0),
    max_gap=60.0,
    lead_index=True,
)
_REUS = _TableSpec(
    anchor="Réu/Executado",
    vocabulary=frozenset({"Réu/Executado", "Total bloqueado pelo bloqueio original e reiterações"}),
    headers=("Réu/Executado", "Total bloqueado pelo bloqueio original e reiterações"),
    align="left",
    row_start=_DEBTOR_ROW,
    row_mode="top",
    window=(-3.0, 3.0),
)
_VEICULOS = _TableSpec(
    anchor="Placa Anterior",
    vocabulary=frozenset(
        {
            "Placa", "Placa Anterior", "UF", "Marca/Modelo", "Ano Fabricação", "Ano Modelo",
            "Proprietário", "Restrições Existentes", "Ações",
        }
    ),
    headers=(
        "Placa", "Placa Anterior", "UF", "Marca/Modelo", "Ano Fabricação", "Ano Modelo",
        "Proprietário", "Restrições Existentes", "Ações",
    ),
    align="left",
    row_start=_PLATE_ROW,
    row_mode="nearest",
    window=(-3.0, 3.0),
)


def _kinds(native: str) -> frozenset[str]:
    kinds: set[str] = set()
    if "Juiz solicitante do bloqueio:" in native and "Número do protocolo:" in native:
        kinds.add("sisbajud")
    if "Saldo bloqueado" in native and "Tipo de ordem" in native and "Juiz solicitante" in native:
        kinds.add("sisbajud")
    if "Lista de Veículos" in native and "Placa Anterior" in native and "Restrições Existentes" in native:
        kinds.add("renajud")
    # Capa do processo: a de 1o grau traz "Chave Processo:"; a do agravo (2o grau) nao, mas traz
    # "Partes e Representantes" (sem isto a capa do agravo perdia as tabelas de grade, E13).
    if "Informações Adicionais" in native and ("Chave Processo:" in native or "Partes e Representantes" in native):
        kinds.add("capa")
    return frozenset(kinds)


def report_plan(page: object, native: str, *, exclude: Iterable[object] = ()) -> ReportPlan | None:
    """Plano de pareamento da folha, ou ``None`` se ela nao e um relatorio reconhecido,
    nada se pareia ou a conferencia de palavras falha. ``exclude``: retangulos (tabelas de grade)
    que ficam fora do plano."""

    kinds = _kinds(native)
    if not kinds:
        return None
    every = page_lines(page)
    boxes = list(exclude)
    lines = [line for line in every if line.clean and not _inside(line, boxes)]
    height = _page_height(page, every)
    plan = ReportPlan()
    free = {line.index for line in lines}
    if "sisbajud" in kinds:
        for spec in (_RESPOSTAS, _ORDENS, _REUS):
            _tables(spec, lines, free, plan, height)
    if "renajud" in kinds:
        _tables(_VEICULOS, lines, free, plan, height)
    if kinds & {"sisbajud", "capa"}:
        extras = frozenset().union(*(_EXTRA_LABELS[kind] for kind in kinds if kind in _EXTRA_LABELS))
        _pairs(lines, free, plan, extras)
    if not plan.replaced:
        return None
    return plan if _keeps_every_word(every, plan) else None


def render_lines(lines: list[PageLine], plan: ReportPlan) -> str:
    """Texto da folha com o plano aplicado (so para conferir; o extrator usa o dele)."""

    return "\n".join(
        plan.replaced[line.index] if line.index in plan.replaced else line.text
        for line in lines
        if line.index not in plan.removed
    )


def _keeps_every_word(lines: list[PageLine], plan: ReportPlan) -> bool:
    before = Counter(_WORD.findall("\n".join(line.text for line in lines).casefold()))
    after = Counter(_WORD.findall(render_lines(lines, plan).casefold()))
    return before == after


def _inside(line: PageLine, boxes: list[object]) -> bool:
    x = (line.x0 + line.x1) / 2
    y = (line.y0 + line.y1) / 2
    return any(box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1 for box in boxes)


def _page_height(page: object, lines: list[PageLine]) -> float:
    try:
        return float(page.rect.height)
    except Exception:  # noqa: BLE001 - sem retangulo, so as linhas
        return max((line.y1 for line in lines), default=0.0) + _FOOTER_MARGIN


# ---------------------------------------------------------------------------
# Pares rotulo/valor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Cell:
    lines: list[PageLine]

    @property
    def text(self) -> str:
        return " ".join(line.clean for line in self.lines)

    @property
    def x0(self) -> float:
        return min(line.x0 for line in self.lines)

    @property
    def x1(self) -> float:
        return max(line.x1 for line in self.lines)

    @property
    def y0(self) -> float:
        return min(line.y0 for line in self.lines)

    @property
    def y1(self) -> float:
        return max(line.y1 for line in self.lines)

    @property
    def closed(self) -> bool:
        return self.text.endswith(_END_OF_LABEL)


def _is_label(cell: _Cell, extras: frozenset[str]) -> bool:
    text = cell.text
    return len(text) <= 70 and (text.endswith(_END_OF_LABEL) or text in extras)


def _cells(lines: list[PageLine], extras: frozenset[str]) -> list[_Cell]:
    """Linhas vizinhas e alinhadas formam uma celula, ate o rotulo fechar com dois-pontos.

    "Nivel de Sigilo do / Processo:" (rotulo alinhado a direita) e "Sem Sigilo (Nivel / 0)" (valor)
    quebram em duas linhas; so a celula nao fechada recebe a linha de baixo.
    """

    cells: list[_Cell] = []
    open_cells: list[_Cell] = []
    for line in sorted(lines, key=lambda item: (item.y0, item.x0)):
        # Linhas ordenadas por altura: celula longe demais nao recebe mais nada.
        open_cells = [
            cell
            for cell in open_cells
            if line.y0 - cell.lines[-1].y0 <= _CELL_GAP * max(1.0, cell.lines[-1].y1 - cell.lines[-1].y0)
        ]
        joined = None
        for cell in open_cells:
            last = cell.lines[-1]
            if line.y0 - last.y0 <= 0:
                continue
            right = abs(line.x1 - last.x1) <= _ALIGN_TOLERANCE
            left = abs(line.x0 - last.x0) <= _ALIGN_TOLERANCE
            # "Processo:" fecha o rotulo "Nivel de Sigilo do" alinhado a direita; um rotulo novo
            # alinhado so a esquerda ("Classe da acao:" sob "No do processo ...") abre outra celula.
            if line.clean in extras:
                continue
            if right or (left and not line.clean.endswith(_END_OF_LABEL)):
                joined = cell
                break
        if joined is not None:
            joined.lines.append(line)
            if joined.closed:
                open_cells.remove(joined)
            continue
        cell = _Cell([line])
        cells.append(cell)
        if not cell.closed and cell.text not in extras:
            open_cells.append(cell)
    return _split_stacked_values(cells, extras)


def _split_stacked_values(cells: list[_Cell], extras: frozenset[str]) -> list[_Cell]:
    """Valores empilhados, cada um com o rotulo dele a esquerda, nao formam uma celula so.

    "Sem Sigilo (Nivel / 0)" continua um valor (os dois lados tem o mesmo rotulo de duas linhas);
    "Execucao Fiscal" sobre "Civel" nao: o segundo tem outro rotulo a esquerda, na mesma altura.
    """

    labels = [cell for cell in cells if _is_label(cell, extras)]

    def owner(line: PageLine) -> _Cell | None:
        near = [
            cell
            for cell in labels
            if cell.x1 <= line.x0 + _ALIGN_TOLERANCE and cell.y0 - 3 <= line.y0 <= cell.y1 + 1
        ]
        return min(near, key=lambda cell: (abs(cell.y0 - line.y0), -cell.x1)) if near else None

    result: list[_Cell] = []
    for cell in cells:
        if len(cell.lines) == 1 or _is_label(cell, extras):
            result.append(cell)
            continue
        current = [cell.lines[0]]
        current_owner = owner(cell.lines[0])
        for line in cell.lines[1:]:
            line_owner = owner(line)
            if line_owner is not None and line_owner is not current_owner:
                result.append(_Cell(current))
                current, current_owner = [line], line_owner
            else:
                current.append(line)
        result.append(_Cell(current))
    return result


def _overlap(first: _Cell, second: _Cell) -> bool:
    top, bottom = max(first.y0, second.y0), min(first.y1, second.y1)
    smaller = min(first.y1 - first.y0, second.y1 - second.y0)
    return smaller > 0 and (bottom - top) >= _ROW_OVERLAP * smaller


def _pairs(lines: list[PageLine], free: set[int], plan: ReportPlan, extras: frozenset[str]) -> None:
    cells = _cells([line for line in lines if line.index in free], extras)
    claimed: set[int] = set()
    for label in cells:
        if not _is_label(label, extras) or id(label) in claimed:
            continue
        right = [
            cell
            for cell in cells
            if cell is not label
            and cell.x0 >= label.x1 - _ALIGN_TOLERANCE
            and cell.x0 - label.x1 <= _VALUE_MAX_GAP
            and _overlap(label, cell)
        ]
        if not right:
            continue
        value = min(right, key=lambda cell: cell.x0)
        if _is_label(value, extras) or id(value) in claimed:
            continue
        claimed.update((id(label), id(value)))
        text = label.text if label.text.endswith(_END_OF_LABEL) else label.text + ":"
        first = label.lines[0]
        plan.replaced[first.index] = f"{text} {value.text}"
        plan.removed.update(line.index for line in label.lines[1:])
        plan.removed.update(line.index for line in value.lines)
        free.difference_update(line.index for line in label.lines)
        free.difference_update(line.index for line in value.lines)
        plan.pairs += 1


# ---------------------------------------------------------------------------
# Tabelas de cabecalho fixo
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Column:
    lines: list[PageLine]

    @property
    def header(self) -> str:
        return " ".join(line.clean for line in sorted(self.lines, key=lambda item: (item.y0, item.x0)))

    @property
    def x0(self) -> float:
        return min(line.x0 for line in self.lines)

    @property
    def center(self) -> float:
        return sum((line.x0 + line.x1) / 2 for line in self.lines) / len(self.lines)


def _column_key(line: PageLine, align: str) -> float:
    return (line.x0 + line.x1) / 2 if align == "center" else line.x0


def _band(spec: _TableSpec, anchor: PageLine, lines: list[PageLine], free: set[int]) -> list[_Column] | None:
    members = [
        line
        for line in lines
        if line.index in free
        and line.clean in spec.vocabulary
        and anchor.y0 + spec.window[0] <= line.y0 <= anchor.y0 + spec.window[1]
    ]
    tolerance = 6.0 if spec.align == "center" else 4.0
    columns: list[_Column] = []
    for line in sorted(members, key=lambda item: _column_key(item, spec.align)):
        for column in columns:
            key = column.center if spec.align == "center" else column.x0
            if abs(_column_key(line, spec.align) - key) <= tolerance:
                column.lines.append(line)
                break
        else:
            columns.append(_Column([line]))
    columns.sort(key=lambda column: column.center if spec.align == "center" else column.x0)
    if tuple(column.header for column in columns) != spec.headers:
        return None
    return columns


def _nearest_column(line: PageLine, columns: list[_Column], align: str) -> int | None:
    if align == "center":
        key, tolerance = _column_key(line, align), 8.0
        distances = [abs(key - column.center) for column in columns]
    else:
        key, tolerance = line.x0, 5.0
        distances = [abs(key - column.x0) for column in columns]
    best = min(range(len(columns)), key=distances.__getitem__)
    return best if distances[best] <= tolerance else None


def _tables(spec: _TableSpec, lines: list[PageLine], free: set[int], plan: ReportPlan, height: float) -> None:
    anchors = sorted(
        (line for line in lines if line.index in free and line.clean == spec.anchor),
        key=lambda item: item.y0,
    )
    bands: list[tuple[PageLine, list[_Column]]] = []
    for anchor in anchors:
        columns = _band(spec, anchor, lines, free)
        if columns is not None:
            bands.append((anchor, columns))
    # Topo de cada cabecalho e nome da instituicao acima dele (limita a tabela de cima).
    tops = [min(line.y0 for column in columns for line in column.lines) for _a, columns in bands]
    names = [
        _name_above(spec, columns, top, lines, free) if spec.named else []
        for (_anchor, columns), top in zip(bands, tops)
    ]
    for position, (_anchor, columns) in enumerate(bands):
        limit = float("inf")
        if position + 1 < len(bands):
            nxt = names[position + 1]
            limit = min(nxt[0].y0 if nxt else tops[position + 1], tops[position + 1])
        built = _build_table(spec, columns, lines, free, limit, height)
        if built is None:
            continue
        rendered, used = built
        name_lines = names[position]
        band_lines = [line for column in columns for line in column.lines]
        everything = [*name_lines, *band_lines, *used]
        anchor_index = min(line.index for line in everything)
        head = " ".join(line.clean for line in name_lines)
        plan.replaced[anchor_index] = (f"{head}\n\n" if head else "\n") + rendered + "\n"
        plan.removed.update(line.index for line in everything if line.index != anchor_index)
        free.difference_update(line.index for line in everything)
        plan.tables += 1


def _name_above(
    spec: _TableSpec, columns: list[_Column], top: float, lines: list[PageLine], free: set[int]
) -> list[PageLine]:
    """Nome da instituicao: linha a esquerda da 1a coluna, logo acima do cabecalho."""

    left = columns[0].x0
    candidates = sorted(
        (
            line
            for line in lines
            if line.index in free
            and top - _NAME_RANGE[0] <= line.y0 <= top - _NAME_RANGE[1]
            and line.x0 <= left - 8
            and line.clean not in spec.skip
            and line.clean not in spec.vocabulary
            and not _DATE_ROW.match(line.clean)
            and not _TIME_ONLY.match(line.clean)
        ),
        key=lambda item: -item.y0,
    )
    if not candidates:
        return []
    name = [candidates[0]]
    for line in candidates[1:]:
        if name[-1].y0 - line.y0 <= 12 and abs(line.x0 - name[0].x0) <= 2:
            name.append(line)
        else:
            break
    return sorted(name, key=lambda item: item.y0)


def _build_table(
    spec: _TableSpec,
    columns: list[_Column],
    lines: list[PageLine],
    free: set[int],
    limit: float,
    height: float,
) -> tuple[str, list[PageLine]] | None:
    band_bottom = max(line.y0 for column in columns for line in column.lines)
    candidates = sorted(
        (
            line
            for line in lines
            if line.index in free
            and band_bottom + 2 < line.y0 < limit
            and line.y0 < height - _FOOTER_MARGIN
            and line.clean not in spec.skip
        ),
        key=lambda item: (item.y0, item.x0),
    )
    data: list[tuple[int, PageLine]] = []
    previous = band_bottom
    for line in candidates:
        if line.y0 - previous > spec.max_gap:
            break
        if spec.lead_index and line.clean.isdigit() and line.x1 < columns[0].center - 25:
            column: int | None = -1
        else:
            column = _nearest_column(line, columns, spec.align)
        if column is None:
            break
        data.append((column, line))
        previous = max(previous, line.y0)
    if not data:
        return None
    anchors = (
        [line for column, line in data if column == 0 and spec.row_start.match(line.clean)]
        if spec.row_start is not None
        else []
    )
    if not anchors:
        return None
    lead = 1 if spec.lead_index else 0
    rows: list[list[list[PageLine]]] = [[[] for _ in range(len(columns) + lead)] for _ in anchors]
    for column, line in data:
        if spec.row_mode == "top":
            owners = [index for index, anchor in enumerate(anchors) if anchor.y0 <= line.y0 + 2]
            if not owners:
                return None
            row = owners[-1]
        else:
            row = min(range(len(anchors)), key=lambda index: abs(anchors[index].y0 - line.y0))
        rows[row][column + lead].append(line)
    names = [""] * lead + [column.header for column in columns]
    rendered = ["| " + " | ".join(_cell(name) for name in names) + " |"]
    rendered.append("|" + "---|" * len(names))
    for row in rows:
        rendered.append(
            "| "
            + " | ".join(
                _cell(" ".join(line.clean for line in sorted(cell, key=lambda item: (item.y0, item.x0))))
                for cell in row
            )
            + " |"
        )
    return "\n".join(rendered), [line for _column, line in data]


def _cell(text: str) -> str:
    return text.replace("|", "/")

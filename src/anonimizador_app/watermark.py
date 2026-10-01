"""Selo de editor de PDF fora do texto, das tabelas e do OCR (fila 3.x E10).

O PDF-XChange Editor sem licenca carimba cada folha que salva com dois selos nos cantos de
cima: um disco dourado e preto com "Click to BUY NOW!" em diagonal no meio e, em volta,
"PDF-XChange Editor" e "www.tracker-software.com" escritos letra a letra em circulo (fonte
~5 pt, cor dourada). Na camada de texto isso vira dezenas de linhas de uma letra por folha;
na folha com tabela, as letras sobrepostas acionavam a leitura "por camada" e viravam tabelas
falsas ("| h | n a g | e E d i t o r |"); na folha escaneada o OCR lia o selo junto com o texto.

O selo so e reconhecido a partir do texto "Click to BUY NOW!" (diagonal, letra pequena): os
spans dele sao os da mesma cor e letra pequena em volta. Folha sem esse texto passa intacta
(o mesmo objeto de pagina). Na folha com o selo, ``without_watermark`` devolve a pagina vista
sem ele:

- ``get_text`` ("text", "dict", "words") sai sem os spans do selo; nenhum outro span some,
  mesmo o que ficar encostado nele;
- ``find_tables`` le as celulas sem as letras do selo;
- ``get_pixmap`` sai com o disco do selo em branco (ele ja cobre, opaco, o que estava
  embaixo; fora dele nada muda). Sem o disco achado, so as letras do selo saem em branco.

O resto e da pagina original.
"""

from __future__ import annotations

import math

WATERMARK_ANCHORS = ("Click to BUY NOW!",)
# As letras do circulo ficam a ~19 pt da caixa do texto diagonal: ate esta margem fora dela.
_BADGE_MARGIN = 24.0
_BADGE_MAX_SIZE = 6.5
# O disco do selo e um desenho cheio, centrado no texto diagonal, maior que ele.
_SEAL_CENTER_TOLERANCE = 3.0
_SEAL_MIN_RATIO = 1.4
_SEAL_MAX_RATIO = 4.0
_SEAL_MARGIN = 1.0


def _span_key(span: dict) -> tuple:
    return (tuple(round(value, 3) for value in span["bbox"]), span.get("color"))


class _Badge:
    """O que o selo ocupa numa folha: spans, caixas das letras e discos."""

    def __init__(self, keys: set, boxes: list, discs: list) -> None:
        self.keys = keys
        self.boxes = boxes
        self.discs = discs

    def is_mark(self, span: dict) -> bool:
        return _span_key(span) in self.keys


def _badge(page: object) -> _Badge | None:
    """Os spans do selo da folha, ou ``None`` quando ela nao tem o selo."""

    try:
        if not any(anchor in page.get_text("text") for anchor in WATERMARK_ANCHORS):
            return None
        blocks = page.get_text("dict").get("blocks", ())
    except Exception:  # noqa: BLE001 - sem texto nao ha selo a tirar
        return None
    spans = [
        span
        for block in blocks
        if block.get("type") == 0
        for line in block.get("lines", ())
        for span in line.get("spans", ())
    ]
    anchors = [
        span
        for span in spans
        if span.get("text", "").strip() in WATERMARK_ANCHORS and span.get("size", 99) <= _BADGE_MAX_SIZE
    ]
    keys: set = set()
    boxes: list = []
    discs: list = []
    for anchor in anchors:
        ax0, ay0, ax1, ay1 = anchor["bbox"]
        region = (ax0 - _BADGE_MARGIN, ay0 - _BADGE_MARGIN, ax1 + _BADGE_MARGIN, ay1 + _BADGE_MARGIN)
        for span in spans:
            x0, y0, x1, y1 = span["bbox"]
            if (
                span.get("color") == anchor.get("color")
                and span.get("size", 99) <= _BADGE_MAX_SIZE
                and region[0] <= x0 and region[1] <= y0 and x1 <= region[2] and y1 <= region[3]
                and _span_key(span) not in keys
            ):
                keys.add(_span_key(span))
                boxes.append((x0, y0, x1, y1))
        disc = _seal_disc(page, anchor["bbox"])
        if disc is not None:
            discs.append(disc)
    return _Badge(keys, boxes, discs) if keys else None


def _seal_disc(page: object, anchor_box: tuple) -> tuple[float, float, float] | None:
    """Centro e raio do disco do selo: o maior desenho cheio centrado no texto diagonal."""

    try:
        drawings = page.get_cdrawings()
    except Exception:  # noqa: BLE001
        return None
    ax0, ay0, ax1, ay1 = anchor_box
    cx, cy = (ax0 + ax1) / 2, (ay0 + ay1) / 2
    anchor_size = max(ax1 - ax0, ay1 - ay0)
    best: tuple | None = None
    for drawing in drawings:
        if drawing.get("fill") is None:
            continue
        x0, y0, x1, y1 = drawing["rect"]
        width, height = x1 - x0, y1 - y0
        size = max(width, height)
        if (
            abs((x0 + x1) / 2 - cx) <= _SEAL_CENTER_TOLERANCE
            and abs((y0 + y1) / 2 - cy) <= _SEAL_CENTER_TOLERANCE
            and abs(width - height) <= 0.15 * size
            and _SEAL_MIN_RATIO * anchor_size <= size <= _SEAL_MAX_RATIO * anchor_size
            and (best is None or size > best[0])
        ):
            best = (size, x0, y0, x1, y1)
    if best is None:
        return None
    size = best[0]
    return ((best[1] + best[3]) / 2, (best[2] + best[4]) / 2, size / 2 + _SEAL_MARGIN)


def watermark_boxes(page: object) -> list[tuple[float, float, float, float]]:
    """Caixas dos spans do selo na folha (vazia quando a folha nao tem o selo)."""

    badge = _badge(page)
    return list(badge.boxes) if badge else []


def without_watermark(page: object) -> object:
    """A propria pagina, ou a pagina vista sem o selo do editor."""

    badge = _badge(page)
    return _PageWithoutWatermark(page, badge) if badge else page


def _inside(box: tuple, x: float, y: float) -> bool:
    return box[0] - 0.5 <= x <= box[2] + 0.5 and box[1] - 0.5 <= y <= box[3] + 0.5


class _PageWithoutWatermark:
    """Pagina do PyMuPDF sem o selo em ``get_text`` ("text", "dict", "words"), ``find_tables``
    e ``get_pixmap``. O resto e da pagina original."""

    def __init__(self, page: object, badge: _Badge) -> None:
        self._page = page
        self._badge = badge
        self._dict: dict | None = None
        self._marks: dict[str, list[tuple]] | None = None
        self._words: list | None = None

    def __getattr__(self, name: str):
        return getattr(self._page, name)

    def _clean_dict(self) -> dict:
        if self._dict is None:
            raw = self._page.get_text("dict")
            blocks = []
            for block in raw.get("blocks", ()):
                if block.get("type") != 0:
                    blocks.append(block)
                    continue
                lines = []
                for line in block.get("lines", ()):
                    spans = [span for span in line.get("spans", ()) if not self._badge.is_mark(span)]
                    if spans:
                        lines.append({**line, "spans": spans})
                if lines:
                    blocks.append({**block, "lines": lines})
            self._dict = {**raw, "blocks": blocks}
        return self._dict

    def get_text(self, option: str = "text", *args, **kwargs):
        if args or kwargs or option not in {"text", "dict", "words"}:
            return self._page.get_text(option, *args, **kwargs)
        if option == "dict":
            return self._clean_dict()
        if option == "text":
            return "".join(
                "".join(span["text"] for span in line["spans"]) + "\n"
                for block in self._clean_dict()["blocks"]
                if block.get("type") == 0
                for line in block["lines"]
            )
        if self._words is None:
            self._words = self._clean_words(self._page.get_text("words"))
        return list(self._words)

    def _clean_words(self, words: list) -> list:
        """As palavras da pagina menos as do selo.

        Palavra do selo e a que e feita so de letras dele: cada letra tem uma letra igual do
        selo com a caixa dentro da caixa da palavra, e as caixas dessas letras, juntas, cobrem
        a da palavra. Palavra de verdade que so passa por baixo do selo nao cumpre as duas
        coisas; nenhuma sai por estar perto dele.
        """

        marks = self._mark_chars()
        if not marks:
            return list(words)
        clean = []
        for word in words:
            if not _is_mark_word(word, marks):
                clean.append(word)
        return clean

    def find_tables(self, *args, **kwargs):
        """Tabelas da pagina; as letras do selo saem das celulas e dos nomes de cabecalho."""

        found = self._page.find_tables(*args, **kwargs)
        marks = self._mark_chars()
        for table in getattr(found, "tables", ()):
            chars = getattr(table, "_chars", None)
            if chars and marks:
                # Lista compartilhada pelas tabelas da chamada: filtrada no lugar.
                chars[:] = [char for char in chars if not _is_mark_char(char, marks)]
            header = getattr(table, "header", None)
            if header is not None and getattr(header, "external", False):
                self._clean_header(header)
        return found

    def _mark_chars(self) -> dict[str, list[tuple]]:
        """Caixa de cada letra do selo, por letra (a leitura de tabela mede a altura do glifo
        a seu modo: a letra do selo se acha pelo texto e pelo centro dentro da caixa)."""

        if self._marks is not None:
            return self._marks
        try:
            raw = self._page.get_text("rawdict")
        except Exception:  # noqa: BLE001
            return {}
        marks: dict[str, list[tuple]] = {}
        for block in raw.get("blocks", ()):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", ()):
                for span in line.get("spans", ()):
                    if self._badge.is_mark(span):
                        for char in span.get("chars", ()):
                            marks.setdefault(char["c"], []).append(tuple(char["bbox"]))
        self._marks = marks
        return marks

    def _clean_header(self, header: object) -> None:
        """Nome de coluna lido pela pagina original: refaz so o que encosta no selo."""

        names = getattr(header, "names", None)
        cells = getattr(header, "cells", None)
        if not names or not cells or len(names) != len(cells):
            return
        words = self.get_text("words")
        for index, cell in enumerate(cells):
            if cell is None or not any(
                cell[0] < box[2] and box[0] < cell[2] and cell[1] < box[3] and box[1] < cell[3]
                for box in self._badge.boxes
            ):
                continue
            inside = [w for w in words if cell[0] <= (w[0] + w[2]) / 2 < cell[2] and cell[1] <= (w[1] + w[3]) / 2 < cell[3]]
            inside.sort(key=lambda w: (round((w[1] + w[3]) / 2), w[0]))
            names[index] = " ".join(w[4] for w in inside)

    def get_pixmap(self, *args, **kwargs):
        import pymupdf

        pix = self._page.get_pixmap(*args, **kwargs)
        matrix = args[0] if args else kwargs.get("matrix")
        if matrix is None:
            zoom = (kwargs.get("dpi") or 72) / 72
            matrix = pymupdf.Matrix(zoom, zoom)
        white = (255,) * pix.n
        bounds = pymupdf.IRect(pix.irect)
        if self._badge.discs:
            for cx, cy, radius in self._badge.discs:
                _fill_disc(pix, matrix, cx, cy, radius, white, bounds)
        else:
            for box in self._badge.boxes:
                area = (pymupdf.Rect(box) * matrix).irect & bounds
                if not area.is_empty:
                    pix.set_rect(area, white)
        return pix


_WORD_TOLERANCE = 1.0


def _is_mark_word(word: tuple, marks: dict[str, list[tuple]]) -> bool:
    text = word[4]
    if not text or any(char not in marks for char in text):
        return False
    x0, y0, x1, y1 = word[:4]
    found: list[tuple] = []
    for char in text:
        match = next(
            (
                box
                for box in marks[char]
                if box not in found
                and x0 - _WORD_TOLERANCE <= box[0] and box[2] <= x1 + _WORD_TOLERANCE
                and y0 - _WORD_TOLERANCE <= box[1] and box[3] <= y1 + _WORD_TOLERANCE
            ),
            None,
        )
        if match is None:
            return False
        found.append(match)
    return (
        abs(min(box[0] for box in found) - x0) <= _WORD_TOLERANCE
        and abs(min(box[1] for box in found) - y0) <= _WORD_TOLERANCE
        and abs(max(box[2] for box in found) - x1) <= _WORD_TOLERANCE
        and abs(max(box[3] for box in found) - y1) <= _WORD_TOLERANCE
    )


def _is_mark_char(char: dict, marks: dict[str, list[tuple]]) -> bool:
    boxes = marks.get(char["text"])
    if not boxes:
        return False
    x, y = (char["x0"] + char["x1"]) / 2, (char["top"] + char["bottom"]) / 2
    return any(_inside(box, x, y) for box in boxes)


def _fill_disc(pix, matrix, cx: float, cy: float, radius: float, white: tuple, bounds) -> None:
    """Disco em branco, linha de pixels por linha (o disco no espaco da folha, vezes a matriz)."""

    import pymupdf

    centre = pymupdf.Point(cx, cy) * matrix
    rx = radius * math.hypot(matrix.a, matrix.b)
    ry = radius * math.hypot(matrix.c, matrix.d)
    top = max(int(math.floor(centre.y - ry)), bounds.y0)
    bottom = min(int(math.ceil(centre.y + ry)), bounds.y1)
    for y in range(top, bottom):
        dy = (y + 0.5 - centre.y) / ry
        if abs(dy) >= 1:
            continue
        half = rx * math.sqrt(1 - dy * dy)
        x0 = max(int(math.floor(centre.x - half)), bounds.x0)
        x1 = min(int(math.ceil(centre.x + half)), bounds.x1)
        if x1 > x0:
            pix.set_rect(pymupdf.IRect(x0, y, x1, y + 1), white)

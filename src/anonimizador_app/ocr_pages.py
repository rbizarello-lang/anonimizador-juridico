"""OCR so em pagina sem texto legivel, sem reler peca nativa.

Tres casos (medidos no lote eproc de 25/09/2026):

- escaneada: carimbo eproc + imagem cobrindo a folha (contrato digitalizado);
- vetorial: carimbo eproc + texto desenhado como curvas (sem camada de texto e
  sem imagem; centenas de caminhos na pagina);
- fonte quebrada: ha camada de texto, mas a fonte troca digitos e letras por
  simbolos (``ˬ˭/ˬˮ/ˮˬˮ˲`` no lugar de ``01/02/2026``). O texto nativo nao e
  legivel, entao o OCR nao o degrada: substitui. Conta tambem caractere de
  controle (H9-k: acordao cuja camada era so U+0000-U+001F e simbolos soltos).

H9-k, quarto caso: folha **hibrida** (certidao de matricula: cabecalho, carimbo e
rodape nativos, ~600 caracteres; o corpo da certidao e uma imagem de meia folha).
O texto nativo fica, exato, e o OCR da folha entra depois dele.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

MIN_NATIVE_CHARS = 15
STAMP_MAX_CHARS = 300
SCAN_AREA_RATIO = 0.20
# Folha so com carimbo tem ate ~6 caminhos; texto vetorizado tem centenas (>= 314 no lote).
VECTOR_MIN_PATHS = 50
# Proporcao de caracteres improvaveis na camada de texto (0,6% no pior nativo; >= 8,5% nos recibos).
BROKEN_GLYPH_RATIO = 0.02
# Folha hibrida (H9-k): imagem(ns) cobrindo >= 40% da folha e texto nativo curto. A certidao de
# matricula medida tem ~600 caracteres nativos (cabecalho, carimbo, rodape) e o corpo em imagem de ~50%
# da folha. Uma folha corrida de peca tem 2.500-3.500 caracteres nao brancos; meia folha de texto,
# ~1.500. Abaixo de 1.200 o texto nativo nao e o corpo da folha; acima, a imagem e figura ou fundo
# de uma folha que ja se le (logotipo, assinatura, timbre: GOVERNANCA §8.3, sem OCR).
HYBRID_IMAGE_RATIO = 0.40
HYBRID_MAX_CHARS = 1200
# Tinta da folha em cinza: so carimbo <= 0,61%; conteudo mais esparso medido 1,27%.
BLANK_INK_RATIO = 0.009

OCR_SCAN = "escaneada"
OCR_VECTOR = "vetorial"
OCR_BROKEN_FONT = "fonte-quebrada"
OCR_HYBRID = "hibrida"

_OCR_SCALE = 2.0
# Margem da caixa de texto do detector (padrao 1,6 juntava linhas vizinhas).
OCR_UNCLIP_RATIO = 1.3
_INK_SCALE = 0.5
_DARK = bytes(1 if value < 160 else 0 for value in range(256))
# Letras modificadoras, latim estendido-B, uso privado (BMP) e caractere de substituicao. Somam-se,
# fora da regex, controle (Cc, menos tab e quebras) e uso privado fora do BMP (Co) em _improbable.
_BROKEN_GLYPHS = re.compile(r"[ƀ-ɏʰ-˿-�]")
# Controle que e layout, nao glifo: tabulacao, quebra de linha, retorno de carro.
_LAYOUT_CONTROLS = frozenset(map(chr, (9, 10, 13)))
_PAGE_BLOCK = re.compile(
    r"(<!--\s*pagina-origem:\s*(\d+)\s*-->)(.*?)(?=<!--\s*pagina-origem:|\Z)",
    re.DOTALL,
)

_LOG = logging.getLogger(__name__)
_engine = None


@dataclass(frozen=True, slots=True)
class PageOcrResult:
    markdown: str
    pages_read: int


def page_needs_ocr(
    native_chars: int,
    scan_ratio: float,
    *,
    stamp_max: int = STAMP_MAX_CHARS,
    min_ratio: float = SCAN_AREA_RATIO,
) -> bool:
    """Scan que cobre a pagina, com pouco texto nativo (carimbo). Nao e pagina nativa."""

    if scan_ratio < min_ratio:
        return False
    return native_chars < stamp_max


def ocr_reason(
    native_chars: int,
    scan_ratio: float,
    vector_paths: int = 0,
    broken_ratio: float = 0.0,
    image_ratio: float = 0.0,
) -> str | None:
    """Por que a pagina precisa de OCR; ``None`` = texto nativo legivel ou folha so com carimbo.

    ``image_ratio``: area da folha coberta por imagem, recortada a folha (``page_image_ratio``)."""

    if native_chars < STAMP_MAX_CHARS:
        if scan_ratio >= SCAN_AREA_RATIO:
            return OCR_SCAN
        if vector_paths >= VECTOR_MIN_PATHS:
            return OCR_VECTOR
        if broken_ratio > BROKEN_GLYPH_RATIO and native_chars >= MIN_NATIVE_CHARS:
            # Folha curta (fim de acordao) com a camada ilegivel: tambem nao e texto nativo.
            return OCR_BROKEN_FONT
        return None
    if broken_ratio > BROKEN_GLYPH_RATIO:
        return OCR_BROKEN_FONT
    if native_chars < HYBRID_MAX_CHARS and image_ratio >= HYBRID_IMAGE_RATIO:
        return OCR_HYBRID
    return None


def _improbable(char: str) -> bool:
    """Caractere que nao sai de fonte legivel: simbolo trocado, controle, uso privado, substituicao."""

    category = unicodedata.category(char)
    if category == "Cc":
        return char not in _LAYOUT_CONTROLS
    return category == "Co" or _BROKEN_GLYPHS.match(char) is not None


def _compact(text: str) -> str:
    """Sem espaco em branco. Controle nao e espaco aqui: ``str.split`` trata U+001C-U+001F como
    branco, e a camada feita deles sumiria da conta."""

    return "".join(char for char in text if not char.isspace() or _improbable(char))


def broken_glyph_ratio(text: str) -> float:
    compact = _compact(text)
    if not compact:
        return 0.0
    return sum(1 for char in compact if _improbable(char)) / len(compact)


def page_vector_paths(page: object) -> int:
    try:
        return len(page.get_cdrawings())
    except Exception:
        return 0


def page_ocr_reason(page: object, native_text: str) -> str | None:
    """Mede a pagina so no necessario: desenhos apenas em folha sem texto e sem scan."""

    compact = len(_compact(native_text))
    broken = broken_glyph_ratio(native_text)
    if compact < STAMP_MAX_CHARS:
        ratio = page_scan_ratio(page)
        paths = page_vector_paths(page) if ratio < SCAN_AREA_RATIO else 0
        return ocr_reason(compact, ratio, paths, broken)
    if broken > BROKEN_GLYPH_RATIO:
        return OCR_BROKEN_FONT
    image = page_image_ratio(page) if compact < HYBRID_MAX_CHARS else 0.0
    return ocr_reason(compact, 0.0, 0, broken, image)


def page_image_ratio(page: object) -> float:
    """Fracao da folha coberta por imagem: cada caixa recortada a folha, a soma limitada a 1.

    Diferente de ``page_scan_ratio`` (caixa inteira, sem recorte): imagem que transborda a folha
    nao conta a parte de fora."""

    rect = getattr(page, "rect", None)
    if rect is None:
        return 0.0
    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    page_area = (x1 - x0) * (y1 - y0)
    if page_area <= 0:
        return 0.0
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0
    covered = 0.0
    for info in infos:
        bbox = info.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        left, top = max(x0, min(bbox[0], bbox[2])), max(y0, min(bbox[1], bbox[3]))
        right, bottom = min(x1, max(bbox[0], bbox[2])), min(y1, max(bbox[1], bbox[3]))
        if right > left and bottom > top:
            covered += (right - left) * (bottom - top)
    return min(covered / page_area, 1.0)


def page_is_blank(page: object) -> bool:
    """Folha sem tinta alem do carimbo: OCR vazio ali nao e pagina perdida."""

    try:
        import pymupdf

        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(_INK_SCALE, _INK_SCALE),
            colorspace=pymupdf.csGRAY,
            alpha=False,
        )
    except Exception:
        return False
    samples = pix.samples
    if not samples:
        return True
    return sum(samples.translate(_DARK)) / len(samples) < BLANK_INK_RATIO


def recognize_page(page: object) -> str:
    """OCR de uma pagina com o motor local (RapidOCR, reconhecedor latino)."""

    return _recognize_image(_render_page(page), page_number=_page_number(page))


def _page_number(page: object) -> int | None:
    number = getattr(page, "number", None)
    return number + 1 if isinstance(number, int) else None


def recognize_clip(page: object, rect: tuple[float, float, float, float]) -> str:
    """v0.4.12: OCR so da area de uma imagem embutida (recorte colado numa folha de texto)."""

    import pymupdf

    pix = page.get_pixmap(matrix=pymupdf.Matrix(_OCR_SCALE, _OCR_SCALE), clip=pymupdf.Rect(*rect), alpha=False)
    return _recognize_image(pix.tobytes("png"), page_number=_page_number(page))


# v0.4.12, imagens embutidas em folha de texto: a peticao cola recortes de matricula, contrato e telas como imagem. A
# folha tem 700-1.700 caracteres nativos (nao e escaneada) e a imagem cobre 12-39% (nao e hibrida): o recorte nunca ia
# ao OCR. Agora cada imagem que cobre >= 5% da folha e lida, recortada. Logotipo, brasao e assinatura ficam abaixo de 5%.
EMBEDDED_MIN_RATIO = 0.05
# Imagem com a camada de texto por cima (PDF pesquisavel: o scanner ja fez o OCR) nao e relida: o texto nativo dentro da
# caixa passa de 1.000 caracteres por folha inteira de area (uma folha corrida tem 2.500-3.500).
EMBEDDED_TEXT_DENSITY = 1000
# Caixas de imagem que se tocam (a mesma figura cortada em faixas) viram uma so.
_MERGE_GAP = 2.0


def embedded_image_boxes(page: object, *, min_ratio: float = EMBEDDED_MIN_RATIO) -> list[tuple[float, float, float, float]]:
    """Caixas (recortadas a folha, unidas quando se tocam) das imagens que cobrem ``min_ratio`` da folha ou mais e que
    nao tem camada de texto por cima, de cima para baixo."""

    rect = getattr(page, "rect", None)
    if rect is None:
        return []
    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    page_area = (x1 - x0) * (y1 - y0)
    if page_area <= 0:
        return []
    try:
        infos = page.get_image_info()
    except Exception:
        return []
    boxes: list[list[float]] = []
    for info in infos:
        bbox = info.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        left, top = max(x0, min(bbox[0], bbox[2])), max(y0, min(bbox[1], bbox[3]))
        right, bottom = min(x1, max(bbox[0], bbox[2])), min(y1, max(bbox[1], bbox[3]))
        if right > left and bottom > top:
            boxes.append([left, top, right, bottom])
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if (
                    a[0] <= b[2] + _MERGE_GAP and b[0] <= a[2] + _MERGE_GAP
                    and a[1] <= b[3] + _MERGE_GAP and b[1] <= a[3] + _MERGE_GAP
                ):
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    chosen: list[tuple[float, float, float, float]] = []
    words = None
    for box in boxes:
        ratio = (box[2] - box[0]) * (box[3] - box[1]) / page_area
        if ratio < min_ratio or ratio >= 0.98:
            # A folha inteira em imagem e a escaneada (ou a folha pesquisavel): o criterio da folha decide, nao este.
            continue
        if words is None:
            try:
                words = page.get_text("words")
            except Exception:
                words = []
        inside = sum(
            len(str(word[4]))
            for word in words
            if box[0] <= (word[0] + word[2]) / 2 <= box[2] and box[1] <= (word[1] + word[3]) / 2 <= box[3]
        )
        if inside >= EMBEDDED_TEXT_DENSITY * ratio:
            continue
        chosen.append((box[0], box[1], box[2], box[3]))
    return sorted(chosen, key=lambda item: (item[1], item[0]))


_FOLD_KEEP = re.compile(r"[^0-9a-z]+")


def _fold_alnum(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return _FOLD_KEEP.sub("", "".join(char for char in decomposed if not unicodedata.combining(char)))


def drop_native_lines(ocr_text: str, native: str, *, min_chars: int = 8) -> str:
    """Tira do OCR do recorte a linha que a camada de texto ja tem (legenda, texto sobreposto): sem duplicar."""

    folded_native = _fold_alnum(native)
    kept = []
    for line in ocr_text.splitlines():
        folded = _fold_alnum(line)
        if len(folded) >= min_chars and folded in folded_native:
            continue
        if line.strip():
            kept.append(line.strip())
    return "\n".join(kept)


def page_scan_ratio(page: object) -> float:
    rect = getattr(page, "rect", None)
    if rect is None:
        return 0.0
    page_area = abs(float(rect.width) * float(rect.height))
    if page_area <= 0:
        return 0.0
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0
    covered = 0.0
    for info in infos:
        bbox = info.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        covered += abs((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    return min(covered / page_area, 1.0)


def pdf_needs_ocr(path: str | Path, *, stamp_max: int = STAMP_MAX_CHARS) -> bool:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF nao esta instalado") from exc

    with pymupdf.open(path) as document:
        if document.page_count == 0:
            return False
        for page in document:
            compact = "".join(page.get_text("text").split())
            if page_needs_ocr(len(compact), page_scan_ratio(page), stamp_max=stamp_max):
                return True
    return False


def apply_page_ocr(
    path: str | Path,
    markdown: str,
    *,
    recognize=None,
) -> PageOcrResult:
    """Completa paginas que o Docling deixou so com o carimbo eproc."""

    if not pdf_needs_ocr(path):
        return PageOcrResult(markdown=markdown, pages_read=0)
    try:
        import pymupdf
    except ImportError:
        _LOG.warning("OCR de pagina: PyMuPDF indisponivel.")
        return PageOcrResult(markdown=markdown, pages_read=0)

    reader = recognize or _recognize_image
    updated = markdown
    read = 0
    failed = 0
    with pymupdf.open(path) as document:
        for index, page in enumerate(document, start=1):
            compact_native = "".join(page.get_text("text").split())
            if not page_needs_ocr(len(compact_native), page_scan_ratio(page)):
                continue
            body = _page_body(updated, index)
            if body is not None and len("".join(body.split())) >= STAMP_MAX_CHARS:
                continue
            try:
                text = reader(_render_page(page))
            except Exception:
                _LOG.exception("OCR falhou na pagina %s de %s", index, Path(path).name)
                failed += 1
                continue
            if len("".join(text.split())) < MIN_NATIVE_CHARS:
                continue
            spliced = _splice_page(updated, index, text)
            if spliced is updated:
                # Sem ancora desta pagina (Docling cortado ou deslocado): o texto
                # lido nao coube em lugar nenhum. Conta como falha, nao como lida.
                _LOG.warning("OCR da pagina %s de %s sem ancora", index, Path(path).name)
                failed += 1
                continue
            updated = spliced
            read += 1
    if read:
        updated = f"<!-- ocr-paginas: {read} -->\n" + updated
        _LOG.info("OCR seletivo: %s pagina(s) em %s", read, Path(path).name)
    if failed:
        # Marca visivel para a auditoria: falha de OCR nao pode ser silenciosa.
        updated = f"<!-- ocr-falhas: {failed} -->\n" + updated
    return PageOcrResult(markdown=updated, pages_read=read)


def _page_body(markdown: str, number: int) -> str | None:
    for match in _PAGE_BLOCK.finditer(markdown):
        if int(match.group(2)) == number:
            return match.group(3)
    return None


def _splice_page(markdown: str, number: int, ocr_text: str) -> str:
    replacement = None

    def replacer(match: re.Match[str]) -> str:
        nonlocal replacement
        if int(match.group(2)) != number:
            return match.group(0)
        body = match.group(3).rstrip()
        replacement = f"{match.group(1)}{body}\n\n{ocr_text.strip()}\n\n"
        return replacement

    updated = _PAGE_BLOCK.sub(replacer, markdown, count=0)
    return updated if replacement is not None else markdown


def _render_page(page: object) -> bytes:
    import pymupdf

    pix = page.get_pixmap(matrix=pymupdf.Matrix(_OCR_SCALE, _OCR_SCALE), alpha=False)
    return pix.tobytes("png")


def _recognize_image(image: bytes, *, page_number: int | None = None) -> str:
    from . import features, run_timing

    engine = _ocr_engine()
    cleanup = features.enabled("ocr_cleanup")
    with run_timing.stage("ocr"):  # H9-o: tempo e paginas do OCR para a AUDITORIA
        # Os passos sempre explicitos: o RapidOCR guarda o ultimo use_det/use_cls/text_score passado na chamada. Com a
        # limpeza, a linha de confianca 0,3-0,5 tambem vem: vira marcador visivel, nunca some em silencio.
        result = engine(
            image, use_det=True, use_cls=True, use_rec=True, text_score=FLOOR_SCORE if cleanup else 0.5
        )
        if cleanup:
            return "\n".join(clean_ocr_lines(engine, image, result, page_number=page_number))
    txts = getattr(result, "txts", None) or ()
    lines = [str(item).strip() for item in txts if str(item).strip()]
    return "\n".join(lines)


# v0.4.12, limpeza da leitura (medida em folhas datilografadas de 1986-2008 do caso 2): so a linha com confianca abaixo
# de REVIEW_SCORE e conferida, de novo e so no recorte dela.
# - Espelhada: o verso vazado da folha. Relida virada na horizontal, sai com confianca alta (0,94-0,99 contra 0,6-0,94
#   da leitura direta): e o verso vazado; vira UM marcador por folha, "«[verso: texto da frente vazado, omitido]»".
# - Sublinhada: relida sem as linhas horizontais longas (sublinhado de datilografia); fica a leitura de maior confianca.
# - Abaixo de LOW_SCORE: rubrica, assinatura, carimbo e mancha lidos como letras. Decisao do Operador (22/10, noite):
#   nada some em silencio; a linha fica como marcador visivel "«[OCR incerto, fl. N: <texto lido>]»" e a IA leitora e
#   avisada (INSTRUCOES_IA). So o fragmento de 1-2 caracteres (risco, ponto) abaixo de LOW_SCORE sai.
# O carimbo vertical ja e endireitado pelo proprio RapidOCR (caixa alta e estreita gira 90 graus; o classificador
# desvira 180 graus).
REVIEW_SCORE = 0.95
LOW_SCORE = 0.60
FLOOR_SCORE = 0.30
UNCERTAIN_MIN_CHARS = 3
MIRROR_MARK = "«[verso: texto da frente vazado, omitido]»"


def uncertain_mark(text: str, page_number: int | None) -> str:
    """Leitura de baixa confianca, visivel no texto (os colchetes de dentro viram parenteses: nao parecem token)."""

    where = f"fl. {page_number}" if page_number else "fl. ?"
    clean = " ".join(text.replace("«", "").replace("»", "").replace("[", "(").replace("]", ")").split())
    return f"«[OCR incerto, {where}: {clean}]»"
MIRROR_SCORE = 0.85
MIRROR_MARGIN = 0.10
UNDERLINE_MIN_PX = 60


def _decode(image: bytes):
    import cv2
    import numpy as np

    return cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)


def remove_underlines(img):
    """Imagem sem as linhas horizontais finas e longas (sublinhado), pintadas de branco."""

    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    dark = ((gray < 160) * 255).astype("uint8")
    lines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (UNDERLINE_MIN_PX, 1)))
    thick = cv2.morphologyEx(lines, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 5)))
    lines = cv2.dilate(cv2.subtract(lines, thick), cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    out = img.copy()
    out[lines > 0] = 255
    return out


def _crop(img, box):
    import numpy as np

    xs = [int(point[0]) for point in box]
    ys = [int(point[1]) for point in box]
    crop = img[max(0, min(ys)) : max(ys), max(0, min(xs)) : max(xs)]
    if crop.size == 0:
        return None
    if crop.shape[0] >= 1.5 * crop.shape[1]:
        crop = np.rot90(crop)
    return np.ascontiguousarray(crop)


def _read_crop(engine, crop) -> tuple[str, float]:
    result = engine(crop, use_det=False, use_cls=True, use_rec=True)
    txts = getattr(result, "txts", None) or ()
    scores = getattr(result, "scores", None) or ()
    if not txts:
        return "", 0.0
    return str(txts[0]), float(scores[0]) if scores else 0.0


def clean_ocr_lines(
    engine, image: bytes, result, stats: dict | None = None, *, page_number: int | None = None
) -> list[str]:
    """Linhas do OCR: o verso espelhado vira um marcador por folha; a leitura de baixa confianca, marcador visivel com o
    texto lido; a sublinhada e relida sem o sublinhado."""

    txts = list(getattr(result, "txts", None) or ())
    scores = list(getattr(result, "scores", None) or ())
    boxes = getattr(result, "boxes", None)
    boxes = list(boxes) if boxes is not None else []
    if not txts:
        return []
    img = clean = None
    lines: list[str] = []
    mirrored = False
    for index, raw in enumerate(txts):
        text = str(raw).strip()
        score = float(scores[index]) if index < len(scores) else 1.0
        if not text:
            continue
        if score < REVIEW_SCORE and index < len(boxes):
            try:
                if img is None:
                    img = _decode(image)
                crop = _crop(img, boxes[index])
                if crop is not None:
                    _flipped, flipped_score = _read_crop(engine, crop[:, ::-1].copy())
                    if flipped_score >= MIRROR_SCORE and flipped_score >= score + MIRROR_MARGIN:
                        _bump(stats, "espelhada")
                        if not mirrored:
                            lines.append(MIRROR_MARK)
                            mirrored = True
                        continue
                    if clean is None:
                        clean = remove_underlines(img)
                    again = _crop(clean, boxes[index])
                    if again is not None:
                        new_text, new_score = _read_crop(engine, again)
                        if new_text.strip() and new_score > score + 0.02:
                            text, score = new_text.strip(), new_score
                            _bump(stats, "relida_sem_sublinhado")
            except Exception:  # noqa: BLE001 - a conferencia nunca derruba a leitura da folha
                _LOG.debug("conferencia da linha do OCR falhou", exc_info=True)
        if score < LOW_SCORE:
            if sum(char.isalnum() for char in text) < UNCERTAIN_MIN_CHARS:
                _bump(stats, "fragmento")
                continue
            _bump(stats, "incerta")
            lines.append(uncertain_mark(text, page_number))
            continue
        lines.append(text)
    return lines


def _bump(stats: dict | None, key: str) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


def _ocr_engine():
    global _engine
    if _engine is None:
        from rapidocr import ModelType, OCRVersion, RapidOCR

        # Fila 3.x E4, medido em folhas nativas do lote de 25/09 renderizadas (tools/medir_ocr.py):
        # o detector padrao fundia linhas vizinhas de texto justificado numa caixa e o
        # reconhecedor lia uma so (3,1% das linhas perdidas). Sem dilatacao e com margem menor
        # na caixa: 0,6%. O reconhecedor PP-OCRv6 small (multilingue, com os acentos do
        # portugues) empata com o latino v5 mobile e leva metade do tempo.
        _engine = RapidOCR(
            params={
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Rec.model_type": ModelType.SMALL,
                "Det.unclip_ratio": OCR_UNCLIP_RATIO,
                "Det.use_dilation": False,
                "Global.log_level": "error",
            }
        )
    from . import run_timing

    run_timing.note("ocr_provider", ocr_provider_label())
    return _engine


def ocr_provider_label() -> str:
    """H9-o: o motor do OCR e os provedores do ONNX Runtime nesta maquina (o RapidOCR usa a CPU por padrao)."""

    try:
        import onnxruntime

        available = ", ".join(onnxruntime.get_available_providers())
    except Exception:  # noqa: BLE001 - so informativo
        available = "?"
    return f"RapidOCR com ONNX Runtime (provedor usado: CPUExecutionProvider, padrao; disponiveis: {available})"

"""OCR so em pagina digitalizada (carimbo eproc + imagem), sem reler peca nativa."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

MIN_NATIVE_CHARS = 15
STAMP_MAX_CHARS = 300
SCAN_AREA_RATIO = 0.20
_OCR_SCALE = 2.0
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
            updated = _splice_page(updated, index, text)
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


def _recognize_image(image: bytes) -> str:
    engine = _ocr_engine()
    result = engine(image)
    txts = getattr(result, "txts", None) or ()
    lines = [str(item).strip() for item in txts if str(item).strip()]
    return "\n".join(lines)


def _ocr_engine():
    global _engine
    if _engine is None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        # Reconhecedor "latin" (acentos do portugues) so existe no PP-OCRv5 mobile.
        _engine = RapidOCR(
            params={
                "Rec.lang_type": LangRec.LATIN,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Global.log_level": "error",
            }
        )
    return _engine

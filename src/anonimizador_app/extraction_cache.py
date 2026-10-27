"""Cache da extração: um SHA por arquivo + versão do extrator.

Padrão (26/09/2026): PyMuPDF por página + OCR seletivo (``fast_extract``).
O Docling fica como caminho legado (``prefer_docling=True``), com cache próprio.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from importlib import metadata
from pathlib import Path

from .coverage import parse_coverage
from .extractors import ExtractedDocument, extract_document
from .invisible_chars import normalize_invisible
from .fast_extract import FAST_DOCX_CACHE_SUFFIX, FAST_EXTRACTOR_VERSION, FAST_OCR_REUSABLE_FROM, extract_fast

CACHE_SUBDIR = "cache_extracao"
_OCR_PAGES = re.compile(r"<!--\s*ocr-paginas:\s*(\d+)", re.IGNORECASE)
_ANCHORS = re.compile(r"<!--\s*pagina-origem:\s*\d+", re.IGNORECASE)


def default_cache_dir(private_root: Path) -> Path:
    return Path(private_root) / CACHE_SUBDIR


def _pkg_version(*names: str) -> str:
    for name in names:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return "desconhecida"


def docling_extractor_version() -> str:
    """Identifica o circuito legado (Docling). Mudança aqui invalida o cache dele."""

    docling = _pkg_version("docling")
    return f"docling|docling={docling}|ocr-seletivo"


def default_extractor_version(*, prefer_docling: bool = False) -> str:
    return docling_extractor_version() if prefer_docling else FAST_EXTRACTOR_VERSION


def docx_aware_version(path: str | Path, version: str) -> str:
    """Versao que entra na chave do cache: .docx extraido pelo leitor rapido leva o sufixo proprio de docx
    (H9-g); PDF, texto e os demais extratores seguem com a versao que vieram."""

    if Path(path).suffix.lower() == ".docx" and version == FAST_EXTRACTOR_VERSION:
        return version + FAST_DOCX_CACHE_SUFFIX
    if version == FAST_EXTRACTOR_VERSION:
        # v0.4.12: chave do OCR desligada no config.json (recortes, limpeza) = cache proprio, nunca o da leitura ligada.
        from .features import extractor_suffix

        return version + extractor_suffix()
    return version


def cache_key(file_bytes: bytes, version: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(file_bytes)
    hasher.update(b"\0")
    hasher.update(version.encode("utf-8"))
    return hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class CachedExtraction:
    document: ExtractedDocument
    cached: bool
    generated_at: str = ""
    digest: str = ""
    path: Path | None = None

    def audit_line(self) -> str:
        if not self.cached:
            return ""
        stamp = f", gerado em {self.generated_at}" if self.generated_at else ""
        return f"Extração: cache ({self.document.extractor}{stamp})"


def load_or_extract(
    path: str | Path,
    *,
    cache_dir: Path | None,
    use_cache: bool = True,
    version_string: str | None = None,
    prefer_docling: bool = False,
    extract_fn: Callable[[Path], ExtractedDocument] | None = None,
) -> CachedExtraction:
    """Devolve o Markdown extraído, lendo o cache se a versão bater.

    Sem ``extract_fn``, usa o extrator padrão (PyMuPDF por página) ou, com
    ``prefer_docling=True``, o Docling legado. Com ``extract_fn`` próprio,
    ``version_string`` deve identificar o extrator, para não misturar caches.
    """

    # H9-o: tempo da extracao (e acerto de cache) para a AUDITORIA.
    from . import run_timing

    with run_timing.stage("extracao"):
        result = _load_or_extract(
            path,
            cache_dir=cache_dir,
            use_cache=use_cache,
            version_string=version_string,
            prefer_docling=prefer_docling,
            extract_fn=extract_fn,
        )
    if result.cached:
        run_timing.count("extracao_cache")
    return result


def _load_or_extract(
    path: str | Path,
    *,
    cache_dir: Path | None,
    use_cache: bool,
    version_string: str | None,
    prefer_docling: bool,
    extract_fn: Callable[[Path], ExtractedDocument] | None,
) -> CachedExtraction:
    source = Path(path)
    version = docx_aware_version(source, version_string or default_extractor_version(prefer_docling=prefer_docling))
    file_bytes = source.read_bytes()
    digest = cache_key(file_bytes, version)
    md_path = json_path = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        md_path = cache_dir / f"{digest}.md"
        json_path = cache_dir / f"{digest}.json"
        if use_cache and md_path.is_file() and json_path.is_file():
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            extracted = ExtractedDocument(
                # H9-j: invisiveis do PDF viram espaco tambem no cache ja gravado (nada e reextraido).
                markdown=normalize_invisible(md_path.read_text(encoding="utf-8")),
                page_count=int(meta.get("paginas") or meta.get("page_count") or 0),
                extractor=str(meta.get("extrator") or meta.get("extractor") or ""),
                ocr_used=bool(meta.get("ocr_used")),
            )
            return CachedExtraction(
                document=extracted,
                cached=True,
                generated_at=str(meta.get("data") or meta.get("generated_at") or ""),
                digest=digest,
                path=md_path,
            )

    if extract_fn is not None and extract_fn is not extract_fast:
        extracted = extract_fn(source)
    elif prefer_docling:
        extracted = extract_document(source, prefer_docling=True)
    else:
        extracted = extract_fast(
            source,
            previous=_reusable_markdown(file_bytes, cache_dir, version) if use_cache else None,
        )
    if extracted.markdown != normalize_invisible(extracted.markdown):
        # H9-j: o cache novo ja sai sem os invisiveis do PDF.
        extracted = replace(extracted, markdown=normalize_invisible(extracted.markdown))
    generated_at = datetime.now().isoformat(timespec="seconds")
    if cache_dir is not None and md_path is not None and json_path is not None:
        meta = {
            "extrator": extracted.extractor,
            "paginas": extracted.page_count,
            "paginas_ocr": _ocr_page_count(extracted.markdown),
            "ancoras": len(_ANCHORS.findall(extracted.markdown)),
            "data": generated_at,
            "caminho_original": str(source.resolve()),
            "versao": version,
            "ocr_used": extracted.ocr_used,
        }
        coverage = parse_coverage(extracted.markdown)
        if coverage is not None:
            meta["cobertura"] = {
                "nativas": coverage.native,
                "escaneadas": coverage.scanned,
                "vetoriais": coverage.vector,
                "fonte_quebrada": coverage.broken_font,
                "hibridas": coverage.hybrid,
                "recortes": coverage.embedded,
                "recortes_sem_texto": coverage.embedded_empty,
                "recortes_falhos": coverage.embedded_failed,
                "em_branco": coverage.blank,
                "nao_lidas": list(coverage.unread),
            }
        md_path.write_text(extracted.markdown, encoding="utf-8")
        json_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return CachedExtraction(
        document=extracted,
        cached=False,
        generated_at=generated_at,
        digest=digest,
        path=md_path,
    )


def _reusable_markdown(file_bytes: bytes, cache_dir: Path | None, version: str) -> str | None:
    """Extracao de uma versao anterior compativel (``FAST_OCR_REUSABLE_FROM``) do mesmo arquivo.

    So a extracao rapida atual reaproveita: as folhas lidas por OCR vem dela e as nativas sao
    refeitas. Sem cache dessa versao, ``None`` e tudo roda como sempre."""

    from .features import enabled

    if cache_dir is None or not version.startswith(FAST_EXTRACTOR_VERSION):
        return None
    if enabled("ocr_cleanup"):
        # v0.4.12: a limpeza do OCR muda a leitura das folhas escaneadas; o OCR antigo nao serve (verso espelhado).
        return None
    for older in FAST_OCR_REUSABLE_FROM:
        candidate = Path(cache_dir) / f"{cache_key(file_bytes, older)}.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


def _ocr_page_count(markdown: str) -> int:
    match = _OCR_PAGES.search(markdown)
    return int(match.group(1)) if match else 0

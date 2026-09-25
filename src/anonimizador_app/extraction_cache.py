"""Cache da extração (Docling+OCR): um SHA por arquivo + versão do extrator."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

from .extractors import ExtractedDocument, extract_document

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


def extractor_version() -> str:
    """Identifica o circuito de extração. Mudança aqui invalida o cache."""

    docling = _pkg_version("docling")
    return f"docling|docling={docling}|ocr-seletivo"


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
    prefer_docling: bool = True,
) -> CachedExtraction:
    """Devolve o Markdown extraído, lendo o cache se a versão bater."""

    source = Path(path)
    version = version_string or extractor_version()
    digest = cache_key(source.read_bytes(), version)
    md_path = json_path = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        md_path = cache_dir / f"{digest}.md"
        json_path = cache_dir / f"{digest}.json"
        if use_cache and md_path.is_file() and json_path.is_file():
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            extracted = ExtractedDocument(
                markdown=md_path.read_text(encoding="utf-8"),
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

    extracted = extract_document(source, prefer_docling=prefer_docling)
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


def _ocr_page_count(markdown: str) -> int:
    match = _OCR_PAGES.search(markdown)
    return int(match.group(1)) if match else 0

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from anonimizador_juridico import (
    PseudonymMap,
    StructuredDetector,
    propagate_mapped_ids,
    pseudonymize,
    resolve_overlaps,
)

from .audit import (
    audit_map_records,
    audit_markdown,
    extractor_note,
    fidelity_preservation_notes,
    write_audit_report,
)
from .catalog import EXPORT_BLOCKED_REASON
from .coverage import pages_label, unread_pages
from .extraction_cache import default_cache_dir, load_or_extract
from .extractors import UnsupportedDocumentError
from .fidelity import FidelityDropStats, drop_protected_spans
from .gemini_detector import ContextualEngine
from .span_hygiene import sanitize_spans
from .rules import build_recognizers
from .settings import AppSettings

ProgressFn = Callable[[str, float], None]


@dataclass(frozen=True, slots=True)
class FileOutcome:
    source: Path
    ok: bool
    markdown_path: Path | None = None
    replacements: int = 0
    entity_counts: dict[str, int] = field(default_factory=dict)
    page_count: int = 0
    extractor: str = ""
    error: str = ""
    gemini_used: bool = False
    residual_hits: tuple[str, ...] = ()
    extraction_cached: bool = False
    extraction_note: str = ""
    fidelity_drops: FidelityDropStats = field(default_factory=FidelityDropStats)
    # Folhas que nem o texto nativo nem o OCR leram: marcadas no Markdown, bloqueiam o envio.
    unread_pages: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class JobResult:
    job_id: str
    export_dir: Path
    map_path: Path
    outcomes: tuple[FileOutcome, ...]
    record_count: int
    export_allowed: bool = False
    export_block_reason: str = EXPORT_BLOCKED_REASON
    export_warning: str = ""
    gemini_used: bool = False
    contextual_used: bool = False
    residual_hits: tuple[str, ...] = ()

    @property
    def succeeded(self) -> tuple[FileOutcome, ...]:
        return tuple(item for item in self.outcomes if item.ok)

    @property
    def failed(self) -> tuple[FileOutcome, ...]:
        return tuple(item for item in self.outcomes if not item.ok)


def default_export_dir(root: Path) -> Path:
    return root / "saidas" / "exportaveis"


def default_private_dir(root: Path) -> Path:
    return root / "saidas" / "privados"


def lote_map_path(private_root: Path, job_id: str) -> Path:
    return private_root / job_id / "mapa.json"


def resolve_lote_dirs(
    export_root: Path,
    private_root: Path,
    job_id: str,
) -> tuple[Path, Path, str]:
    """Separa .md e mapa.json quando o usuario aponta a mesma pasta para os dois."""

    export_root = Path(export_root)
    private_root = Path(private_root)
    export_dir = export_root / job_id
    map_path = lote_map_path(private_root, job_id)
    warning = ""
    try:
        collided = export_dir.resolve() == map_path.parent.resolve()
    except OSError:
        collided = export_dir == map_path.parent
    if collided:
        export_dir = export_root / "exportaveis" / job_id
        map_path = private_root / "privados" / job_id / "mapa.json"
        warning = (
            "A pasta de textos e a pasta privada coincidem. "
            "Os Markdowns foram gravados em exportaveis/ e o mapa em privados/, "
            "para a chave não viajar com o lote."
        )
    return export_dir, map_path, warning


def write_lote_guides(
    *,
    job_id: str,
    export_dir: Path,
    map_path: Path,
    markdown_names: Sequence[str],
) -> None:
    """Grava um LEIA-ME em cada pasta do lote: textos anonimizados vs mapa."""

    names = "\n".join(f"  - {name}" for name in markdown_names) or "  (nenhum Markdown gravado)"
    export_dir.mkdir(parents=True, exist_ok=True)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    (export_dir / "LEIA-ME.txt").write_text(
        (
            f"LOTE {job_id}\n"
            "\n"
            "Esta pasta contem os TEXTOS ANONIMIZADOS (.md).\n"
            "Sao os arquivos que voce le, copia ou envia para analise.\n"
            "\n"
            "Arquivos deste lote:\n"
            f"{names}\n"
            "\n"
            "O mapa para REVERTER os tokens NAO esta aqui.\n"
            f"Mapa privado: {map_path}\n"
            "\n"
            "Como reverter (se precisar da peca identificada de novo):\n"
            "1. Abra o mapa.json na pasta privada do mesmo lote.\n"
            "2. Cada registro tem \"token\" (ex.: [EMPRESA_1]) e \"originals\".\n"
            "3. No Markdown, troque o token pelo texto original correspondente.\n"
            "Nao envie o mapa junto com estes .md.\n"
        ),
        encoding="utf-8",
    )
    (map_path.parent / "LEIA-ME.txt").write_text(
        (
            f"LOTE {job_id}\n"
            "\n"
            "Esta pasta contem o MAPA PRIVADO (a chave dos tokens).\n"
            "Arquivo: mapa.json\n"
            "\n"
            f"Os textos anonimizados deste lote estao em:\n  {export_dir}\n"
            "\n"
            "Como reverter: no mapa.json, cada item liga um token ao texto original.\n"
            "Nao envie esta pasta para fora desta maquina.\n"
            "A configuracao do app (regras, motor local) fica em config.json,\n"
            "na pasta privada raiz, nao dentro deste lote.\n"
        ),
        encoding="utf-8",
    )


def new_job_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"lote_{stamp}"


def run_job(
    files: Sequence[str | Path],
    *,
    export_root: Path,
    private_root: Path,
    job_id: str | None = None,
    on_progress: ProgressFn | None = None,
    contextual: ContextualEngine | None = None,
    settings: AppSettings | None = None,
    use_cache: bool = True,
) -> JobResult:
    sources = _dedupe_paths(files)
    if not sources:
        raise ValueError("Nenhum arquivo foi informado.")

    identifier = job_id or new_job_id()
    export_dir, map_path, split_warning = resolve_lote_dirs(
        export_root, private_root, identifier
    )
    export_dir.mkdir(parents=True, exist_ok=True)
    private_root.mkdir(parents=True, exist_ok=True)
    map_path.parent.mkdir(parents=True, exist_ok=True)

    active = settings or AppSettings()
    detector = StructuredDetector(recognizers=build_recognizers(active))
    mapping = PseudonymMap(token_labels=active.token_labels())
    cache_dir = default_cache_dir(private_root) if use_cache else None
    outcomes: list[FileOutcome] = []
    total = len(sources)

    for index, source in enumerate(sources):
        _report(
            on_progress,
            f"Extraindo {source.name} ({index + 1} de {total})",
            index / total,
        )
        outcomes.append(
            process_file(
                source,
                export_dir=export_dir,
                detector=detector,
                mapping=mapping,
                contextual=contextual,
                exclusions=active.exclusions,
                use_cache=use_cache,
                cache_dir=cache_dir,
            )
        )

    _report(on_progress, "Gravando mapa privado", 0.97)
    mapping.dump(map_path)
    write_audit_report(export_dir, build_audit_notes(outcomes, mapping))
    write_lote_guides(
        job_id=identifier,
        export_dir=export_dir,
        map_path=map_path,
        markdown_names=[
            outcome.markdown_path.name
            for outcome in outcomes
            if outcome.ok and outcome.markdown_path is not None
        ],
    )
    _report(on_progress, "Concluido", 1.0)
    return build_job_result(
        job_id=identifier,
        export_dir=export_dir,
        map_path=map_path,
        outcomes=tuple(outcomes),
        record_count=len(mapping.records),
        contextual_used=contextual is not None,
        extra_warning=split_warning,
    )


def process_file(
    source: Path,
    *,
    export_dir: Path,
    detector: StructuredDetector,
    mapping: PseudonymMap,
    contextual: ContextualEngine | None = None,
    exclusions: Iterable[str] = (),
    use_cache: bool = True,
    cache_dir: Path | None = None,
) -> FileOutcome:
    """Extrai, detecta e tokeniza um arquivo.

    Encadeamento (fidelidade vence recall): IDs estruturados → filial/alias
    de CPF/CNPJ já mapeados → lista de alvos / eproc / capa / tabela / formulário
    / endereço → NER → drop de órgão/juiz/ministro/PFN e de nome só banal →
    higiene de span → troca.
    """
    try:
        cached = load_or_extract(source, cache_dir=cache_dir, use_cache=use_cache)
        extracted = cached.document
        structured = detector.detect(extracted.markdown)
        structured = propagate_mapped_ids(
            extracted.markdown, structured, mapping=mapping
        )
        contextual_spans = contextual.detect(extracted.markdown) if contextual else []
        entities = resolve_overlaps([*structured, *contextual_spans])
        fidelity_stats = FidelityDropStats()
        entities = drop_protected_spans(
            extracted.markdown,
            entities,
            exclusions,
            source_name=source.name,
            stats=fidelity_stats,
        )
        entities = sanitize_spans(
            extracted.markdown, entities, stats=fidelity_stats
        )
        result = pseudonymize(extracted.markdown, entities, mapping=mapping)
        residual = tuple(contextual.scan_residual(result.text)) if contextual else ()
        markdown_path = _unique_path(export_dir, source.stem, ".md")
        markdown_path.write_text(result.text, encoding="utf-8")
        counts = Counter(entity.entity_type for entity in entities)
        note = cached.audit_line() or ""
        return FileOutcome(
            source=source,
            ok=True,
            markdown_path=markdown_path,
            replacements=result.replacements,
            entity_counts=dict(counts),
            page_count=extracted.page_count,
            extractor=extracted.extractor,
            residual_hits=residual,
            extraction_cached=cached.cached,
            extraction_note=note,
            fidelity_drops=fidelity_stats,
            unread_pages=unread_pages(extracted.markdown),
        )
    except UnsupportedDocumentError as exc:
        return FileOutcome(source=source, ok=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a UI precisa da mensagem por arquivo
        return FileOutcome(source=source, ok=False, error=str(exc))


def build_job_result(
    *,
    job_id: str,
    export_dir: Path,
    map_path: Path,
    outcomes: tuple[FileOutcome, ...],
    record_count: int,
    contextual_used: bool,
    gemini_used: bool = False,
    extra_warning: str = "",
) -> JobResult:
    residual = tuple(
        hit for outcome in outcomes if outcome.ok for hit in outcome.residual_hits
    )
    failed = any(not outcome.ok for outcome in outcomes)
    unread = [
        (outcome.source.name, outcome.unread_pages)
        for outcome in outcomes
        if outcome.ok and outcome.unread_pages
    ]
    warning = extra_warning.strip()
    if failed:
        allowed = False
        reason = "Há arquivos com erro. Corrija o lote antes de copiar o resultado."
    elif unread:
        allowed = False
        listed = "; ".join(f"{name}: páginas {pages_label(pages)}" for name, pages in unread)
        reason = (
            "Há folhas que não puderam ser lidas (" + listed + "). Estão marcadas no "
            "texto e na AUDITORIA. Confira no PDF original antes de enviar."
        )
    elif not contextual_used:
        allowed = False
        reason = EXPORT_BLOCKED_REASON
    else:
        allowed = True
        reason = (
            "Textos anonimizados gravados. Revise o trecho. "
            "O mapa privado fica em outra pasta e não deve ser copiado junto."
        )
        if residual:
            residual_note = (
                "A revisão residual apontou trechos possivelmente identificáveis, "
                "que não foram substituídos para não descaracterizar a peça: "
                + ", ".join(residual[:8])
            )
            warning = f"{warning} {residual_note}".strip() if warning else residual_note
    return JobResult(
        job_id=job_id,
        export_dir=export_dir,
        map_path=map_path,
        outcomes=outcomes,
        record_count=record_count,
        export_allowed=allowed,
        export_block_reason=reason,
        export_warning=warning,
        gemini_used=gemini_used,
        contextual_used=contextual_used,
        residual_hits=residual,
    )


def build_audit_notes(
    outcomes: Sequence[FileOutcome],
    mapping: PseudonymMap,
) -> list[tuple[str, list[str]]]:
    """AUDITORIA.txt: primeiro o mapa (sem PII), depois cada Markdown."""

    notes: list[tuple[str, list[str]]] = [
        ("mapa.json", audit_map_records(mapping.records)),
    ]
    for outcome in outcomes:
        if outcome.ok and outcome.markdown_path is not None:
            text = outcome.markdown_path.read_text(encoding="utf-8")
            notes.append((outcome.markdown_path.name, _file_audit_notes(outcome, text)))
    return notes


def _file_audit_notes(outcome: FileOutcome, text: str) -> list[str]:
    notes = [extractor_note(outcome.extractor)]
    if outcome.extraction_note:
        notes.append(outcome.extraction_note)
    notes.extend(audit_markdown(text))
    notes.extend(fidelity_preservation_notes(outcome.fidelity_drops))
    return notes


def _dedupe_paths(files: Iterable[str | Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for item in files:
        path = Path(item).expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


def _report(on_progress: ProgressFn | None, message: str, fraction: float) -> None:
    if on_progress is not None:
        on_progress(message, fraction)

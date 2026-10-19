from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from anonimizador_juridico import (
    EntitySpan,
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
from .batch_propagation import RECOGNIZER as PROPAGATION_RECOGNIZER
from .batch_propagation import BatchRoster
from .catalog import EXPORT_BLOCKED_REASON
from .coverage import pages_label, unread_pages
from .extraction_cache import CachedExtraction, default_cache_dir, load_or_extract
from .extractors import UnsupportedDocumentError
from .file_names import export_stem, write_name_map
from .fidelity import FidelityDropStats, drop_protected_spans
from .lote_elenco import CAST_NAME, apply_company_groups, write_lote_cast
from . import lote_alerts
from .lote_index import INDEX_NAME, add_sheet_anchors, write_lote_index
from .gemini_detector import ContextualEngine
from .local_ner import ner_fallback_warning, reset_ner_status
from .netblock import block_network
from .span_hygiene import sanitize_spans
from .rules import build_recognizers
from .settings import AppSettings, case_list_lines

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
    # Ocorrencias acrescentadas pela propagacao do lote (nome ou ID ja detectado em outro ponto).
    propagated: int = 0


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


def _read_quiet(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_lote_guides(
    *,
    job_id: str,
    export_dir: Path,
    map_path: Path,
    markdown_names: Sequence[str],
    engine: str = "local",
    notes: Sequence[str] = (),
) -> None:
    """Grava um LEIA-ME em cada pasta do lote: textos anonimizados vs mapa."""

    from .local_ner import ner_fallback_warning
    from .version import version_info

    names = "\n".join(f"  - {name}" for name in markdown_names) or "  (nenhum Markdown gravado)"
    ner_warning = ner_fallback_warning()
    attention = "".join(f"ATENCAO: {line}\n" for line in [*notes, ner_warning] if line)
    # O paragrafo do sufixo de grupo so vale quando algum texto tem token de grupo (·G1).
    group_note = ""
    if any("·G" in _read_quiet(export_dir / name) for name in markdown_names):
        group_note = (
            "\n"
            "Sufixo de grupo: token de empresa como [EMPRESA_3·G1] e [EMPRESA_7·G1] sao tokens\n"
            "SEPARADOS (cada um com o seu nome) que o ELENCO.md declara ligados (mudanca de nome\n"
            "ou mesmo CNPJ nas capas); G1 e o grupo. Para reverter, ignore o sufixo: o mapa tem\n"
            "[EMPRESA_3] e [EMPRESA_7].\n"
        )
    if attention:
        attention += "\n"
    generated_by = f"Gerado por: {version_info().line(engine)}\n"
    export_dir.mkdir(parents=True, exist_ok=True)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    (export_dir / "LEIA-ME.txt").write_text(
        (
            f"LOTE {job_id}\n"
            + generated_by
            + "\n"
            "Esta pasta contem os TEXTOS ANONIMIZADOS (.md).\n"
            "Sao os arquivos que voce le, copia ou envia para analise.\n"
            "\n"
            "Arquivos deste lote:\n"
            f"{names}\n"
            "\n"
            + attention
            + f"{INDEX_NAME}: eventos e documentos de cada arquivo, com a faixa de folhas,\n"
            "as folhas lidas por OCR e as tabelas; documento que continua de um PARTE\n"
            "para o seguinte vem marcado. Envie junto com os .md.\n"
            f"{CAST_NAME}: quem e quem (requerido, executado, procurador...) pelos tokens,\n"
            "sem nomes, tirado da capa de cada processo. Envie junto com os .md.\n"
            f"{lote_alerts.INSTRUCTIONS_NAME}: o que a IA que vai ler estes textos precisa saber (tokens,\n"
            "nao deduzir parentesco, lote parcial, sigilo). Envie junto, de preferencia como primeira mensagem.\n"
            "\n"
            "O mapa para REVERTER os tokens NAO esta aqui.\n"
            f"Mapa privado: {map_path}\n"
            "\n"
            "Como reverter (se precisar da peca identificada de novo):\n"
            "1. Abra o mapa.json na pasta privada do mesmo lote.\n"
            "2. Cada registro tem \"token\" (ex.: [EMPRESA_1]) e \"originals\".\n"
            "3. No Markdown, troque o token pelo texto original correspondente (o campo \"preferida\" do\n"
            "   registro e a grafia certa, com acento).\n"
            "Para conferir uma peca que outra IA desanonimizou: python tools\\reverter_lote.py <mapa.json>\n"
            "<peca com tokens> --conferir <peca desanonimizada> (so contagens na tela).\n"
            + group_note
            + "Nao envie o mapa junto com estes .md.\n"
        ),
        encoding="utf-8",
    )
    (map_path.parent / "LEIA-ME.txt").write_text(
        (
            f"LOTE {job_id}\n"
            + generated_by
            + "\n"
            "Esta pasta contem o MAPA PRIVADO (a chave dos tokens).\n"
            "Arquivo: mapa.json\n"
            "\n"
            f"Os textos anonimizados deste lote estao em:\n  {export_dir}\n"
            "\n"
            "Como reverter: no mapa.json, cada item liga um token ao texto original.\n"
            "O sufixo de grupo do texto ([EMPRESA_3·G1]) nao esta no mapa: procure [EMPRESA_3].\n"
            "Nao envie esta pasta para fora desta maquina.\n"
            f"{PRIVATE_AUDIT_NAME}: as palavras da marca aprovada e dos municipios recusados (a\n"
            "AUDITORIA dos textos traz so tokens e contagens).\n"
            "A configuracao do app (regras, motor local) fica em config.json,\n"
            "na pasta privada raiz, nao dentro deste lote.\n"
        ),
        encoding="utf-8",
    )


def write_outcomes_index(export_dir: Path, outcomes: Sequence[FileOutcome]) -> Path:
    """INDICE.md (eventos e documentos com faixa de folhas) e ELENCO.md (papeis sem nomes),
    ambos do texto ja tokenizado."""

    paths = [outcome.markdown_path for outcome in outcomes if outcome.ok and outcome.markdown_path]
    write_lote_cast(export_dir, paths)
    index = write_lote_index(export_dir, paths)
    # H9-j: sigilo e evento citado fora do lote (no INDICE e nas instrucoes para a IA redatora).
    if paths:  # arquivo retido (nada exportado): sem instrucoes, nada a mandar para fora
        alerts = lote_alerts.analyze([(path.name, path.read_text(encoding="utf-8")) for path in paths])
        lote_alerts.append_to_index(index, alerts)
        lote_alerts.write_ai_instructions(export_dir, alerts)
    return index


PRIVATE_AUDIT_NAME = "AUDITORIA_PRIVADA.txt"


def finalize_lote(
    *,
    job_id: str,
    export_dir: Path,
    map_path: Path,
    outcomes: Sequence[FileOutcome],
    mapping: PseudonymMap,
    roster: BatchRoster | None = None,
    network_attempts: int | None = None,
    settings: AppSettings | None = None,
) -> None:
    """Depois da troca de todos os arquivos: mapa, AUDITORIA (e a privada), INDICE, ELENCO e LEIA-ME.

    ``network_attempts``: tentativas de rede fora de loopback barradas durante a rodada (H9-d); a AUDITORIA
    registra o numero mesmo sem LLM, para o zero ficar escrito."""

    mapping.dump(map_path)
    # Empresas ligadas por vinculo declarado levam o sufixo de grupo no texto, antes da AUDITORIA, do INDICE e
    # do ELENCO (que leem o texto ja com o sufixo).
    apply_company_groups([o.markdown_path for o in outcomes if o.ok and o.markdown_path is not None])
    notes = build_audit_notes(outcomes, mapping, roster)
    notes.append(lote_alerts.audit_section([o.markdown_path for o in outcomes if o.ok and o.markdown_path is not None]))
    if network_attempts is not None:
        notes.insert(0, ("motor", [*local_network_lines(network_attempts), *case_list_lines(settings)]))
    write_audit_report(export_dir, notes)
    if roster is not None:
        write_private_audit(map_path.parent, roster, mapping)
    write_outcomes_index(export_dir, outcomes)
    write_name_map(
        map_path.parent,
        ((o.markdown_path.name, o.source.name) for o in outcomes if o.ok and o.markdown_path is not None),
    )
    write_lote_guides(
        job_id=job_id,
        export_dir=export_dir,
        map_path=map_path,
        markdown_names=[
            outcome.markdown_path.name
            for outcome in outcomes
            if outcome.ok and outcome.markdown_path is not None
        ],
    )


def write_private_audit(private_dir: Path, roster: BatchRoster, mapping: PseudonymMap) -> Path:
    """AUDITORIA com as palavras (marca aprovada, municipio recusado), na pasta privada do lote.
    A exportavel traz so tokens e contagens."""

    lines = [
        "AUDITORIA PRIVADA DESTE LOTE",
        "Contem palavras reais do caso. NAO ENVIAR: fica com o mapa, fora da pasta dos textos.",
        "",
        "## Marca de uma palavra (confira se cada palavra aprovada e mesmo nome proprio da empresa)",
        *(f"- {line}" for line in roster.brand_report(mapping, private=True)),
        "",
    ]
    path = Path(private_dir) / PRIVATE_AUDIT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


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

    # H9-d: a rodada local inteira roda com a rede bloqueada fora do loopback (o loopback fica livre para a
    # tela). Nada aqui precisa de rede; a contagem vai para a AUDITORIA e tem de dar zero.
    with block_network(allow_loopback=True) as attempts:
        return _run_job_blocked(
            sources,
            identifier=identifier,
            export_dir=export_dir,
            map_path=map_path,
            split_warning=split_warning,
            private_root=private_root,
            on_progress=on_progress,
            contextual=contextual,
            settings=settings,
            use_cache=use_cache,
            attempts=attempts,
        )


def local_network_lines(blocked: int) -> list[str]:
    """Bloco "motor" da AUDITORIA do modo local (sem LLM): so contagens."""

    return [
        "Motor: local (sem LLM). Nenhuma chamada a provedor: nada saiu desta maquina.",
        f"Tentativas de rede fora de loopback bloqueadas: {blocked}",
    ]


def _run_job_blocked(
    sources: Sequence[Path],
    *,
    identifier: str,
    export_dir: Path,
    map_path: Path,
    split_warning: str,
    private_root: Path,
    on_progress: ProgressFn | None,
    contextual: ContextualEngine | None,
    settings: AppSettings | None,
    use_cache: bool,
    attempts: list[str],
) -> JobResult:
    active = settings or AppSettings()
    reset_ner_status()
    detector = StructuredDetector(recognizers=build_recognizers(active))
    mapping = PseudonymMap(token_labels=active.token_labels())
    cache_dir = default_cache_dir(private_root) if use_cache else None
    total = len(sources)

    # Fase 1: le e detecta todos os arquivos; o elenco e do lote inteiro.
    roster = BatchRoster(active.exclusions)
    prepared: list[PreparedFile] = []
    for index, source in enumerate(sources):
        _report(
            on_progress,
            f"Lendo e detectando {source.name} ({index + 1} de {total})",
            0.8 * index / total,
        )
        item = prepare_file(
            source,
            detector=detector,
            known_ids=known_ids(mapping, roster),
            contextual=contextual,
            exclusions=active.exclusions,
            use_cache=use_cache,
            cache_dir=cache_dir,
        )
        prepared.append(item)
        if item.ok:
            roster.add(item.text, item.entities)
    _report(on_progress, "Montando o elenco do lote", 0.8)
    roster.review(item.text for item in prepared if item.ok)

    # Fase 2: mesma entidade, mesmo token, em todos os arquivos.
    outcomes: list[FileOutcome] = []
    for index, item in enumerate(prepared):
        _report(
            on_progress,
            f"Trocando e gravando {item.source.name} ({index + 1} de {total})",
            0.8 + 0.17 * index / total,
        )
        outcomes.append(
            finish_file(
                item,
                export_dir=export_dir,
                mapping=mapping,
                roster=roster,
                contextual=contextual,
                ordinal=index + 1,
            )
        )

    _report(on_progress, "Gravando mapa privado", 0.97)
    finalize_lote(
        job_id=identifier,
        export_dir=export_dir,
        map_path=map_path,
        outcomes=outcomes,
        mapping=mapping,
        roster=roster,
        network_attempts=len(attempts),
        settings=active,
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


@dataclass(slots=True)
class PreparedFile:
    """Arquivo lido e detectado (fase 1), ainda sem troca."""

    source: Path
    cached: CachedExtraction | None = None
    entities: list[EntitySpan] = field(default_factory=list)
    stats: FidelityDropStats = field(default_factory=FidelityDropStats)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.cached is not None

    @property
    def text(self) -> str:
        return self.cached.document.markdown if self.cached is not None else ""


class _KnownIds:
    """CPF/CNPJ ja vistos (mapa + lote): o alias de OCR de um arquivo acha o do outro."""

    def __init__(self, *sources: object) -> None:
        self._sources = sources

    @property
    def records(self) -> tuple:
        return tuple(record for source in self._sources for record in getattr(source, "records", ()))


def known_ids(mapping: PseudonymMap, roster: BatchRoster) -> _KnownIds:
    return _KnownIds(mapping, roster)


def prepare_file(
    source: Path,
    *,
    detector: StructuredDetector,
    known_ids: object | None = None,
    contextual: ContextualEngine | None = None,
    exclusions: Iterable[str] = (),
    use_cache: bool = True,
    cache_dir: Path | None = None,
) -> PreparedFile:
    """Fase 1: extrai e detecta, sem trocar.

    Encadeamento (fidelidade vence recall): IDs estruturados → filial/alias
    de CPF/CNPJ já vistos → lista de alvos / eproc / capa / tabela / formulário
    / endereço → NER → drop de órgão/juiz/ministro/PFN e de nome só banal →
    higiene de span.
    """
    try:
        cached = load_or_extract(source, cache_dir=cache_dir, use_cache=use_cache)
        text = cached.document.markdown
        structured = detector.detect(text)
        structured = propagate_mapped_ids(text, structured, mapping=known_ids)
        contextual_spans = contextual.detect(text) if contextual else []
        entities = resolve_overlaps([*structured, *contextual_spans])
        stats = FidelityDropStats()
        entities = drop_protected_spans(
            text, entities, exclusions, source_name=source.name, stats=stats
        )
        entities = sanitize_spans(text, entities, stats=stats)
        return PreparedFile(source=source, cached=cached, entities=entities, stats=stats)
    except UnsupportedDocumentError as exc:
        return PreparedFile(source=source, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a UI precisa da mensagem por arquivo
        return PreparedFile(source=source, error=str(exc) or type(exc).__name__)


def finish_file(
    prepared: PreparedFile,
    *,
    export_dir: Path,
    mapping: PseudonymMap,
    roster: BatchRoster | None = None,
    contextual: ContextualEngine | None = None,
    ordinal: int = 0,
) -> FileOutcome:
    """Fase 2: aplica o elenco do lote (mesmo token por entidade; ocorrências que
    a detecção não pegou), troca e grava."""

    if not prepared.ok or prepared.cached is None:
        return FileOutcome(source=prepared.source, ok=False, error=prepared.error or "Falha na leitura.")
    try:
        text = prepared.text
        entities = prepared.entities
        if roster is not None:
            entities = roster.apply(text, entities, source_name=prepared.source.name)
        result = pseudonymize(text, entities, mapping=mapping)
        residual = tuple(contextual.scan_residual(result.text)) if contextual else ()
        # H9-f: o nome exportado nao leva o nome de origem (pode ter razao social); ver ``file_names``.
        number = ordinal or 1 + sum(1 for _item in export_dir.glob("*.md"))
        markdown_path = _unique_path(export_dir, export_stem(prepared.source, mapping, number), ".md")
        # Ancora visivel por folha ("[fl. 84 · Evento 1 · CONTRSOCIAL4 · p. 1]"), depois da troca:
        # nao entra na deteccao nem muda os caches.
        markdown_path.write_text(add_sheet_anchors(result.text, markdown_path.name), encoding="utf-8")
        counts = Counter(entity.entity_type for entity in entities)
        extracted = prepared.cached.document
        return FileOutcome(
            source=prepared.source,
            ok=True,
            markdown_path=markdown_path,
            replacements=result.replacements,
            entity_counts=dict(counts),
            page_count=extracted.page_count,
            extractor=extracted.extractor,
            residual_hits=residual,
            extraction_cached=prepared.cached.cached,
            extraction_note=prepared.cached.audit_line() or "",
            fidelity_drops=prepared.stats,
            unread_pages=unread_pages(text),
            propagated=sum(1 for entity in entities if entity.recognizer == PROPAGATION_RECOGNIZER),
        )
    except Exception as exc:  # noqa: BLE001 - a UI precisa da mensagem por arquivo
        return FileOutcome(source=prepared.source, ok=False, error=str(exc) or type(exc).__name__)


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
    """Um arquivo só: as duas fases com o elenco do próprio arquivo (``run_job`` usa o do lote)."""

    roster = BatchRoster(exclusions)
    prepared = prepare_file(
        source,
        detector=detector,
        known_ids=known_ids(mapping, roster),
        contextual=contextual,
        exclusions=exclusions,
        use_cache=use_cache,
        cache_dir=cache_dir,
    )
    if prepared.ok:
        roster.add(prepared.text, prepared.entities)
        roster.review([prepared.text])
    return finish_file(
        prepared, export_dir=export_dir, mapping=mapping, roster=roster, contextual=contextual
    )


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
    ner_warning = ner_fallback_warning()
    if ner_warning:
        warning = f"{warning} {ner_warning}".strip()
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
    roster: BatchRoster | None = None,
) -> list[tuple[str, list[str]]]:
    """AUDITORIA.txt: primeiro o mapa (sem PII), a marca de uma palavra (so tokens), depois cada Markdown."""

    notes: list[tuple[str, list[str]]] = [
        ("mapa.json", audit_map_records(mapping.records)),
    ]
    if roster is not None:
        notes.append(("elenco do lote: marca de uma palavra (so tokens e contagens)", roster.brand_report(mapping)))
    for outcome in outcomes:
        if outcome.ok and outcome.markdown_path is not None:
            text = outcome.markdown_path.read_text(encoding="utf-8")
            notes.append((outcome.markdown_path.name, _file_audit_notes(outcome, text)))
    return notes


def _file_audit_notes(outcome: FileOutcome, text: str) -> list[str]:
    notes = [extractor_note(outcome.extractor)]
    if outcome.extraction_note:
        notes.append(outcome.extraction_note)
    notes.append(
        f"Propagacao do lote: {outcome.propagated} ocorrencia(s) acrescentada(s) "
        "(nome ou ID ja detectado em outro ponto do lote)"
    )
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

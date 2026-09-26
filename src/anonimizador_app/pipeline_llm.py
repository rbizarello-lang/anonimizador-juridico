"""Pipeline com LLM: o modelo descobre o elenco, o codigo troca, o modelo confere.

Tres fases por lote: (1) descoberta em todos os arquivos, (2) verificacao,
(3) troca final com o elenco do processo inteiro. Encadeamento por arquivo:

1. extracao fiel e rapida (``fast_extract``) com ancora de pagina;
2. IDs estruturados nesta maquina (Presidio + regras de fidelidade de ID);
3. texto com os IDs ja mascarados vai ao LLM (``Discoverer``), que devolve
   quem aparece e com que papel — nada de CPF/CNPJ sai da maquina;
4. elenco: uma entrada por entidade; a tabela de papeis decide o que troca;
5. propagacao deterministica a todas as ocorrencias; termo protegido fica;
6. troca por token estavel (mesmo ``PseudonymMap`` do app);
7. verificacao: o LLM le o texto JA tokenizado; o que ainda identificar
   entra no elenco e o arquivo e refeito (``verification_rounds``).

Fail-closed: pagina que nenhuma passada conseguiu ler bloqueia a exportacao
e aparece na AUDITORIA. O elenco com originais fica em ``privados``; a
legenda exportavel (token -> tipo -> papel) nao leva nome.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from anonimizador_juridico import (
    EntitySpan,
    PseudonymMap,
    StructuredDetector,
    propagate_mapped_ids,
    pseudonymize,
    resolve_overlaps,
)
from anonimizador_juridico.pseudonymizer import PseudonymizationResult

from .audit import audit_map_records, audit_markdown, write_audit_report
from .coverage import unread_pages as extraction_unread_pages
from .extraction_cache import CachedExtraction, default_cache_dir, load_or_extract
from .extractors import ExtractedDocument, UnsupportedDocumentError
from .fast_extract import FAST_EXTRACTOR_VERSION, extract_fast
from .fidelity import FidelityDropStats, drop_protected_spans
from .llm_discovery import Discoverer, DiscoveryReport, LlmConfigError, page_of
from .pipeline import (
    FileOutcome,
    JobResult,
    ProgressFn,
    _dedupe_paths,
    _report,
    _unique_path,
    build_job_result,
    new_job_id,
    resolve_lote_dirs,
    write_lote_guides,
)
from .propagation import FoldedText, fold_with_index, spans_for_roster
from .roster import KEEP, TOKENIZE, Mention, Roster
from .rules import build_recognizers
from .settings import AppSettings

LLM_CACHE_SUBDIR = "cache_llm"
ROSTER_FILENAME = "elenco.json"
LEGEND_FILENAME = "ELENCO.md"


@dataclass(frozen=True, slots=True)
class OffsetMap:
    """Liga posicoes do texto com tokens as posicoes do texto original."""

    new_starts: tuple[int, ...] = ()
    new_ends: tuple[int, ...] = ()
    orig_ends: tuple[int, ...] = ()

    def to_original(self, start: int, end: int) -> tuple[int, int] | None:
        """``None`` se o trecho encosta num token (ja anonimizado)."""

        first = max(bisect_right(self.new_starts, start) - 1, 0)
        last = bisect_left(self.new_starts, end)
        for index in range(first, last):
            if self.new_starts[index] < end and start < self.new_ends[index]:
                return None
        before = bisect_right(self.new_starts, start) - 1
        if before < 0:
            return start, end
        shift = self.orig_ends[before] - self.new_ends[before]
        return start + shift, end + shift


@dataclass(slots=True)
class LlmFileReport:
    discovery: DiscoveryReport
    verification: list[DiscoveryReport] = field(default_factory=list)
    unread_pages: list[int] = field(default_factory=list)
    new_in_verification: int = 0

    @property
    def unverified(self) -> bool:
        return bool(self.unread_pages)


@dataclass(frozen=True, slots=True)
class LlmJobResult:
    job: JobResult
    reports: dict[str, LlmFileReport]
    roster_path: Path
    legend_path: Path | None
    roster: Roster


def run_llm_job(
    files: Sequence[str | Path],
    *,
    export_root: Path,
    private_root: Path,
    discoverer: Discoverer,
    settings: AppSettings | None = None,
    roster_path: Path | None = None,
    verification_rounds: int = 1,
    use_cache: bool = True,
    extract_fn: Callable[[Path], ExtractedDocument] = extract_fast,
    extractor_version: str = FAST_EXTRACTOR_VERSION,
    write_legend: bool = True,
    on_progress: ProgressFn | None = None,
) -> LlmJobResult:
    """Tres fases: descobre em todos os arquivos, confere, e so entao troca.

    O elenco e do processo inteiro, nao do arquivo: socio que so aparece com
    nome completo na PARTE_2 tambem e trocado na PARTE_1.
    """

    sources = _dedupe_paths(files)
    if not sources:
        raise ValueError("Nenhum arquivo foi informado.")

    identifier = new_job_id()
    export_dir, map_path, split_warning = resolve_lote_dirs(
        Path(export_root), Path(private_root), identifier
    )
    export_dir.mkdir(parents=True, exist_ok=True)
    map_path.parent.mkdir(parents=True, exist_ok=True)

    active = settings or AppSettings()
    exclusions = tuple(active.exclusions)
    detector = StructuredDetector(recognizers=build_recognizers(active))
    mapping = PseudonymMap(token_labels=active.token_labels())
    roster = Roster.load(roster_path) if roster_path else Roster()
    for name in active.target_companies:
        roster.add_listed("empresa", name)
    for name in active.target_people:
        roster.add_listed("pessoa", name)
    cache_dir = default_cache_dir(Path(private_root)) if use_cache else None

    total = len(sources)
    states: list[_FileState] = []
    errors: dict[Path, FileOutcome] = {}
    for index, source in enumerate(sources):
        _report(on_progress, f"Lendo {source.name} ({index + 1} de {total})", 0.5 * index / total)
        state = _guard(
            source,
            errors,
            lambda: _discover_file(
                source,
                detector=detector,
                mapping=mapping,
                roster=roster,
                discoverer=discoverer,
                exclusions=exclusions,
                use_cache=use_cache,
                cache_dir=cache_dir,
                extract_fn=extract_fn,
                extractor_version=extractor_version,
            ),
        )
        if state is not None:
            states.append(state)

    for index, state in enumerate(states):
        _report(
            on_progress,
            f"Conferindo {state.source.name} ({index + 1} de {len(states)})",
            0.5 + 0.4 * index / max(1, len(states)),
        )
        _guard(
            state.source,
            errors,
            lambda: _verify_file(
                state,
                roster=roster,
                discoverer=discoverer,
                mapping=mapping,
                exclusions=exclusions,
                rounds=verification_rounds,
            ),
        )

    _report(on_progress, "Gravando textos, mapa e elenco", 0.95)
    outcomes: list[FileOutcome] = []
    reports: dict[str, LlmFileReport] = {}
    notes: list[tuple[str, list[str]]] = []
    for source in sources:
        if source in errors:
            outcomes.append(errors[source])
            continue
        state = next(item for item in states if item.source == source)
        # Troca final com o elenco completo do lote (so acrescenta tokens).
        entities, result, _offsets = _tokenize(
            state.text, state.ids, roster, state.anchored, exclusions, state.folded, mapping
        )
        state.report.unread_pages = sorted(set.intersection(*state.failed_pages))
        markdown_path = _unique_path(export_dir, source.stem, ".md")
        markdown_path.write_text(result.text, encoding="utf-8")
        outcome = FileOutcome(
            source=source,
            ok=True,
            markdown_path=markdown_path,
            replacements=result.replacements,
            entity_counts=dict(Counter(entity.entity_type for entity in entities)),
            page_count=state.cached.document.page_count,
            extractor=state.cached.document.extractor,
            gemini_used=_is_remote(discoverer),
            extraction_cached=state.cached.cached,
            extraction_note=state.cached.audit_line() or "",
            fidelity_drops=state.stats,
            unread_pages=extraction_unread_pages(state.cached.document.markdown),
        )
        outcomes.append(outcome)
        reports[source.name] = state.report
        notes.append((markdown_path.name, _file_notes(outcome, state.report, discoverer, result.text)))

    mapping.dump(map_path)
    roster_file = roster.dump(map_path.parent / ROSTER_FILENAME)
    legend_path = _write_legend(export_dir, roster, mapping) if write_legend else None
    notes.insert(0, ("elenco (sem nomes)", roster_summary(roster)))
    notes.insert(0, ("mapa.json", audit_map_records(mapping.records)))
    write_audit_report(export_dir, notes)
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
    _append_roster_guide(map_path.parent / "LEIA-ME.txt")
    job = build_job_result(
        job_id=identifier,
        export_dir=export_dir,
        map_path=map_path,
        outcomes=tuple(outcomes),
        record_count=len(mapping.records),
        contextual_used=True,
        gemini_used=_is_remote(discoverer),
        extra_warning=split_warning,
    )
    unread = {name: item.unread_pages for name, item in reports.items() if item.unverified}
    if unread and job.export_allowed:
        listed = "; ".join(f"{name}: {_pages_label(pages)}" for name, pages in unread.items())
        job = replace(
            job,
            export_allowed=False,
            export_block_reason=(
                "O LLM nao conseguiu ler todas as paginas (" + listed + "). "
                "Rode de novo (o cache aproveita o que ja foi lido) antes de enviar."
            ),
        )
    _report(on_progress, "Concluido", 1.0)
    return LlmJobResult(
        job=job,
        reports=reports,
        roster_path=roster_file,
        legend_path=legend_path,
        roster=roster,
    )


@dataclass(slots=True)
class _FileState:
    source: Path
    cached: CachedExtraction
    text: str
    ids: list[EntitySpan]
    stats: FidelityDropStats
    folded: FoldedText
    anchored: list[tuple[int, int, str]]
    report: LlmFileReport
    failed_pages: list[set[int]]


def _discover_file(
    source: Path,
    *,
    detector: StructuredDetector,
    mapping: PseudonymMap,
    roster: Roster,
    discoverer: Discoverer,
    exclusions: Sequence[str],
    use_cache: bool,
    cache_dir: Path | None,
    extract_fn: Callable[[Path], ExtractedDocument],
    extractor_version: str,
) -> _FileState:
    """Fase 1: extrai, acha os IDs aqui, mascara e manda o LLM descobrir o elenco."""

    cached = load_or_extract(
        source,
        cache_dir=cache_dir,
        use_cache=use_cache,
        version_string=extractor_version,
        extract_fn=extract_fn,
    )
    text = cached.document.markdown
    stats = FidelityDropStats()
    ids = detector.detect(text)
    ids = propagate_mapped_ids(text, ids, mapping=mapping)
    ids = resolve_overlaps(
        drop_protected_spans(text, ids, exclusions, source_name=source.name, stats=stats)
    )
    masked, masked_offsets = pseudonymize_with_offsets(text, ids, mapping)
    discovery = discoverer.discover(masked.text)
    return _FileState(
        source=source,
        cached=cached,
        text=text,
        ids=ids,
        stats=stats,
        folded=fold_with_index(text),
        anchored=absorb_mentions(discovery.mentions, roster, masked_offsets, source="llm"),
        report=LlmFileReport(discovery=discovery.report),
        failed_pages=[_failed_pages(masked.text, discovery.report)],
    )


def _verify_file(
    state: _FileState,
    *,
    roster: Roster,
    discoverer: Discoverer,
    mapping: PseudonymMap,
    exclusions: Sequence[str],
    rounds: int,
) -> None:
    """Fase 2: o LLM le o texto ja tokenizado; o que ainda identifica entra no elenco."""

    _entities, result, offsets = _tokenize(
        state.text, state.ids, roster, state.anchored, exclusions, state.folded, mapping
    )
    for _round in range(max(0, rounds)):
        before = _tokenizable_state(roster)
        check = discoverer.discover(result.text)
        state.report.verification.append(check.report)
        state.failed_pages.append(_failed_pages(result.text, check.report))
        extra = absorb_mentions(check.mentions, roster, offsets, source="verificacao")
        after = _tokenizable_state(roster)
        changed = sum(1 for key, value in after.items() if before.get(key) != value)
        if not changed and not extra:
            return
        state.report.new_in_verification += changed + len(extra)
        state.anchored.extend(extra)
        _entities, result, offsets = _tokenize(
            state.text, state.ids, roster, state.anchored, exclusions, state.folded, mapping
        )


def _guard(source: Path, errors: dict[Path, FileOutcome], step: Callable):
    """Erro de arquivo vira FileOutcome; erro de configuracao do LLM para o lote."""

    try:
        return step()
    except LlmConfigError:
        raise
    except UnsupportedDocumentError as exc:
        errors[source] = FileOutcome(source=source, ok=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a UI precisa da mensagem por arquivo
        errors[source] = FileOutcome(
            source=source, ok=False, error=f"{type(exc).__name__}: {exc}"
        )
    return None


def _is_remote(discoverer: Discoverer) -> bool:
    config = getattr(discoverer, "config", None)
    return getattr(config, "provider", "") != "ollama"


def _append_roster_guide(path: Path) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n"
            "elenco.json: quem e quem neste lote (com os originais), o papel que o\n"
            "LLM atribuiu e a acao (tokenizar/manter). Para corrigir, troque 'acao'\n"
            "e rode de novo com --elenco apontando para este arquivo.\n"
        )


def _tokenize(
    text: str,
    ids: Sequence[EntitySpan],
    roster: Roster,
    anchored: Sequence[tuple[int, int, str]],
    exclusions: Sequence[str],
    folded: FoldedText,
    mapping: PseudonymMap,
) -> tuple[list[EntitySpan], PseudonymizationResult, OffsetMap]:
    spans = spans_for_roster(
        text,
        roster.entries,
        anchored=anchored,
        extra_keep=exclusions,
        folded=folded,
    )
    entities = resolve_overlaps([*ids, *spans])
    result, offsets = pseudonymize_with_offsets(text, entities, mapping)
    return entities, result, offsets


def pseudonymize_with_offsets(
    text: str,
    entities: Sequence[EntitySpan],
    mapping: PseudonymMap,
) -> tuple[PseudonymizationResult, OffsetMap]:
    result = pseudonymize(text, list(entities), mapping=mapping)
    new_starts: list[int] = []
    new_ends: list[int] = []
    orig_ends: list[int] = []
    shift = 0
    for entity in sorted(entities, key=lambda item: item.start):
        token_start = entity.start + shift
        token_end = result.text.index("]", token_start) + 1
        new_starts.append(token_start)
        new_ends.append(token_end)
        orig_ends.append(entity.end)
        shift = token_end - entity.end
    return result, OffsetMap(tuple(new_starts), tuple(new_ends), tuple(orig_ends))


def absorb_mentions(
    mentions: Iterable[Mention],
    roster: Roster,
    offsets: OffsetMap,
    *,
    source: str,
) -> list[tuple[int, int, str]]:
    """Leva as mencoes ao elenco; devolve as ancoras (no texto original)."""

    anchored: list[tuple[int, int, str]] = []
    for mention in mentions:
        entry = roster.add_mention(mention, source=source)
        if entry is None or not mention.anchored:
            continue
        original = offsets.to_original(mention.start, mention.end)
        if original is not None:
            anchored.append((original[0], original[1], entry.key))
    return anchored


def roster_summary(roster: Roster) -> list[str]:
    """Contagens do elenco por tipo, acao e papel. Nao copia nome."""

    if not len(roster):
        return ["Elenco vazio: o LLM nao apontou pessoa, empresa nem endereco."]
    by_kind = Counter((entry.kind, entry.action) for entry in roster.entries)
    roles = Counter(role for entry in roster.entries for role in entry.roles)
    pinned = sum(1 for entry in roster.entries if entry.pinned)
    lines = [
        "Entidades: "
        + ", ".join(
            f"{kind} {by_kind.get((kind, TOKENIZE), 0)} tokenizadas / "
            f"{by_kind.get((kind, KEEP), 0)} mantidas"
            for kind in ("pessoa", "empresa", "endereco")
        ),
        "Papeis: " + ", ".join(f"{role} x{count}" for role, count in sorted(roles.items())),
    ]
    if pinned:
        lines.append(f"Decisoes do usuario aplicadas: {pinned}")
    return lines


def _tokenizable_state(roster: Roster) -> dict[str, tuple[str, ...]]:
    return {
        entry.key: tuple(sorted(entry.variants))
        for entry in roster.entries
        if entry.action == TOKENIZE
    }


def _failed_pages(text: str, report: DiscoveryReport) -> set[int]:
    pages: set[int] = set()
    for start, end in report.failed:
        first, last = page_of(text, start), page_of(text, max(start, end - 1))
        pages.update(range(first, last + 1))
    return pages


def _pages_label(pages: Sequence[int]) -> str:
    if pages == [0]:
        return "trechos sem ancora de pagina"
    return "paginas " + ", ".join(str(page) for page in pages[:20]) + (
        "..." if len(pages) > 20 else ""
    )


def _file_notes(
    outcome: FileOutcome,
    report: LlmFileReport,
    discoverer: Discoverer,
    text: str,
) -> list[str]:
    discovery = report.discovery
    notes = [
        f"Extrator: {outcome.extractor} ({outcome.page_count} pagina(s))",
        f"LLM: {discoverer.describe()}",
        (
            f"Descoberta: {discovery.chunks} trecho(s), {discovery.cached} do cache, "
            f"{discovery.sent} enviado(s), {len(discovery.failed)} com falha, "
            f"{discovery.ungrounded} mencao(oes) sem ancora no texto"
        ),
    ]
    for number, check in enumerate(report.verification, start=1):
        notes.append(
            f"Verificacao {number}: {check.chunks} trecho(s), {check.cached} do cache, "
            f"{len(check.failed)} com falha"
        )
    notes.append(f"Entidades acrescentadas pela verificacao: {report.new_in_verification}")
    reasons = sorted(
        {reason for item in (discovery, *report.verification) for reason in item.failure_reasons}
    )
    if reasons:
        notes.append("Motivos de falha: " + " | ".join(reasons[:5]))
    if report.unverified:
        notes.append(
            "ALERTA: " + _pages_label(report.unread_pages) + " sem leitura do LLM em "
            "nenhuma passada. Exportacao bloqueada."
        )
    notes.extend(audit_markdown(text))
    return notes


def _token_order(token: str) -> tuple[str, int]:
    label, _, number = token.strip("[]").rpartition("_")
    return label, int(number) if number.isdigit() else 0


def _write_legend(export_dir: Path, roster: Roster, mapping: PseudonymMap) -> Path | None:
    """Legenda exportavel: token, tipo e papel. Sem nome, sem variante."""

    tokens = {record.canonical: record.token for record in mapping.records}
    rows = []
    for entry in roster.to_tokenize():
        token = tokens.get(entry.key)
        if token is None:
            continue
        roles = ", ".join(role for role in entry.roles if role != "desconhecido") or "-"
        rows.append((token, entry.kind, roles))
    if not rows:
        return None
    rows.sort(key=lambda row: _token_order(row[0]))
    lines = [
        "# Legenda dos tokens",
        "",
        "Papel processual de cada token deste lote. Nao ha nome nem documento aqui.",
        "",
        "| Token | Tipo | Papel |",
        "|---|---|---|",
        *(f"| {token} | {kind} | {roles} |" for token, kind, roles in rows),
    ]
    path = export_dir / LEGEND_FILENAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

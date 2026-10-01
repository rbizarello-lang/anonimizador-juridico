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

A saida tem a mesma estrutura do motor local: ancora visivel por folha,
``INDICE.md`` e ``ELENCO.md`` (capas, vinculos, socios dos contratos), o sufixo de grupo
das empresas ligadas (``[EMPRESA_3·G1]``, ``lote_elenco.apply_company_groups``), a trava do
IBGE para empresa de uma palavra (``geo_names``) e a matricula por cartorio (que vem do
``StructuredDetector``, o mesmo do motor local). O
``ELENCO.md`` ganha, no fim, a secao dos papeis atribuidos pelo LLM, com a
contagem de mencoes de cada token. A linha de versao (AUDITORIA, LEIA-ME)
diz o motor: ``motor LLM: <canal>``.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import re
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
from .geo_names import geo_context, is_geo_word
from .llm_discovery import Discoverer, DiscoveryReport, LlmConfigError, page_of
from .lote_elenco import CompanyGroup, apply_company_groups, apply_groups
from .lote_index import add_sheet_anchors
from .pipeline import (
    PRIVATE_AUDIT_NAME,
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
    write_outcomes_index,
)
from .propagation import RECOGNIZER as ROSTER_RECOGNIZER
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
    # Por chave do elenco: mencoes trocadas (ou, nas entradas mantidas, achadas), e token.
    occurrences: dict[str, int] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    engine: str = ""


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
    engine: str | None = None,
    engine_notes: Sequence[str] = (),
    job_id: str | None = None,
    on_progress: ProgressFn | None = None,
) -> LlmJobResult:
    """Tres fases: descobre em todos os arquivos, confere, e so entao troca.

    O elenco e do processo inteiro, nao do arquivo: socio que so aparece com
    nome completo na PARTE_2 tambem e trocado na PARTE_1.
    """

    sources = _dedupe_paths(files)
    if not sources:
        raise ValueError("Nenhum arquivo foi informado.")

    engine_id = engine or getattr(getattr(discoverer, "config", None), "channel", "") or "llm"
    identifier = job_id or _unique_job_id(Path(export_root), Path(private_root))
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
    written: list[tuple[FileOutcome, _FileState]] = []
    occurrences: Counter[str] = Counter()
    refused_brands: Counter[tuple[str, str]] = Counter()
    for source in sources:
        if source in errors:
            outcomes.append(errors[source])
            continue
        state = next(item for item in states if item.source == source)
        # Troca final com o elenco completo do lote (so acrescenta tokens).
        entities, result, _offsets, refused = _tokenize(
            state.text, state.ids, roster, state.anchored, exclusions, state.folded, mapping
        )
        refused_brands.update(refused)
        state.report.unread_pages = sorted(set.intersection(*state.failed_pages))
        markdown_path = _unique_path(export_dir, source.stem, ".md")
        # Ancora visivel por folha, depois da troca (igual ao motor local).
        final_text = add_sheet_anchors(result.text, markdown_path.name)
        markdown_path.write_text(final_text, encoding="utf-8")
        occurrences.update(
            entity.canonical_key
            for entity in entities
            if entity.recognizer == ROSTER_RECOGNIZER and entity.canonical_key in roster
        )
        occurrences.update(_kept_occurrences(state, roster, exclusions))
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
        written.append((outcome, state))

    # Empresas ligadas por vinculo declarado levam o sufixo de grupo ([EMPRESA_3·G1]), como no motor local:
    # depois da troca de todos os arquivos e antes da AUDITORIA, do INDICE e do ELENCO (que leem o sufixo).
    groups = apply_company_groups([outcome.markdown_path for outcome, _state in written])
    notes: list[tuple[str, list[str]]] = []
    for outcome, state in written:
        final_text = outcome.markdown_path.read_text(encoding="utf-8")
        notes.append(
            (outcome.markdown_path.name, _file_notes(outcome, state.report, discoverer, final_text))
        )

    mapping.dump(map_path)
    tokens = {record.canonical: record.token for record in mapping.records}
    roster_file = roster.dump(
        map_path.parent / ROSTER_FILENAME,
        annotations={
            entry.key: {
                "token": tokens.get(entry.key, ""),
                "ocorrencias": occurrences.get(entry.key, 0),
            }
            for entry in roster.entries
        },
    )
    write_outcomes_index(export_dir, outcomes)
    legend_path = (
        _append_llm_roles(export_dir, roster, tokens, occurrences, groups) if write_legend else None
    )
    write_llm_private_audit(map_path.parent, refused_brands)
    notes.insert(0, ("elenco (sem nomes)", roster_summary(roster)))
    notes.insert(1, ("marca de uma palavra (so contagens)", brand_guard_lines(refused_brands)))
    notes.insert(0, ("mapa.json", audit_map_records(mapping.records)))
    notes.insert(0, ("motor", [f"Motor LLM: {engine_id}", *engine_notes]))
    write_audit_report(export_dir, notes, engine=engine_id)
    write_lote_guides(
        job_id=identifier,
        export_dir=export_dir,
        map_path=map_path,
        markdown_names=[
            outcome.markdown_path.name
            for outcome in outcomes
            if outcome.ok and outcome.markdown_path is not None
        ],
        engine=engine_id,
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
        occurrences=dict(occurrences),
        tokens=tokens,
        engine=engine_id,
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

    _entities, result, offsets, _refused = _tokenize(
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
        _entities, result, offsets, _refused = _tokenize(
            state.text, state.ids, roster, state.anchored, exclusions, state.folded, mapping
        )


def _unique_job_id(export_root: Path, private_root: Path) -> str:
    """``lote_AAAAMMDD_HHMMSS``; nova rodada no mesmo segundo nao sobrescreve a anterior."""

    base = new_job_id()
    candidate, index = base, 2
    while (export_root / candidate).exists() or (private_root / candidate).exists():
        candidate = f"{base}_{index}"
        index += 1
    return candidate


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
    """Os dois canais (Gemini API paga e Vertex AI) mandam o texto ao provedor."""

    return True


def _append_roster_guide(path: Path) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n"
            "elenco.json: quem e quem neste lote (com os originais), o papel que o\n"
            "LLM atribuiu e a acao (tokenizar/manter). Para corrigir, troque 'acao'\n"
            "e rode de novo com --elenco apontando para este arquivo (com --sem-rede,\n"
            "so o cache e usado: nada e enviado ao provedor).\n"
        )


def _tokenize(
    text: str,
    ids: Sequence[EntitySpan],
    roster: Roster,
    anchored: Sequence[tuple[int, int, str]],
    exclusions: Sequence[str],
    folded: FoldedText,
    mapping: PseudonymMap,
) -> tuple[list[EntitySpan], PseudonymizationResult, OffsetMap, list[tuple[str, str]]]:
    spans = spans_for_roster(
        text,
        roster.entries,
        anchored=anchored,
        extra_keep=exclusions,
        folded=folded,
    )
    spans, refused = drop_geo_brand_spans(text, spans)
    entities = resolve_overlaps([*ids, *spans])
    result, offsets = pseudonymize_with_offsets(text, entities, mapping)
    return entities, result, offsets, refused


_SINGLE_WORD = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*")
_COMPANY_TAIL_WORDS = frozenset({"ltda", "me", "epp", "eireli", "sa", "s", "a"})


def _brand_word(value: str) -> str:
    """A unica palavra de conteudo do trecho (sem sufixo societario), ou "" se ha mais de uma."""

    words = [
        word for word in _SINGLE_WORD.findall(value) if word.casefold() not in _COMPANY_TAIL_WORDS
    ]
    return words[0] if len(words) == 1 else ""


def drop_geo_brand_spans(
    text: str, spans: Sequence[EntitySpan]
) -> tuple[list[EntitySpan], list[tuple[str, str]]]:
    """Mesma trava do motor local (B3, ``geo_names``): empresa de UMA palavra que e nome de municipio ou
    de estado (IBGE), ou que aparece em contexto de foro ou de endereco, nao vira token. Vale para a
    mencao de uma palavra que o LLM apontou ("Joinville" num "Vara Federal de Joinville"); nome de
    2+ palavras nao passa por aqui. Devolve os spans mantidos e, dos recusados, (palavra, motivo)."""

    kept: list[EntitySpan] = []
    refused: list[tuple[str, str]] = []
    for span in spans:
        if span.recognizer == ROSTER_RECOGNIZER and span.entity_type == "ORGANIZATION":
            word = _brand_word(text[span.start : span.end])
            if word:
                if is_geo_word(word):
                    refused.append((word, "municipio ou estado"))
                    continue
                if geo_context(text, span.start, span.end):
                    refused.append((word, "contexto de foro ou endereco"))
                    continue
        kept.append(span)
    return kept, refused


def brand_guard_lines(refused: Counter[tuple[str, str]]) -> list[str]:
    """Contagens, sem a palavra (a AUDITORIA exportavel nao leva nome)."""

    if not refused:
        return [
            "Nenhuma mencao de uma palavra recusada por municipio, estado ou contexto de foro/endereco."
        ]
    by_reason: Counter[str] = Counter()
    for (_word, reason), count in refused.items():
        by_reason[reason] += count
    return [
        f"Mencao de uma palavra de empresa recusada ({reason}): {count} ocorrencia(s)"
        for reason, count in sorted(by_reason.items())
    ]


def write_llm_private_audit(private_dir: Path, refused: Counter[tuple[str, str]]) -> Path:
    """``AUDITORIA_PRIVADA.txt`` do lote LLM: as palavras recusadas, para o Operador conferir.
    Fica ao lado do ``mapa.json``, nunca na pasta dos textos."""

    lines = [
        "AUDITORIA PRIVADA DESTE LOTE (motor LLM)",
        "Contem palavras reais do caso. NAO ENVIAR: fica com o mapa, fora da pasta dos textos.",
        "",
        "## Mencao de uma palavra de empresa recusada (municipio, estado ou contexto de foro/endereco)",
    ]
    if refused:
        lines.extend(
            f"- {word}: {count} ocorrencia(s), motivo: {reason}"
            for (word, reason), count in sorted(refused.items(), key=lambda item: (-item[1], item[0]))
        )
    else:
        lines.append("- nenhuma")
    lines.append("")
    path = Path(private_dir) / PRIVATE_AUDIT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


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
    if getattr(discoverer, "offline", False):
        notes.append(
            "Modo sem rede: nenhuma chamada ao provedor; trecho fora do cache ficou sem leitura "
            "nesta rodada."
        )
    notes.extend(audit_markdown(text))
    return notes


def _kept_occurrences(
    state: _FileState, roster: Roster, exclusions: Sequence[str]
) -> Counter[str]:
    """Mencoes das entradas MANTIDAS (a tela de revisao mostra quanto a troca atingiria)."""

    kept = [replace(entry, action=TOKENIZE) for entry in roster.entries if entry.action == KEEP]
    if not kept:
        return Counter()
    spans = spans_for_roster(state.text, kept, extra_keep=exclusions, folded=state.folded)
    return Counter(span.canonical_key for span in spans)


def _token_order(token: str) -> tuple[str, int]:
    label, _, number = token.strip("[]").rpartition("_")
    return label, int(number) if number.isdigit() else 0


def _append_llm_roles(
    export_dir: Path,
    roster: Roster,
    tokens: dict[str, str],
    occurrences: Counter[str],
    groups: Sequence[CompanyGroup] = (),
) -> Path | None:
    """Secao "Papeis atribuidos pelo LLM" no fim do ``ELENCO.md``: token, tipo, papel, mencoes.

    Sem nome, sem variante. O ``ELENCO.md`` (capas e vinculos) ja foi gravado pelo mesmo
    gerador do motor local; aqui so se acrescenta o que o LLM classificou.
    """

    rows = []
    for entry in roster.to_tokenize():
        token = tokens.get(entry.key)
        if token is None:
            continue
        roles = ", ".join(role for role in entry.roles if role != "desconhecido") or "-"
        rows.append((token, entry.kind, roles, occurrences.get(entry.key, 0)))
    if not rows:
        return None
    rows.sort(key=lambda row: _token_order(row[0]))
    lines = [
        "## Papéis atribuídos pelo LLM",
        "",
        "Papel processual de cada token deste lote, como o LLM o classificou; menções = quantas "
        "vezes o token aparece nos textos. Não há nome nem documento aqui.",
        "",
        "| Token | Tipo | Papel | Menções |",
        "|---|---|---|---|",
        *(f"| {token} | {kind} | {roles} | {count} |" for token, kind, roles, count in rows),
    ]
    path = export_dir / LEGEND_FILENAME
    existing = path.read_text(encoding="utf-8").rstrip("\n") if path.is_file() else ""
    # O token de empresa de um grupo leva o sufixo tambem aqui, como no resto do ELENCO.md.
    body = apply_groups("\n".join(lines) + "\n", groups)
    path.write_text((existing + "\n\n" if existing else "") + body, encoding="utf-8")
    return path

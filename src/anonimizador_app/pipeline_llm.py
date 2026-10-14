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

Fail-closed (B1/I6): pagina que o LLM nao leu (falha, bloqueio, trecho fora do
cache sem rede) ou para a qual respondeu SEM nenhuma entidade passa pelo motor
local (NER mais reconhecedores, o mesmo do motor padrao): nunca sai com nome em
claro por causa de falha do LLM. As folhas tratadas assim ficam na AUDITORIA, no
LEIA-ME e no resultado (``fallback_pages``); a CLI sai com codigo diferente de
zero se houve alguma, salvo ``--aceitar-fallback``. Se o motor local tambem nao
puder tratar a pagina, o arquivo NAO vai para a pasta exportavel: fica em
``quarentena`` na pasta privada. Resposta vazia ou falha nunca entra no cache.

Piso local (I-N4, ligado por padrao; ``--piso-local`` na CLI, ``llm_local_floor`` nas configuracoes):
o motor local roda em TODAS as folhas e o resultado final e a UNIAO do que o LLM e o motor local
apontaram. O LLM so acrescenta protecao: omissao parcial (o LLM acha o executado da folha 1 e esquece
o socio da folha 2) ou mencao sem ancora no texto nao deixam nome em claro. A AUDITORIA e o LEIA-ME
dizem se o piso estava ligado e quantas entidades ele acrescentou fora das folhas suspeitas.
H9-c: a mencao do LLM passa por higiene (``llm_hygiene``: classe, ancora, minusculas, termo protegido, rotulo
com numero, termo juridico, lugar, pessoa de uma palavra, token colado em numero) e as entradas dele passam pelo
juizo do lote (agente publico, banco do SISBAJUD, rua com nome de pessoa) antes da troca. So mencao do proprio
LLM e descartada; nada do motor local. A AUDITORIA leva so as contagens.
O elenco com originais fica em ``privados``; a legenda exportavel (token -> tipo ->
papel) nao leva nome.

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

import re
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
from .batch_propagation import BatchRoster
from .coverage import unread_pages as extraction_unread_pages
from .extraction_cache import CachedExtraction, default_cache_dir, load_or_extract
from .extractors import ExtractedDocument, UnsupportedDocumentError
from .fast_extract import FAST_EXTRACTOR_VERSION, extract_fast
from .contextual import build_contextual_engine
from .fidelity import FidelityDropStats, drop_protected_spans
from .gemini_detector import ContextualEngine
from .geo_names import geo_context, is_geo_word
from .llm_discovery import Discoverer, DiscoveryReport, LlmConfigError, page_of, page_ranges, split_chunks
from .local_ner import reset_ner_status
from .netblock import block_network
from .llm_gate import FilePlan, GateError, SendPlan, safe_error, scrub_secrets
from .llm_hygiene import LlmHygieneStats, filter_llm_mentions, role_counts
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
from .ner_cache import default_ner_cache_dir
from .propagation import RECOGNIZER as ROSTER_RECOGNIZER
from .propagation import FoldedText, fold_with_index, spans_for_roster
from .roster import KEEP, TOKENIZE, Mention, Roster
from .rules import build_recognizers
from .settings import AppSettings
from .version import ENGINE_LOCAL
from .span_hygiene import sanitize_spans

LLM_CACHE_SUBDIR = "cache_llm"
ROSTER_FILENAME = "elenco.json"
LEGEND_FILENAME = "ELENCO.md"
QUARANTINE_DIR = "quarentena"
LOCAL_FALLBACK_SOURCE = "motor_local"


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
    # B1/I6: folhas que o motor local tratou por falha ou resposta vazia do LLM.
    fallback_pages: list[int] = field(default_factory=list)
    # Resposta vazia do LLM que o motor local confirmou sem nome (nada a acrescentar).
    empty_confirmed_pages: list[int] = field(default_factory=list)
    fallback_entities: int = 0
    # Preenchido quando o motor local nao pode tratar as folhas suspeitas: o arquivo fica retido.
    fallback_error: str = ""
    # Piso local (I-N4): o motor local rodou em todas as folhas; entidades que ele acrescentou FORA das
    # folhas suspeitas (onde o LLM leu e respondeu com entidades). ``floor_error``: o piso estava ligado
    # mas o motor local nao rodou (so aviso: as folhas suspeitas, se houver, seguem o fail-closed acima).
    floor_entities: int = 0
    floor_error: str = ""
    # Ocorrencias que o motor local apontou e o juizo do lote deixou fora do elenco, como no motor local:
    # nome em contexto de juiz ou procurador, instituicao do SISBAJUD.
    local_kept: int = 0

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
    # Por arquivo, as folhas tratadas pelo motor local por falha do LLM (vazio = nenhuma).
    fallback_pages: dict[str, list[int]] = field(default_factory=dict)
    # Arquivos retidos na quarentena (privados) porque nem o motor local tratou as folhas.
    held: tuple[str, ...] = ()
    # Piso local ligado nesta rodada (o motor local rodou em todas as folhas e o resultado e a uniao).
    local_floor: bool = False


def run_local_job(files: Sequence[str | Path], **kwargs) -> LlmJobResult:
    """Pipeline unico sem LLM: so o motor local acha os nomes, e o resto (elenco, troca, AUDITORIA, ELENCO)
    e o mesmo do motor LLM. Com isso a deteccao e a unica diferenca entre os dois modos (H5)."""

    return _run_llm_job(files, discoverer=None, **kwargs)


def run_llm_job(files: Sequence[str | Path], *, discoverer: Discoverer, **kwargs) -> LlmJobResult:
    """Tres fases: descobre em todos os arquivos, confere, e so entao troca (ver ``_run_llm_job``).

    A confirmacao da rodada (``discoverer.permit``, ver ``llm_gate.RunPermit``) vale so enquanto esta funcao
    roda: ela e ativada aqui e expira no fim, com ou sem erro. Descobridor sem confirmacao (o modo sem
    rede, que so le o cache, ou um falso de teste) segue sem ela; o que chama o provedor exige a
    confirmacao no ponto da rede (``LangExtractDiscoverer``)."""

    permit = getattr(discoverer, "permit", None)
    if permit is not None:
        try:
            permit.start()
        except GateError as exc:
            raise LlmConfigError(str(exc)) from None
    try:
        if _is_local_llm(discoverer):
            return _run_local_llm_job(files, discoverer=discoverer, **kwargs)
        return _run_llm_job(files, discoverer=discoverer, **kwargs)
    finally:
        if permit is not None:
            permit.finish()


def _is_local_llm(discoverer: object) -> bool:
    """Canal local (H9-b): o Ollama em loopback. Nada sai da maquina; nao tem confirmacao de rodada."""

    return getattr(getattr(discoverer, "config", None), "provider", "") == "ollama"


def _run_local_llm_job(files: Sequence[str | Path], *, discoverer: Discoverer, **kwargs) -> LlmJobResult:
    """Rodada do canal local DENTRO do bloqueio de rede (H9-b): so o loopback abre socket; qualquer outra
    tentativa (conexao, resolucao de nome, datagrama) e barrada e contada. A lista viva vai para o
    descobridor, que a AUDITORIA le ao ser escrita; ao fim fica o total em ``blocked_attempts``.

    O bloqueio troca as funcoes de ``socket`` do processo inteiro (todas as threads) enquanto dura."""

    with block_network(allow_loopback=True) as attempts:
        discoverer.attempts_live = attempts  # type: ignore[attr-defined]
        try:
            return _run_llm_job(files, discoverer=discoverer, **kwargs)
        finally:
            discoverer.blocked_attempts = len(attempts)  # type: ignore[attr-defined]
            discoverer.attempts_live = None  # type: ignore[attr-defined]


def _run_llm_job(
    files: Sequence[str | Path],
    *,
    export_root: Path,
    private_root: Path,
    discoverer: Discoverer | None,
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
    fallback_engine: ContextualEngine | None = None,
    local_floor: bool | None = None,
) -> LlmJobResult:
    """Tres fases: descobre em todos os arquivos, confere, e so entao troca.

    O elenco e do processo inteiro, nao do arquivo: socio que so aparece com
    nome completo na PARTE_2 tambem e trocado na PARTE_1.

    ``fallback_engine``: o motor local (``ContextualEngine``) que trata a folha que o LLM nao leu ou
    para a qual respondeu vazio. Sem ele, e criado o mesmo do motor padrao (``build_contextual_engine``),
    so se alguma folha precisar (ou se o piso local estiver ligado).

    ``local_floor``: piso local (I-N4). ``None`` = o que as configuracoes dizem (``llm_local_floor``, ligado
    por padrao). Ligado, o motor local roda em TODAS as folhas e o resultado e a uniao com o LLM.

    ``discoverer=None``: pipeline unico sem LLM (``run_local_job``). Nada e enviado; o motor local roda em
    todas as folhas e e a unica fonte de nomes; nao ha folha suspeita, verificacao nem papel do LLM.
    """

    sources = _dedupe_paths(files)
    if not sources:
        raise ValueError("Nenhum arquivo foi informado.")

    local_only = discoverer is None
    engine_id = engine or (
        ENGINE_LOCAL if local_only else getattr(getattr(discoverer, "config", None), "channel", "") or "llm"
    )
    identifier = job_id or _unique_job_id(Path(export_root), Path(private_root))
    export_dir, map_path, split_warning = resolve_lote_dirs(
        Path(export_root), Path(private_root), identifier
    )
    export_dir.mkdir(parents=True, exist_ok=True)
    map_path.parent.mkdir(parents=True, exist_ok=True)

    active = settings or AppSettings()
    reset_ner_status()
    floor = local_only or bool(active.llm_local_floor if local_floor is None else local_floor)
    exclusions = tuple(active.exclusions)
    detector = StructuredDetector(recognizers=build_recognizers(active))
    mapping = PseudonymMap(token_labels=active.token_labels())
    roster = Roster.load(roster_path) if roster_path else Roster()
    for name in active.target_companies:
        roster.add_listed("empresa", name)
    for name in active.target_people:
        roster.add_listed("pessoa", name)
    cache_dir = default_cache_dir(Path(private_root)) if use_cache else None
    local_engine = _LazyEngine(
        fallback_engine,
        lambda: build_contextual_engine(
            active,
            use_ner_cache=True,
            ner_cache_dir=default_ner_cache_dir(Path(private_root)),
        ),
    )

    total = len(sources)
    states: list[_FileState] = []
    errors: dict[Path, FileOutcome] = {}
    # H9-c: contagens da higiene das mencoes do LLM (AUDITORIA, so numeros).
    hygiene = LlmHygieneStats()
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
                hygiene=hygiene,
            ),
        )
        if state is not None:
            states.append(state)

    for index, state in enumerate(states):
        if not state.suspect_pages and not floor:
            continue
        _report(
            on_progress,
            (
                f"Piso local (motor local em todas as folhas) em {state.source.name} ({index + 1} de {len(states)})"
                if floor
                else f"Motor local nas folhas sem leitura do LLM em {state.source.name} ({index + 1} de {len(states)})"
            ),
            0.5 + 0.1 * index / max(1, len(states)),
        )
        _apply_local_fallback(state, engine=local_engine, exclusions=exclusions, floor=floor)
    batch = _review_local_names(states, roster=roster, exclusions=exclusions, hygiene=hygiene)

    for index, state in enumerate(states if not local_only else ()):
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
                batch=batch,
                hygiene=hygiene,
            ),
        )

    _report(on_progress, "Gravando textos, mapa e elenco", 0.95)
    outcomes: list[FileOutcome] = []
    reports: dict[str, LlmFileReport] = {}
    written: list[tuple[FileOutcome, _FileState]] = []
    occurrences: Counter[str] = Counter()
    refused_brands: Counter[tuple[str, str]] = Counter()
    held: list[str] = []
    for source in sources:
        if source in errors:
            outcomes.append(errors[source])
            continue
        state = next(item for item in states if item.source == source)
        # Troca final com o elenco completo do lote (so acrescenta tokens).
        entities, result, _offsets, refused = _tokenize(
            state.text, state.ids, roster, state.anchored, exclusions, state.folded, mapping, batch
        )
        refused_brands.update(refused)
        state.report.unread_pages = [
            page
            for page in sorted(set.intersection(*state.failed_pages))
            if page not in state.report.fallback_pages
            and page not in state.report.empty_confirmed_pages
        ]
        reports[source.name] = state.report
        if state.report.fallback_error:
            # Nem o motor local tratou as folhas que o LLM nao leu: o texto tem nome em claro e NAO vai
            # para a pasta exportavel (B1). Fica retido, na pasta privada do lote.
            held_path = _unique_path(map_path.parent / QUARANTINE_DIR, source.stem, ".md")
            held_path.parent.mkdir(parents=True, exist_ok=True)
            held_path.write_text(add_sheet_anchors(result.text, held_path.name), encoding="utf-8")
            held.append(source.name)
            outcomes.append(
                FileOutcome(
                    source=source,
                    ok=False,
                    error=(
                        "O LLM nao leu algumas folhas e o motor local nao pode trata-las ("
                        + state.report.fallback_error
                        + f"); o texto ficou retido em {QUARANTINE_DIR}/ na pasta privada, "
                        "nao na pasta exportavel."
                    ),
                )
            )
            continue
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
            gemini_used=not local_only,
            extraction_cached=state.cached.cached,
            extraction_note=state.cached.audit_line() or "",
            fidelity_drops=state.stats,
            unread_pages=extraction_unread_pages(state.cached.document.markdown),
            residual_hits=_residual_hits(local_engine.peek(), result.text),
        )
        outcomes.append(outcome)
        written.append((outcome, state))

    # Empresas ligadas por vinculo declarado levam o sufixo de grupo ([EMPRESA_3·G1]), como no motor local:
    # depois da troca de todos os arquivos e antes da AUDITORIA, do INDICE e do ELENCO (que leem o sufixo).
    groups = apply_company_groups([outcome.markdown_path for outcome, _state in written])
    notes: list[tuple[str, list[str]]] = []
    for outcome, state in written:
        final_text = outcome.markdown_path.read_text(encoding="utf-8")
        notes.append(
            (outcome.markdown_path.name, _file_notes(outcome, state.report, discoverer, final_text, floor))
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
        _append_llm_roles(export_dir, roster, tokens, occurrences, groups)
        if write_legend and not local_only
        else None
    )
    write_llm_private_audit(map_path.parent, refused_brands, batch=batch, mapping=mapping, local_only=local_only)
    notes.insert(0, ("elenco (sem nomes)", roster_summary(roster)))
    notes.insert(1, ("marca de uma palavra (so contagens)", brand_guard_lines(refused_brands)))
    if batch is not None:
        notes.insert(2, ("elenco do lote: marca de uma palavra (so tokens e contagens)", batch.brand_report(mapping)))
    notes.insert(0, ("mapa.json", audit_map_records(mapping.records)))
    fallback_by_file = {
        name: list(item.fallback_pages) for name, item in reports.items() if item.fallback_pages
    }
    fallback_notes = _fallback_lines(fallback_by_file, held)
    floor_notes = _local_only_lines(reports) if local_only else _floor_lines(floor, reports)
    guide_notes = [*floor_notes, *fallback_notes]
    secrets = _secrets_of(discoverer)
    network = getattr(discoverer, "network_check", None)
    if local_only:
        network_notes = ["Nenhuma chamada a provedor: nada saiu desta maquina."]
        engine_line = "Motor: local (pipeline unico, sem LLM)"
    else:
        network_notes = (
            network.audit_lines()
            if network is not None
            else ["Nenhuma chamada ao provedor nesta rodada (tudo veio do cache local ou nada foi enviado)."]
        )
        engine_line = f"Motor LLM: {engine_id}"
        # H6: chamadas, tokens (entrada, saida, raciocinio) e respostas cortadas; so contagens.
        network_notes = [*network_notes, *getattr(discoverer, "usage_lines", lambda: [])()]
        if _is_local_llm(discoverer):
            # H9-b: o canal local roda com a rede bloqueada fora do loopback; tem de dar 0.
            blocked = getattr(discoverer, "blocked_count", lambda: None)()
            network_notes.append(
                "LLM local (Ollama, loopback), nada enviado. Tentativas de rede fora de loopback "
                f"bloqueadas: {blocked if blocked is not None else 'rodada sem bloqueio de rede'}"
            )
    # H9-c: higiene das mencoes do LLM e papel publico (so contagens).
    hygiene_notes = [] if local_only else hygiene.audit_lines(*role_counts(roster))
    notes.insert(0, ("motor", [engine_line, *engine_notes, *network_notes, *hygiene_notes, *guide_notes]))
    # Defesa em profundidade: nem a chave nem nada com cara de chave entra na AUDITORIA.
    notes = [(title, [scrub_secrets(line, *secrets) for line in lines]) for title, lines in notes]
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
        notes=guide_notes,
    )
    _append_roster_guide(map_path.parent / "LEIA-ME.txt", local_only=local_only)
    job = build_job_result(
        job_id=identifier,
        export_dir=export_dir,
        map_path=map_path,
        outcomes=tuple(outcomes),
        record_count=len(mapping.records),
        contextual_used=True,
        gemini_used=not local_only,
        extra_warning=split_warning,
    )
    if fallback_by_file:
        warning = (
            "; ".join(fallback_notes) + " Confira essas folhas no texto antes de enviar."
        )
        job = replace(job, export_warning=(job.export_warning + " " + warning).strip())
    floor_failed = [name for name, item in reports.items() if item.floor_error]
    if local_only and floor_failed and job.export_allowed:
        # Sem LLM, o motor local e a unica fonte de nomes: se ele nao rodou, o texto so tem os IDs trocados.
        job = replace(
            job,
            export_allowed=False,
            export_block_reason=(
                "O motor local nao rodou em: " + ", ".join(floor_failed) + ". Sem ele os nomes ficariam em "
                "claro; confira a instalacao (AUDITORIA) e rode de novo antes de enviar."
            ),
        )
    elif floor and floor_failed:
        warning = (
            "O piso local estava ligado, mas o motor local nao rodou em: "
            + ", ".join(floor_failed)
            + ". Nessas folhas vale so a leitura do LLM; confira o texto antes de enviar."
        )
        job = replace(job, export_warning=(job.export_warning + " " + warning).strip())
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
        fallback_pages=fallback_by_file,
        held=tuple(held),
        local_floor=floor,
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
    # Folhas que a descoberta nao leu (falha) ou respondeu vazio; o motor local as confere.
    suspect_pages: set[int] = field(default_factory=set)
    failed_discovery: set[int] = field(default_factory=set)
    offline_discovery: bool = False
    # Nomes que o motor local achou (fase 1), a espera do juizo do lote (``_review_local_names``).
    local_names: list[EntitySpan] = field(default_factory=list)
    local_ran: bool = False
    local_pages: set[int] = field(default_factory=set)


def _extract_and_mask(
    source: Path,
    *,
    detector: StructuredDetector,
    mapping: PseudonymMap,
    exclusions: Sequence[str],
    use_cache: bool,
    cache_dir: Path | None,
    extract_fn: Callable[[Path], ExtractedDocument],
    extractor_version: str,
) -> tuple[CachedExtraction, str, FidelityDropStats, list[EntitySpan], PseudonymizationResult, OffsetMap]:
    """Extrai, acha os IDs aqui e mascara: o texto que (so ele) pode ir ao provedor."""

    cached = load_or_extract(
        source,
        cache_dir=cache_dir,
        use_cache=use_cache,
        version_string=extractor_version,
        extract_fn=extract_fn,
    )
    text = cached.document.markdown
    stats = FidelityDropStats()
    ids, masked, masked_offsets = mask_text(
        text, detector=detector, mapping=mapping, exclusions=exclusions, source_name=source.name, stats=stats
    )
    return cached, text, stats, ids, masked, masked_offsets


def mask_text(
    text: str,
    *,
    detector: StructuredDetector,
    mapping: PseudonymMap,
    exclusions: Sequence[str],
    source_name: str = "",
    stats: FidelityDropStats | None = None,
) -> tuple[list[EntitySpan], PseudonymizationResult, OffsetMap]:
    """Acha os IDs nesta maquina e os mascara: o texto que (so ele) pode ir ao LLM. Uma so definicao, usada
    pelo pipeline e pelo comparador de motores (H9-c), para que os dois leiam o mesmo texto e dividam o cache."""

    ids = detector.detect(text)
    ids = propagate_mapped_ids(text, ids, mapping=mapping)
    ids = resolve_overlaps(
        drop_protected_spans(text, ids, exclusions, source_name=source_name, stats=stats)
    )
    masked, masked_offsets = pseudonymize_with_offsets(text, ids, mapping)
    return ids, masked, masked_offsets


class LlmTextMasker:
    """O texto que o pipeline manda ao LLM, sem rodar o pipeline nem gravar nada: mesmo detector de IDs, mesmo
    ``settings`` e um ``PseudonymMap`` que vive entre os textos (como no lote), na ordem em que sao passados."""

    def __init__(self, settings: AppSettings | None = None) -> None:
        active = settings or AppSettings()
        self._detector = StructuredDetector(recognizers=build_recognizers(active))
        self._mapping = PseudonymMap(token_labels=active.token_labels())
        self._exclusions = tuple(active.exclusions)

    def mask(self, text: str, source_name: str = "") -> tuple[str, OffsetMap]:
        _ids, masked, offsets = mask_text(
            text,
            detector=self._detector,
            mapping=self._mapping,
            exclusions=self._exclusions,
            source_name=source_name,
        )
        return masked.text, offsets


def _discover_file(
    source: Path,
    *,
    detector: StructuredDetector,
    mapping: PseudonymMap,
    roster: Roster,
    discoverer: Discoverer | None,
    exclusions: Sequence[str],
    use_cache: bool,
    cache_dir: Path | None,
    extract_fn: Callable[[Path], ExtractedDocument],
    extractor_version: str,
    hygiene: LlmHygieneStats | None = None,
) -> _FileState:
    """Fase 1: extrai, acha os IDs aqui, mascara e manda o LLM descobrir o elenco."""

    cached, text, stats, ids, masked, masked_offsets = _extract_and_mask(
        source,
        detector=detector,
        mapping=mapping,
        exclusions=exclusions,
        use_cache=use_cache,
        cache_dir=cache_dir,
        extract_fn=extract_fn,
        extractor_version=extractor_version,
    )
    if discoverer is None:
        # Pipeline unico sem LLM: nada sai desta maquina; o motor local e a unica fonte de nomes.
        return _FileState(
            source=source,
            cached=cached,
            text=text,
            ids=ids,
            stats=stats,
            folded=fold_with_index(text),
            anchored=[],
            report=LlmFileReport(discovery=DiscoveryReport()),
            failed_pages=[set()],
        )
    discovery = discoverer.discover(masked.text)
    failed = _pages_of(masked.text, discovery.report.failed)
    empty = _pages_of(masked.text, discovery.report.empty)
    return _FileState(
        source=source,
        cached=cached,
        text=text,
        ids=ids,
        stats=stats,
        folded=fold_with_index(text),
        anchored=absorb_mentions(
            _clean_mentions(discovery.mentions, masked.text, roster, exclusions, hygiene),
            roster,
            masked_offsets,
            source="llm",
        ),
        report=LlmFileReport(discovery=discovery.report),
        failed_pages=[failed],
        suspect_pages=failed | empty,
        failed_discovery=failed,
        offline_discovery=bool(getattr(discovery.report, "offline", False)),
    )


def plan_send(
    files: Sequence[str | Path],
    *,
    private_root: Path,
    discoverer: Discoverer,
    settings: AppSettings | None = None,
    verification_rounds: int = 1,
    use_cache: bool = True,
    extract_fn: Callable[[Path], ExtractedDocument] = extract_fast,
    extractor_version: str = FAST_EXTRACTOR_VERSION,
    on_progress: ProgressFn | None = None,
) -> SendPlan:
    """Previa do que a rodada manda ao provedor: por arquivo, o texto JA MASCARADO que seria enviado
    (caracteres, trechos, quantos ja estao no cache local). Extrai (com o cache de extracao) e acha os
    IDs, nesta maquina; nao fala com ninguem. E o que a confirmacao da rodada mostra ao Operador."""

    sources = _dedupe_paths(files)
    if not sources:
        raise ValueError("Nenhum arquivo foi informado.")
    active = settings or AppSettings()
    detector = StructuredDetector(recognizers=build_recognizers(active))
    mapping = PseudonymMap(token_labels=active.token_labels())
    cache_dir = default_cache_dir(Path(private_root)) if use_cache else None
    exclusions = tuple(active.exclusions)
    planner = getattr(discoverer, "plan", None)
    plans: list[FilePlan] = []
    errors: list[tuple[str, str]] = []
    for index, source in enumerate(sources):
        _report(on_progress, f"Preparando {source.name} ({index + 1} de {len(sources)})", index / len(sources))
        try:
            cached, _text, _stats, _ids, masked, _offsets = _extract_and_mask(
                source,
                detector=detector,
                mapping=mapping,
                exclusions=exclusions,
                use_cache=use_cache,
                cache_dir=cache_dir,
                extract_fn=extract_fn,
                extractor_version=extractor_version,
            )
        except LlmConfigError:
            raise
        except UnsupportedDocumentError as exc:
            errors.append((source.name, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - a confirmacao lista o arquivo como nao enviado
            errors.append((source.name, type(exc).__name__))
            continue
        if planner is not None:
            chunks, chars, cached_chunks, pending_chars = planner(masked.text)
        else:
            pieces = split_chunks(masked.text, 4000)
            chunks, chars = len(pieces), sum(len(piece.text) for piece in pieces)
            cached_chunks, pending_chars = 0, chars
        plans.append(
            FilePlan(
                name=source.name,
                pages=cached.document.page_count,
                chars=chars,
                chunks=chunks,
                cached_chunks=cached_chunks,
                pending_chars=pending_chars,
            )
        )
    return SendPlan(
        files=tuple(plans),
        verification_rounds=max(0, verification_rounds),
        errors=tuple(errors),
    )


def _verify_file(
    state: _FileState,
    *,
    roster: Roster,
    discoverer: Discoverer,
    mapping: PseudonymMap,
    exclusions: Sequence[str],
    rounds: int,
    batch: BatchRoster | None = None,
    hygiene: LlmHygieneStats | None = None,
) -> None:
    """Fase 2: o LLM le o texto ja tokenizado; o que ainda identifica entra no elenco."""

    _entities, result, offsets, _refused = _tokenize(
        state.text, state.ids, roster, state.anchored, exclusions, state.folded, mapping, batch
    )
    for _round in range(max(0, rounds)):
        before = _tokenizable_state(roster)
        check = discoverer.discover(result.text)
        state.report.verification.append(check.report)
        state.failed_pages.append(_pages_of(result.text, check.report.failed))
        extra = absorb_mentions(
            _clean_mentions(check.mentions, result.text, roster, exclusions, hygiene),
            roster,
            offsets,
            source="verificacao",
        )
        after = _tokenizable_state(roster)
        changed = sum(1 for key, value in after.items() if before.get(key) != value)
        if not changed and not extra:
            return
        state.report.new_in_verification += changed + len(extra)
        state.anchored.extend(extra)
        _entities, result, offsets, _refused = _tokenize(
            state.text, state.ids, roster, state.anchored, exclusions, state.folded, mapping, batch
        )


class _LazyEngine:
    """O motor local (NER mais reconhecedores) so e criado se alguma folha precisar."""

    def __init__(self, engine: ContextualEngine | None, factory: Callable[[], ContextualEngine | None]) -> None:
        self._engine = engine
        self._factory: Callable[[], ContextualEngine | None] | None = None if engine is not None else factory

    def get(self) -> ContextualEngine | None:
        if self._engine is None and self._factory is not None:
            factory, self._factory = self._factory, None
            self._engine = factory()
        return self._engine

    def peek(self) -> ContextualEngine | None:
        """O motor, se ja foi criado; nunca o cria."""

        return self._engine


_NAME_KINDS = {"PERSON": "pessoa", "ORGANIZATION": "empresa", "LOCATION": "endereco"}


def _apply_local_fallback(
    state: _FileState,
    *,
    engine: _LazyEngine,
    exclusions: Sequence[str],
    floor: bool = False,
) -> None:
    """B1/I6 e I-N4: o motor local confere as folhas em que o LLM falhou ou respondeu sem entidade e,
    com o piso local ligado (``floor``), TODAS as folhas: o resultado e a uniao do LLM com o motor local.

    Fase 1, por arquivo: roda o motor local e filtra (fidelidade e higiene). O que nao e nome entra
    direto como ID; os nomes esperam o juizo do lote (``_review_local_names``), o mesmo do motor local.
    Se o motor local nao puder rodar, ``fallback_error`` retem o arquivo (so se ha folha suspeita); sem
    folha suspeita, o piso que nao rodou fica em ``floor_error`` (aviso).
    """

    report = state.report

    def unavailable(reason: str) -> None:
        if state.suspect_pages:
            report.fallback_error = reason
        else:
            report.floor_error = reason

    try:
        local = engine.get()
        if local is None:
            unavailable("motor local indisponivel")
            return
        found = local.detect(state.text)
    except Exception as exc:  # noqa: BLE001 - nao pode derrubar o lote nem vazar texto na mensagem
        unavailable(f"motor local falhou ({type(exc).__name__})")
        return
    in_zone = _suspect_zone(state)
    found = resolve_overlaps(found)
    found = drop_protected_spans(
        state.text, found, exclusions, source_name=state.source.name, stats=state.stats
    )
    found = sanitize_spans(state.text, found, stats=state.stats)
    if not floor:
        found = [span for span in found if in_zone(span.start)]

    known_ids = [(span.start, span.end) for span in state.ids]
    for span in found:
        if span.entity_type in _NAME_KINDS:
            state.local_names.append(span)
            continue
        overlaps_known = any(start < span.end and span.start < end for start, end in known_ids)
        state.ids.append(span)
        if in_zone(span.start):
            report.fallback_entities += 1
            state.local_pages.add(page_of(state.text, span.start))
        elif not overlaps_known:
            report.floor_entities += 1
    state.local_ran = True


def _review_local_names(
    states: Sequence[_FileState],
    *,
    roster: Roster,
    exclusions: Sequence[str],
    hygiene: LlmHygieneStats | None = None,
) -> BatchRoster | None:
    """Fase 2, do lote: os nomes do motor local passam pelo mesmo juizo do motor local
    (``BatchRoster.review``) antes de entrar no elenco. Nome visto em contexto de juiz ou procurador nao
    entra; instituicao do SISBAJUD fica em claro; rua com nome de pessoa entra como endereco. Ate o H9-b o que
    o LLM ja pos no elenco nao mudava; a partir do H9-c o mesmo juizo vale para as entradas dele.

    O que passa entra no elenco (origem ``motor_local``: aparece na revisao e propaga pelo lote). Entrada
    do elenco marcada ``manter`` vence o motor local (juiz, orgao). Folha em que o LLM FALHOU conta como
    tratada pelo motor local mesmo que ele nao ache nada; folha de resposta vazia (ou, sem rede, fora do
    cache) so conta se o motor local achar algo.

    H9-c: as entradas que o LLM pos no elenco passam pelo mesmo juizo (``_judge_llm_entries``), antes dos nomes
    do motor local, mesmo quando o motor local nao rodou em nenhuma folha.
    """

    _judge_llm_entries(states, roster, exclusions, hygiene)
    ran = [state for state in states if state.local_ran]
    if not ran:
        return None
    batch = BatchRoster(exclusions)
    for state in ran:
        batch.add(state.text, [*state.ids, *state.local_names])
    batch.review(state.text for state in ran)
    judged = {entity.key: entity for entity in batch.entities()}

    # Agrupamento do lote (H8): a entidade do BatchRoster, nao a grafia, e a unidade do elenco. Toda grafia
    # (erro de OCR, forma curta, "executada X") entra como variante da MESMA entrada, com o nome mais completo
    # como canonico: um token so, e spans_for_roster acha as formas curtas, como no motor local.
    used: set[str] = set()
    for state in ran:
        report = state.report
        in_zone = _suspect_zone(state)
        for span in state.local_names:
            value = state.text[span.start : span.end]
            kind = _NAME_KINDS[span.entity_type]
            canonical = value
            entity = judged.get(batch.key_for(span.entity_type, value))
            if entity is not None:
                if entity.public_agent or entity.financial:
                    report.local_kept += 1
                    continue
                if entity.is_street:
                    kind = "endereco"
                canonical = entity.representative
            entry = roster.lookup(kind, value) or roster.lookup(kind, canonical)
            if entry is not None and entry.action == KEEP:
                continue
            created = entry is None
            entry = roster.add_mention(
                Mention(kind=kind, text=value, canonical=canonical), source=LOCAL_FALLBACK_SOURCE
            )
            if entry is None:
                continue
            if entity is not None and entity.key not in used:
                used.add(entity.key)
                for variant in (*entity.names, *entity.role_phrases):
                    roster.add_mention(Mention(kind=kind, text=variant, canonical=canonical), source=LOCAL_FALLBACK_SOURCE)
            state.anchored.append((span.start, span.end, entry.key))
            if in_zone(span.start):
                report.fallback_entities += 1
                state.local_pages.add(page_of(state.text, span.start))
            elif created:
                report.floor_entities += 1

        # Falha real do LLM: a folha conta como tratada pelo motor local. Vazio (ou sem rede, fora do
        # cache): so conta se o motor local achou alguma coisa nela.
        real_failure = set() if state.offline_discovery else set(state.failed_discovery)
        treated = real_failure | (state.suspect_pages & state.local_pages)
        report.fallback_pages = sorted(treated)
        report.empty_confirmed_pages = sorted(state.suspect_pages - treated)
    return batch


def _suspect_zone(state: _FileState) -> Callable[[int], bool]:
    """Se uma posicao do texto cai numa folha suspeita (que o LLM nao leu ou respondeu vazio)."""

    ranges = page_ranges(state.text)
    zones = sorted(span for page in state.suspect_pages for span in ranges.get(page, ()))
    zone_starts = [start for start, _end in zones]

    def in_zone(position: int) -> bool:
        index = bisect_right(zone_starts, position) - 1
        return index >= 0 and position < zones[index][1]

    return in_zone


def _residual_hits(engine: ContextualEngine | None, text: str) -> tuple[str, ...]:
    """Revisao residual no texto ja trocado, como no modo local: so avisa, nunca troca."""

    if engine is None:
        return ()
    try:
        return tuple(engine.scan_residual(text))
    except Exception:  # noqa: BLE001 - aviso opcional; nao derruba o lote
        return ()


def _local_only_lines(reports: dict[str, LlmFileReport]) -> list[str]:
    """AUDITORIA e LEIA-ME do pipeline unico sem LLM: o motor local foi a unica fonte de nomes."""

    kept = sum(item.local_kept for item in reports.values())
    failed = [name for name, item in reports.items() if item.floor_error]
    lines = [
        "Motor local: unica fonte de nomes (sem LLM), em todas as folhas, com o juizo do lote; ocorrencias "
        f"deixadas fora do elenco por contexto de juiz/procurador ou de instituicao do SISBAJUD: {kept}."
    ]
    if failed:
        lines.append("ALERTA: o motor local nao rodou em " + ", ".join(failed) + " (so os IDs foram trocados).")
    return lines


def _floor_lines(floor: bool, reports: dict[str, LlmFileReport]) -> list[str]:
    """Linha da AUDITORIA e do LEIA-ME sobre o piso local: ligado ou desligado, e quanto ele acrescentou."""

    if not floor:
        return [
            "Piso local: DESLIGADO (o motor local so tratou as folhas que o LLM nao leu ou respondeu vazio; "
            "omissao parcial do LLM nao tem piso)."
        ]
    added = sum(item.floor_entities for item in reports.values())
    kept = sum(item.local_kept for item in reports.values())
    failed = [name for name, item in reports.items() if item.floor_error]
    lines = [
        "Piso local: LIGADO (o motor local rodou em todas as folhas e o resultado e a uniao do que o LLM e o "
        f"motor local apontaram; o LLM so acrescenta protecao). Entidades novas do motor local fora das folhas "
        f"suspeitas: {added}.",
        "Juizo do lote sobre o motor local (o mesmo do modo local): ocorrencias deixadas fora do elenco por "
        f"contexto de juiz/procurador ou de instituicao do SISBAJUD: {kept}.",
    ]
    if failed:
        lines.append("ALERTA: o motor local nao rodou em " + ", ".join(failed) + " (so valeu a leitura do LLM).")
    return lines


def _fallback_lines(by_file: dict[str, list[int]], held: Sequence[str]) -> list[str]:
    """Linhas da AUDITORIA e do LEIA-ME: so nome de arquivo e numero de folha, nunca texto."""

    lines: list[str] = []
    if by_file:
        listed = "; ".join(f"{name}: {_pages_label(pages)}" for name, pages in by_file.items())
        lines.append(f"Folhas tratadas pelo motor local por falha do LLM: {listed}.")
    if held:
        lines.append(
            "Arquivo(s) retido(s) em quarentena (pasta privada; nao exportado): "
            + ", ".join(held)
            + ". O LLM nao leu algumas folhas e o motor local nao pode trata-las."
        )
    return lines


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
    except Exception as exc:  # noqa: BLE001 - a UI precisa de um motivo por arquivo
        # Tipo, codigo HTTP e status: a mensagem crua de uma excecao do provedor pode trazer a chave ou
        # trechos da peca, e este motivo chega a tela, a CLI e ao log.
        errors[source] = FileOutcome(source=source, ok=False, error=safe_error(exc))
    return None


def _secrets_of(discoverer: Discoverer | None) -> tuple[str, ...]:
    """O valor da chave do descobridor, so para limpar o que vai para a AUDITORIA."""

    key = getattr(getattr(discoverer, "config", None), "api_key", None)
    reveal = getattr(key, "reveal", None)
    return (reveal(),) if callable(reveal) else ()


def _append_roster_guide(path: Path, *, local_only: bool = False) -> None:
    if local_only:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n"
                "elenco.json: quem e quem neste lote (com os originais), como o motor local\n"
                "os achou, e a acao (tokenizar/manter). Nada foi enviado a provedor nenhum.\n"
            )
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n"
            "elenco.json: quem e quem neste lote (com os originais), o papel que o\n"
            "LLM atribuiu e a acao (tokenizar/manter). Para corrigir, troque 'acao'\n"
            "e rode de novo com --elenco apontando para este arquivo (com --sem-rede,\n"
            "so o cache e usado: nada e enviado ao provedor).\n"
            "\n"
            f"{LLM_CACHE_SUBDIR}/ (na pasta privada, ao lado dos lotes): as respostas do\n"
            "provedor, com nomes, papeis e trechos EM CLARO. E tao sensivel quanto o\n"
            "mapa.json: nao copie, nao envie. Apagar a pasta so faz o proximo lote\n"
            "consultar o provedor de novo.\n"
        )


def _tokenize(
    text: str,
    ids: Sequence[EntitySpan],
    roster: Roster,
    anchored: Sequence[tuple[int, int, str]],
    exclusions: Sequence[str],
    folded: FoldedText,
    mapping: PseudonymMap,
    batch: BatchRoster | None = None,
) -> tuple[list[EntitySpan], PseudonymizationResult, OffsetMap, list[tuple[str, str]]]:
    spans = spans_for_roster(
        text,
        roster.entries,
        anchored=anchored,
        extra_keep=exclusions,
        folded=folded,
    )
    spans, refused = drop_geo_brand_spans(text, spans)
    if batch is not None:
        # O que o elenco do lote do motor local acrescenta, igual ao modo local: IDs ja vistos no lote e
        # a marca de uma palavra aprovada (H5). A marca leva a chave da empresa NO ELENCO deste pipeline,
        # para ter o mesmo token do nome completo (como no motor local).
        # Entrada marcada ``manter`` no elenco vence tambem aqui (a marca da empresa mantida fica em claro).
        keys = _roster_keys(batch, roster)
        for span in batch.lote_spans(text, [*ids, *spans], folded=folded):
            key = keys.get(span.canonical_key, span.canonical_key)
            entry = roster.get(key)
            if entry is not None and entry.action == KEEP:
                continue
            spans.append(replace(span, canonical_key=key))
    entities = resolve_overlaps([*ids, *spans])
    result, offsets = pseudonymize_with_offsets(text, entities, mapping)
    return entities, result, offsets, refused


def _roster_keys(batch: BatchRoster, roster: Roster) -> dict[str, str]:
    """Chave de cada entidade do elenco do lote -> chave da mesma entidade no ``Roster`` deste pipeline."""

    keys: dict[str, str] = {}
    for entity in batch.entities():
        kind = "endereco" if entity.is_street else entity.kind
        entry = roster.lookup(kind, entity.representative)
        if entry is not None:
            keys[entity.key] = entry.key
    return keys


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


def write_llm_private_audit(
    private_dir: Path,
    refused: Counter[tuple[str, str]],
    *,
    batch: BatchRoster | None = None,
    mapping: PseudonymMap | None = None,
    local_only: bool = False,
) -> Path:
    """``AUDITORIA_PRIVADA.txt`` do lote: as palavras recusadas e a marca de uma palavra aprovada pelo elenco
    do lote, para o Operador conferir. Fica ao lado do ``mapa.json``, nunca na pasta dos textos."""

    lines = [
        "AUDITORIA PRIVADA DESTE LOTE (" + ("motor local, pipeline unico" if local_only else "motor LLM") + ")",
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
    if batch is not None and mapping is not None:
        lines += [
            "## Marca de uma palavra (confira se cada palavra aprovada e mesmo nome proprio da empresa)",
            *(f"- {line}" for line in batch.brand_report(mapping, private=True)),
            "",
        ]
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


def _clean_mentions(
    mentions: Iterable[Mention],
    text: str,
    roster: Roster,
    exclusions: Sequence[str],
    stats: LlmHygieneStats | None,
) -> list[Mention]:
    """H9-c: higiene das mencoes do LLM (``llm_hygiene``), a mesma em todos os canais, antes do elenco.
    So descarta mencao do proprio LLM que nao e nome de pessoa, empresa ou endereco; o motor local nao passa
    por aqui. ``text`` e o texto que o LLM leu (as posicoes sao dele)."""

    known = [
        value
        for entry in roster.entries
        if entry.kind == "pessoa"
        for value in (entry.name, *entry.variants)
    ]
    return filter_llm_mentions(mentions, text, stats, extra_keep=exclusions, known_people=known)


_JUDGED_TYPES = {"pessoa": "PERSON", "empresa": "ORGANIZATION"}


def _judge_llm_entries(
    states: Sequence[_FileState],
    roster: Roster,
    exclusions: Sequence[str],
    stats: LlmHygieneStats | None,
) -> None:
    """H9-c: o juizo do lote (``BatchRoster.review``: agente publico por contexto de "assinado por"/cargo,
    instituicao do SISBAJUD, rua com nome de pessoa) tambem sobre as entradas que o LLM pos no elenco.
    Antes so o NER passava por ele. Entrada julgada agente publico ou instituicao financeira vira ``manter``
    (``Roster.lock_keep``: "entrada manter vence", nenhuma mencao posterior a desfaz); rua com nome de pessoa
    passa a endereco. Entrada fixada pelo usuario e lista de alvos nao mudam.

    Usa um ``BatchRoster`` so para julgar (nao o do pipeline): as entidades do LLM nao entram na marca de uma
    palavra nem na propagacao de IDs do lote, que seguem sendo do motor local."""

    keys = {key for state in states for _s, _e, key in state.anchored}
    candidates = [
        entry
        for key in sorted(keys)
        if (entry := roster.get(key)) is not None
        and entry.kind in _JUDGED_TYPES
        and entry.action == TOKENIZE
        and not entry.pinned
        and entry.source != "lista"
    ]
    if not candidates:
        return
    judge = BatchRoster(exclusions)
    for state in states:
        spans = list(state.local_names)
        for start, end, key in state.anchored:
            entry = roster.get(key)
            if entry is None or entry.kind not in _JUDGED_TYPES or not 0 <= start < end <= len(state.text):
                continue
            spans.append(
                EntitySpan(start=start, end=end, entity_type=_JUDGED_TYPES[entry.kind], score=0.9, recognizer="LlmMention")
            )
        judge.add(state.text, spans)
    judge.review(state.text for state in states)
    judged = {entity.key: entity for entity in judge.entities()}
    for entry in candidates:
        entity_type = _JUDGED_TYPES[entry.kind]
        entity = None
        for value in (entry.name, *entry.variants):
            entity = judged.get(judge.key_for(entity_type, value))
            if entity is not None:
                break
        if entity is None:
            continue
        if entry.kind == "pessoa" and entity.is_street:
            addresses = [
                roster.add_mention(
                    Mention(kind="endereco", text=value, canonical=entry.name), source=entry.source
                )
                for value in (entry.name, *entry.variants)
            ]
            street = next((item for item in addresses if item is not None), None)
            if street is not None and roster.lock_keep(entry.key):
                for state in states:
                    state.anchored.extend(
                        (start, end, street.key) for start, end, key in list(state.anchored) if key == entry.key
                    )
                if stats is not None:
                    stats.lote_rua += 1
        elif entry.kind == "pessoa" and entity.public_agent:
            if roster.lock_keep(entry.key) and stats is not None:
                stats.lote_publico += 1
        elif entry.kind == "empresa" and entity.financial:
            if roster.lock_keep(entry.key) and stats is not None:
                stats.lote_financeiro += 1


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


def _pages_of(text: str, ranges: Iterable[tuple[int, int]]) -> set[int]:
    pages: set[int] = set()
    for start, end in ranges:
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
    discoverer: Discoverer | None,
    text: str,
    floor: bool = False,
) -> list[str]:
    if discoverer is None:
        return [
            f"Extrator: {outcome.extractor} ({outcome.page_count} pagina(s))",
            "Motor local em todas as folhas (sem LLM): entidades que ele acrescentou ao elenco: "
            f"{report.floor_entities}; deixadas fora pelo juizo do lote: {report.local_kept}",
            *(["ALERTA: o motor local nao rodou: " + report.floor_error] if report.floor_error else []),
            *audit_markdown(text),
        ]
    discovery = report.discovery
    notes = [
        f"Extrator: {outcome.extractor} ({outcome.page_count} pagina(s))",
        f"LLM: {discoverer.describe()}",
        (
            f"Descoberta: {discovery.chunks} trecho(s), {discovery.cached} do cache, "
            f"{discovery.sent} enviado(s), {len(discovery.failed)} com falha, "
            f"{len(discovery.empty)} resposta(s) vazia(s) (nao guardada(s) no cache), "
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
    if report.fallback_pages:
        notes.append(
            "Folhas tratadas pelo motor local por falha do LLM: "
            + _pages_label(report.fallback_pages)
            + f" ({report.fallback_entities} entidade(s) apontada(s) pelo motor local)."
        )
    if report.empty_confirmed_pages:
        notes.append(
            "Resposta vazia do LLM, sem nome segundo o motor local: "
            + _pages_label(report.empty_confirmed_pages)
        )
    if floor:
        notes.append(
            f"Piso local ligado: entidades novas do motor local fora das folhas suspeitas: {report.floor_entities}"
        )
        if report.floor_error:
            notes.append("ALERTA: piso local ligado, mas " + report.floor_error + "; so valeu a leitura do LLM.")
    if report.fallback_error:
        notes.append("ALERTA: " + report.fallback_error + ". Arquivo retido em quarentena.")
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

"""Tela do motor LLM (NiceGUI): escolha do motor, aviso do canal, chave e revisao do elenco.

Camada fina sobre ``llm_engine`` (portao), ``credentials`` (chave) e
``roster_review`` (L3). Nada daqui e gravado: o app SEMPRE abre no motor local
e a escolha do motor vale so para a sessao. A chave so e digitada num campo de
senha e vai direto ao Gerenciador de Credenciais; a tela mostra apenas a fonte.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from .compat import patch_python314

patch_python314()

from nicegui import run, ui

from . import credentials
from .llm_engine import (
    CHANNELS,
    EngineChoice,
    EngineDecision,
    audit_lines,
    billing_statement,
    build_discoverer,
    check_engine,
    warning_paragraphs,
)
from .pipeline import JobResult
from .pipeline_llm import LLM_CACHE_SUBDIR, LlmJobResult, run_llm_job
from .roster import KEEP, TOKENIZE
from .roster_review import (
    ACTION_LABELS,
    build_rows,
    reapply_offline,
    summarize,
)

LOCAL = "local"
LLM = "llm"


@dataclass
class EngineSession:
    """Estado do motor na sessao. Nunca e gravado em disco."""

    choice: EngineChoice = field(default_factory=EngineChoice)
    decision: EngineDecision | None = None
    last: LlmJobResult | None = None
    last_files: list[Path] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.decision is not None and self.decision.allowed

    def back_to_local(self) -> None:
        self.choice = EngineChoice()
        self.decision = None


def build_engine_section(session, *, on_change: Callable[[], None] = lambda: None) -> None:
    """Seletor de motor dentro do cartao "Motor automatico". Abre sempre em "Local"."""

    engine: EngineSession = session.engine
    ui.label("Motor de descoberta").classes("aj-label q-mt-sm")
    toggle = ui.toggle({LOCAL: "Local", LLM: "LLM"}, value=LOCAL).props("no-caps dense unelevated")
    status = ui.label("").classes("aj-card-note")

    def refresh() -> None:
        if engine.active:
            status.text = (
                f"LLM ativo: {engine.decision.channel}. O texto, com os IDs mascarados, "
                "será enviado ao provedor."
            )
            toggle.value = LLM
        else:
            status.text = "Local (padrão): nada sai deste computador."
            toggle.value = LOCAL
        on_change()

    def chosen(event) -> None:
        if event.value == LLM and not engine.active:
            open_engine_dialog(session, on_close=refresh)
        elif event.value == LOCAL and engine.active:
            engine.back_to_local()
            refresh()

    toggle.on_value_change(chosen)
    ui.button("Configurar LLM…", icon="cloud_queue", on_click=lambda: open_engine_dialog(session, on_close=refresh)).props(
        "flat dense no-caps"
    )
    refresh()


def open_engine_dialog(session, *, on_close: Callable[[], None]) -> None:
    engine: EngineSession = session.engine
    draft = {"choice": engine.choice if engine.choice.channel else EngineChoice(channel="gemini-pago")}

    def current() -> EngineChoice:
        return draft["choice"]

    def set_choice(**changes) -> None:
        draft["choice"] = replace(draft["choice"], **changes)

    def on_channel(value: str) -> None:
        set_choice(channel=value, acknowledged=False, billing_confirmed=False)
        redraw()

    def on_field(**changes) -> None:
        # Campo ou caixa de selecao: refaz so a conferencia, sem recriar o que foi digitado.
        set_choice(**changes)
        draw_reasons()

    with ui.dialog() as dialog, ui.column().classes("aj-dialog"):
        with ui.row().classes("w-full items-center no-wrap"):
            with ui.column().classes("gap-1"):
                ui.label("Opcional").classes("aj-kicker")
                ui.label("Motor LLM").classes("aj-title").style("font-size:24px")
            ui.space()
            ui.button(icon="close", on_click=lambda: dialog.close()).props(
                'flat round aria-label="Fechar"'
            )
        ui.label(
            "O motor local segue sendo o padrão e o app sempre abre nele. O LLM só roda depois "
            "de todas as conferências abaixo, e a escolha vale apenas para esta sessão."
        ).classes("aj-card-note")
        ui.select(
            {key: spec.label for key, spec in CHANNELS.items()},
            value=current().channel,
            label="Canal",
            on_change=lambda event: on_channel(event.value),
        ).classes("w-full")
        project_box = ui.column().classes("w-full gap-1")
        key_box = ui.column().classes("w-full gap-1")
        warning_box = ui.column().classes("w-full gap-2")
        confirm_box = ui.column().classes("w-full gap-1")
        reasons_box = ui.column().classes("w-full gap-1")
        with ui.row().classes("w-full items-center justify-end gap-2"):
            ui.button("Usar o motor local", on_click=lambda: use_local()).props("flat no-caps")
            use_button = ui.button("Usar este motor", icon="check", on_click=lambda: confirm()).props(
                "unelevated no-caps color=primary"
            )

    def decide() -> EngineDecision:
        return check_engine(current(), private_root=session.private_dir)

    def draw_reasons() -> None:
        decision = decide()
        reasons_box.clear()
        with reasons_box:
            if decision.allowed:
                with ui.row().classes("aj-callout info items-start"):
                    ui.icon("check_circle")
                    ui.label("Tudo em ordem: o motor LLM pode ser usado nesta sessão.")
            else:
                for reason in decision.reasons:
                    with ui.row().classes("aj-callout warn items-start"):
                        ui.icon("block")
                        ui.label(reason)
        use_button.set_enabled(decision.allowed)

    def redraw() -> None:
        choice = current()
        spec = CHANNELS.get(choice.channel)

        project_box.clear()
        if spec is not None and spec.needs_project:
            with project_box:
                ui.input(
                    "Projeto do Google Cloud",
                    value=choice.project,
                    on_change=lambda event: on_field(project=event.value or ""),
                ).classes("w-full")
                ui.label(
                    f"Região: {choice.location}. Credencial: gcloud auth application-default login."
                ).classes("aj-card-note")

        key_box.clear()
        if spec is not None and spec.needs_key:
            found = credentials.load_key()
            with key_box:
                ui.label("Chave da API").classes("aj-label")
                ui.label(credentials.describe_source(found)).classes("aj-card-note")
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    key_input = ui.input("Colar a chave (não aparece na tela)", password=True).classes("aj-grow")
                    ui.button("Guardar", icon="lock", on_click=lambda: store(key_input)).props(
                        "outline no-caps dense"
                    )
                    ui.button("Apagar", icon="delete", on_click=lambda: erase()).props(
                        "flat no-caps dense"
                    )
                ui.label(
                    "Fica no Gerenciador de Credenciais do Windows (ou na variável "
                    f"{credentials.ENV_VAR}); nunca em arquivo do projeto. GEMINI_API_KEY e "
                    "GOOGLE_API_KEY não são lidas de propósito."
                ).classes("aj-card-note")

        warning_box.clear()
        with warning_box:
            ui.label("Aviso").classes("aj-label")
            with ui.column().classes("aj-callout warn gap-2"):
                for paragraph in warning_paragraphs(choice.channel):
                    ui.html(f"<p>{html.escape(paragraph)}</p>")

        confirm_box.clear()
        if spec is not None:
            with confirm_box:
                ui.checkbox(
                    "Li o aviso acima.",
                    value=choice.acknowledged,
                    on_change=lambda event: on_field(acknowledged=bool(event.value)),
                )
                ui.checkbox(
                    billing_statement(choice.channel),
                    value=choice.billing_confirmed,
                    on_change=lambda event: on_field(billing_confirmed=bool(event.value)),
                )

        draw_reasons()

    def store(key_input) -> None:
        try:
            credentials.store_key(str(key_input.value or ""))
        except credentials.CredentialsError as exc:
            ui.notify(str(exc), type="negative")
            return
        key_input.value = ""
        ui.notify("Chave guardada no Gerenciador de Credenciais.", type="positive")
        redraw()

    def erase() -> None:
        try:
            removed = credentials.delete_key()
        except credentials.CredentialsError as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify("Chave apagada." if removed else "Não havia chave guardada.", type="info")
        redraw()

    def confirm() -> None:
        decision = decide()
        if not decision.allowed:
            return
        engine.choice = current()
        engine.decision = decision
        dialog.close()

    def use_local() -> None:
        engine.back_to_local()
        dialog.close()

    dialog.on("hide", lambda _: on_close())
    redraw()
    dialog.open()


async def run_llm_flow(
    session,
    sources: list[Path],
    *,
    status,
    progress,
    result_area,
    run_kicker,
    run_button,
    render_results: Callable[..., None],
    token_labels: Callable[[], dict[str, str]],
) -> None:
    """Rodada com LLM no lugar do fluxo local. Reconfere o portao a cada rodada."""

    engine: EngineSession = session.engine
    decision = check_engine(engine.choice, private_root=session.private_dir)
    if not decision.allowed:
        engine.back_to_local()
        ui.notify("Motor LLM bloqueado: " + decision.blocked_message(), type="negative")
        return
    engine.decision = decision

    session.running = True
    run_kicker.text = "Executando (LLM)"
    run_button.disable()
    progress.visible = True
    progress.value = 0
    result_area.clear()
    with result_area:
        with ui.row().classes("items-center gap-3"):
            ui.spinner(size="md", color="accent")
            ui.label("Lendo o lote com o LLM…").classes("aj-muted")

    state = {"message": "Iniciando", "fraction": 0.0}

    def on_progress(message: str, fraction: float) -> None:
        state["message"], state["fraction"] = message, fraction

    ticker = ui.timer(0.4, lambda: _tick(status, progress, state))
    try:
        discoverer = build_discoverer(decision, cache_dir=session.private_dir / LLM_CACHE_SUBDIR)
        result = await run.io_bound(
            lambda: run_llm_job(
                sources,
                export_root=session.export_dir,
                private_root=session.private_dir,
                discoverer=discoverer,
                settings=session.settings,
                engine=decision.channel,
                engine_notes=audit_lines(decision, engine.choice),
                on_progress=on_progress,
            )
        )
    except Exception as exc:  # noqa: BLE001
        ticker.cancel()
        status.text = "A execução falhou."
        ui.notify(str(exc), type="negative")
        result_area.clear()
        with result_area:
            with ui.row().classes("aj-callout warn items-start"):
                ui.icon("error")
                ui.label(f"A execução falhou: {exc}")
        session.running = False
        run_kicker.text = "Falhou"
        run_button.enable()
        progress.visible = False
        return
    ticker.cancel()

    engine.last, engine.last_files = result, list(sources)
    show_result(session, result, status=status, progress=progress, result_area=result_area,
                run_kicker=run_kicker, run_button=run_button, render_results=render_results,
                token_labels=token_labels)


def show_result(
    session,
    result: LlmJobResult,
    *,
    status,
    progress,
    result_area,
    run_kicker,
    run_button,
    render_results: Callable[..., None],
    token_labels: Callable[[], dict[str, str]],
) -> None:
    job: JobResult = result.job
    session.last_job = job
    progress.value = 1
    status.text = (
        f"Concluído (LLM {result.engine}): {len(job.succeeded)} ok, {len(job.failed)} com erro. "
        "Revise o elenco antes de enviar."
    )
    render_results(result_area, job, token_labels())
    with result_area:
        if result.fallback_pages or result.held:
            with ui.row().classes("aj-callout warn items-start"):
                ui.icon("report_problem")
                with ui.column().classes("gap-1"):
                    ui.label(
                        "O LLM não leu algumas folhas. Elas passaram pelo motor local (nunca saem com "
                        "nome em claro por falha do LLM); confira essas folhas antes de enviar."
                    )
                    for name, pages in result.fallback_pages.items():
                        ui.label(
                            f"Folhas tratadas pelo motor local por falha do LLM, em {name}: "
                            + ", ".join(str(page) for page in pages)
                        ).classes("aj-card-note")
                    for name in result.held:
                        ui.label(
                            f"{name}: retido na pasta privada (quarentena), não exportado: o motor "
                            "local também não pôde tratar as folhas sem leitura do LLM."
                        ).classes("aj-card-note")
        with ui.row().classes("aj-callout info items-start"):
            ui.icon("fact_check")
            with ui.column().classes("gap-1"):
                ui.label(
                    f"Elenco do LLM: {len(result.roster)} entidade(s). Confira quem foi trocado e quem foi "
                    "mantido; a decisão vale para a próxima gravação e roda sem rede."
                )
                ui.button(
                    "Revisar elenco", icon="rule",
                    on_click=lambda: open_roster_review(
                        session, result, status=status, progress=progress, result_area=result_area,
                        run_kicker=run_kicker, run_button=run_button, render_results=render_results,
                        token_labels=token_labels,
                    ),
                ).props("outline no-caps dense")
    session.running = False
    run_kicker.text = "Concluído"
    run_button.enable()
    if job.failed:
        ui.notify("Alguns arquivos falharam. Veja o resultado.", type="warning")
    elif not job.export_allowed:
        ui.notify(job.export_block_reason, type="warning")
    else:
        ui.notify("Lote concluído.", type="positive")


def open_roster_review(session, result: LlmJobResult, **view) -> None:
    """Tabela do elenco: tipo, nome, papel, mencoes, token e tokenizar/manter. So local."""

    rows = build_rows(result.roster, occurrences=result.occurrences, tokens=result.tokens)
    decisions: dict[str, str] = {}
    filters = {"text": "", "only_changed": False}

    with ui.dialog() as dialog, ui.column().classes("aj-dialog").style("width:min(1080px,96vw);max-width:1080px!important"):
        with ui.row().classes("w-full items-center no-wrap"):
            with ui.column().classes("gap-1"):
                ui.label("Revisão").classes("aj-kicker")
                ui.label("Elenco do lote").classes("aj-title").style("font-size:24px")
            ui.space()
            ui.button(icon="close", on_click=lambda: dialog.close()).props('flat round aria-label="Fechar"')
        with ui.row().classes("aj-callout info items-start"):
            ui.icon("lock")
            ui.label(
                "Esta tela mostra nomes reais e fica só neste computador. Trocar uma linha para "
                "“Manter” deixa o nome em claro; “Tokenizar” o troca. A decisão fica fixa e a "
                "regravação roda sem rede, só com o cache."
            )
        summary = ui.label("").classes("aj-card-note")
        with ui.row().classes("w-full items-center gap-3"):
            ui.input(
                "Buscar nome", on_change=lambda event: (filters.update(text=(event.value or "").casefold()), redraw())
            ).props("dense clearable").classes("aj-grow")
            ui.checkbox(
                "Só as minhas decisões",
                on_change=lambda event: (filters.update(only_changed=bool(event.value)), redraw()),
            )
        body = ui.column().classes("w-full gap-0").style("max-height:52vh;overflow:auto")
        with ui.row().classes("w-full items-center justify-end gap-2"):
            ui.button("Fechar", on_click=lambda: dialog.close()).props("flat no-caps")
            apply_button = ui.button(
                "Aplicar e regravar (sem rede)", icon="save", on_click=lambda: apply()
            ).props("unelevated no-caps color=primary")

    def action_of(row) -> str:
        return decisions.get(row.key, row.action)

    def toggle(row, value: str) -> None:
        if value == row.action:
            decisions.pop(row.key, None)
        else:
            decisions[row.key] = value
        refresh_summary()

    def refresh_summary() -> None:
        summary.text = summarize(rows, decisions)
        apply_button.set_enabled(bool(decisions))

    def redraw() -> None:
        body.clear()
        with body:
            for kind in ("empresa", "pessoa", "endereco"):
                group = [
                    row for row in rows
                    if row.kind == kind
                    and (not filters["text"] or filters["text"] in row.name.casefold())
                    and (not filters["only_changed"] or row.changed or row.key in decisions)
                ]
                if not group:
                    continue
                ui.label(f"{group[0].kind_label} ({len(group)})").classes("aj-label q-mt-sm")
                for row in group:
                    with ui.element("div").classes("aj-rule").style(
                        "grid-template-columns:minmax(0,2.4fr) minmax(0,1.2fr) 70px 110px 190px;align-items:center"
                    ):
                        ui.label(row.name).classes("aj-rule-name")
                        ui.label(row.role_label).classes("aj-card-note")
                        ui.label(f"{row.occurrences}×").classes("aj-card-note")
                        ui.label(row.token or "—").classes("aj-code")
                        ui.toggle(
                            {TOKENIZE: ACTION_LABELS[TOKENIZE], KEEP: ACTION_LABELS[KEEP]},
                            value=action_of(row),
                            on_change=lambda event, r=row: toggle(r, event.value),
                        ).props("no-caps dense unelevated")
        refresh_summary()

    async def apply() -> None:
        if not decisions:
            return
        apply_button.disable()
        summary.text = "Regravando sem rede…"
        decision = session.engine.decision
        model_id = decision.config.model_id if decision is not None and decision.config is not None else None
        try:
            kwargs = {"model_id": model_id} if model_id else {}
            done = await run.io_bound(
                lambda: reapply_offline(
                    result,
                    dict(decisions),
                    session.engine.last_files,
                    export_root=session.export_dir,
                    private_root=session.private_dir,
                    settings=session.settings,
                    **kwargs,
                )
            )
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"Não foi possível regravar: {exc}", type="negative")
            apply_button.enable()
            refresh_summary()
            return
        dialog.close()
        session.engine.last = done.result
        show_result(session, done.result, **view)

    redraw()
    dialog.open()


def _tick(status, progress, state: dict) -> None:
    status.text = str(state["message"])
    progress.value = float(state["fraction"])

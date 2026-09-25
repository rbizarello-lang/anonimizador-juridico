from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .compat import patch_python314

patch_python314()

from nicegui import run, ui

from anonimizador_juridico import PseudonymMap, StructuredDetector

from .audit import write_audit_report
from .catalog import (
    CONTEXTUAL_TAGS,
    FIDELITY_PRINCIPLE,
    GEMINI_POLICY,
    STRUCTURED_TAGS,
)
from .cnpj_index import CnpjIndex, default_index_path
from .contextual import build_contextual_engine
from .ner_cache import default_ner_cache_dir
from .branding import apply_taskbar_icon, ensure_icon, install_native_icon_hook, png_path
from .desktop import APP_PORT, APP_URL, install_windows_shortcuts, bootstrap_instance, wants_browser
from .extractors import SUPPORTED_SUFFIXES
from .pipeline import (
    FileOutcome,
    JobResult,
    build_job_result,
    default_export_dir,
    default_private_dir,
    new_job_id,
    process_file,
    resolve_lote_dirs,
    build_audit_notes,
    write_lote_guides,
)
from .rules import InvalidRuleError, build_recognizers, slug_entity_type, validate_custom_rule
from .settings import CustomRule, load_settings, save_settings
from .spreadsheet_targets import merge_unique, parse_target_spreadsheet
from .target_ids import parse_target_ids

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_APP_CSS = """
<style>
  body, .q-page, #app { background: #f3efe6 !important; color: #2b261f; }
  .aj-shell { max-width: 880px; margin: 0 auto; padding: 20px 16px 56px; }
  .aj-muted { color: #6a6358; }
  .aj-help {
    color: #5c564c;
    font-size: 0.92rem;
    line-height: 1.45;
    background: #faf7f1;
    border-left: 3px solid #c9bda8;
    padding: 8px 12px;
    border-radius: 0 8px 8px 0;
    cursor: default;
    user-select: text;
  }
  .aj-path {
    font-family: ui-monospace, Consolas, monospace;
    font-size: 0.8rem;
    word-break: break-all;
    color: #5a5349;
    background: #f7f4ee;
    padding: 6px 10px;
    border-radius: 8px;
    cursor: default;
  }
  .aj-hero { gap: 4px; margin-bottom: 8px; }
  .aj-kicker { font-size: 0.78rem; letter-spacing: 0.06em; text-transform: uppercase; color: #6b5a3e; }
  .aj-title { font-size: 1.7rem; font-weight: 600; color: #1b365d; line-height: 1.2; }
  .aj-subtitle { color: #5c564c; font-size: 1rem; line-height: 1.4; }
  .aj-banner {
    background: #eef3ea !important;
    border: 1px solid #d5e0d1;
    box-shadow: none !important;
  }
  .aj-section { box-shadow: 0 1px 3px rgba(40, 32, 20, 0.08) !important; border-radius: 12px !important; }
  .aj-section-title { font-size: 1.05rem; font-weight: 600; color: #1b365d; }
  .aj-badge {
    width: 28px; height: 28px; min-width: 28px;
    border-radius: 50%;
    background: #1b365d; color: #fff;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.85rem; font-weight: 700;
  }
  .aj-actions { gap: 8px; flex-wrap: wrap; }
  .aj-file {
    background: #faf7f1;
    border: 1px solid #e4ddd0;
    border-radius: 10px;
    padding: 8px 10px;
  }
  .aj-empty { color: #7a7368; font-size: 0.92rem; font-style: italic; }
  .q-btn { border-radius: 8px !important; font-weight: 600 !important; letter-spacing: 0.01em; }
  .q-btn--unelevated, .q-btn--standard { min-height: 40px; padding: 0 16px; }
  .q-btn--outline { min-height: 40px; padding: 0 16px; background: #fff !important; }
  .q-btn--flat:not(.q-btn--round) { min-height: 36px; }
  .q-btn--round { background: #fff !important; border: 1px solid #d4cbb8 !important; }
  .q-tab { font-weight: 600; text-transform: none; }
  .q-tab--active { color: #1b365d; }
  .q-checkbox { background: #faf7f1; border-radius: 8px; padding: 6px 10px; }
  .q-field--outlined .q-field__control { border-radius: 8px; background: #fff; }
  .q-expansion-item { background: #fff; border-radius: 12px; }
</style>
"""


def _help(text: str) -> None:
    ui.label(text).classes("aj-help w-full")


def _section_head(number: str, title: str) -> None:
    with ui.row().classes("items-center no-wrap q-mb-sm"):
        ui.label(number).classes("aj-badge")
        ui.label(title).classes("aj-section-title")


def _parse_name_list(text: object) -> list[str]:
    return [item.strip() for item in str(text or "").splitlines() if item.strip()]


def _commit_target_lists(
    session: Session,
    companies_text: object,
    people_text: object,
    ids_text: object = "",
) -> None:
    session.settings.target_companies = _parse_name_list(companies_text)
    session.settings.target_people = _parse_name_list(people_text)
    session.settings.target_ids = list(parse_target_ids([str(ids_text or "")]))
    _sync_target_list_text(session)


def _sync_target_list_text(session: Session) -> None:
    session.target_companies_text = "\n".join(session.settings.target_companies)
    session.target_people_text = "\n".join(session.settings.target_people)
    session.target_ids_text = "\n".join(session.settings.target_ids)


def _import_spreadsheet_into_session(session: Session, path: Path) -> str:
    parsed = parse_target_spreadsheet(path)
    before_companies = len(session.settings.target_companies)
    before_people = len(session.settings.target_people)
    before_ids = len(session.settings.target_ids)
    session.settings.target_companies = merge_unique(
        session.settings.target_companies, parsed.companies
    )
    session.settings.target_people = merge_unique(
        session.settings.target_people, parsed.people
    )
    session.settings.target_ids = list(
        parse_target_ids(session.settings.target_ids + parsed.ids)
    )
    _sync_target_list_text(session)
    save_settings(session.private_dir, session.settings)
    added_companies = len(session.settings.target_companies) - before_companies
    added_people = len(session.settings.target_people) - before_people
    added_ids = len(session.settings.target_ids) - before_ids
    parts = []
    if added_companies:
        parts.append(f"{added_companies} empresa(s)")
    if added_people:
        parts.append(f"{added_people} pessoa(s)")
    if added_ids:
        parts.append(f"{added_ids} CPF/CNPJ")
    added = " e ".join(parts) if parts else "nenhum nome ou documento novo"
    return f"Importados {added} de {path.name}."


def _bind_target_lists(session: Session):
    companies = (
        ui.textarea("Empresas a anonimizar")
        .props("outlined")
        .classes("w-full")
        .bind_value(session, "target_companies_text")
    )
    people = (
        ui.textarea("Pessoas a anonimizar")
        .props("outlined")
        .classes("w-full")
        .bind_value(session, "target_people_text")
    )
    ids = (
        ui.textarea("CNPJ e CPF da carteira")
        .props("outlined")
        .classes("w-full")
        .bind_value(session, "target_ids_text")
    )
    return companies, people, ids


class Session:
    def __init__(self) -> None:
        self.files: list[Path] = []
        self.export_dir = default_export_dir(PROJECT_ROOT)
        self.private_dir = default_private_dir(PROJECT_ROOT)
        self.running = False
        self.last_job: JobResult | None = None
        self.settings = load_settings(self.private_dir)
        self.target_companies_text = "\n".join(self.settings.target_companies)
        self.target_people_text = "\n".join(self.settings.target_people)
        self.target_ids_text = "\n".join(self.settings.target_ids)


def run_app() -> None:
    bootstrap_instance(PROJECT_ROOT)
    session = Session()
    session.export_dir.mkdir(parents=True, exist_ok=True)
    session.private_dir.mkdir(parents=True, exist_ok=True)
    try:
        install_windows_shortcuts(PROJECT_ROOT)
    except OSError:
        pass

    ui.dark_mode(False)
    ui.colors(
        primary="#1b365d",
        secondary="#6b5a3e",
        accent="#1b365d",
        positive="#2f6b4f",
        negative="#8b2942",
        warning="#8a6d3b",
        info="#1b365d",
    )
    ui.add_head_html(
        f"""
        <meta name="theme-color" content="#1b365d">
        <meta name="application-name" content="Anonimizador Juridico">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="mobile-web-app-capable" content="yes">
        <link rel="manifest" href="data:application/manifest+json,{_app_manifest()}">
        {_APP_CSS}
        """
    )

    with ui.column().classes("aj-shell w-full"):
        _build_page(session)

    native = not wants_browser()
    if native:
        try:
            import webview  # noqa: F401
        except ImportError:
            native = False
    icon_file = ensure_icon(PROJECT_ROOT)
    mark = icon_file if icon_file and icon_file.is_file() else png_path(PROJECT_ROOT)
    kwargs: dict = {
        "title": "Anonimizador Juridico",
        "favicon": str(mark) if mark.is_file() else "⚖️",
        "host": "127.0.0.1",
        "port": APP_PORT,
        "reload": False,
        "show": not native,
        "language": "pt-BR",
    }
    if native:
        kwargs["native"] = True
        kwargs["window_size"] = (1100, 820)
        if icon_file is not None:
            install_native_icon_hook(icon_file)
            try:
                from nicegui import app as nicegui_app

                nicegui_app.native.start_args["icon"] = str(icon_file)
            except Exception:
                pass
        apply_taskbar_icon(PROJECT_ROOT, title="Anonimizador Juridico")
    else:
        kwargs["native"] = False
    ui.run(**kwargs)


def _build_page(session: Session) -> None:
    with ui.column().classes("aj-hero w-full"):
        ui.label("App local").classes("aj-kicker")
        ui.label("Anonimizador Juridico").classes("aj-title")
        ui.label(
            "Escolha os arquivos, informe os nomes do caso se quiser, e clique em Anonimizar. "
            "O texto tokenizado vai para uma pasta; a chave para reverter, para outra."
        ).classes("aj-subtitle")
        ui.label(APP_URL).classes("aj-path")

    with ui.card().classes("aj-banner w-full"):
        ui.label("O que permanece na peca").classes("text-weight-medium")
        ui.label(FIDELITY_PRINCIPLE).classes("aj-help w-full q-mt-xs")

    with ui.tabs().classes("w-full") as tabs:
        anonymize_tab = ui.tab("Anonimizar", icon="description")
        rules_tab = ui.tab("Regras", icon="tune")
    with ui.tab_panels(tabs, value=anonymize_tab).classes("w-full"):
        with ui.tab_panel(anonymize_tab):
            _build_anonymize_tab(session)
        with ui.tab_panel(rules_tab):
            _build_rules_tab(session)


def _build_anonymize_tab(session: Session) -> None:
    with ui.card().classes("aj-section w-full"):
        _section_head("1", "Arquivos do lote")
        _help(
            "Pode escolher varios PDFs de uma vez (PARTES de cerca de 10 MB). "
            "O app executa um apos o outro. Cada arquivo tem ate 15 minutos na extracao. "
            "No mesmo lote, o mesmo dado vira o mesmo token em todos os arquivos."
        )
        with ui.row().classes("aj-actions q-mt-sm"):
            choose_files_btn = ui.button("Escolher arquivos", icon="description").props(
                "unelevated no-caps"
            )
            choose_folder_btn = ui.button("Adicionar pasta", icon="folder").props(
                "outline no-caps"
            )
            clear_btn = ui.button("Limpar fila", icon="delete").props("outline no-caps")
        with ui.row().classes("w-full items-center q-mt-sm"):
            path_input = ui.input(
                placeholder="Ou cole o caminho de um arquivo ou pasta"
            ).props("dense outlined").classes("col-grow")
            add_path_btn = ui.button("Adicionar").props("outline no-caps")
        file_list = ui.column().classes("w-full q-mt-sm")

    with ui.card().classes("aj-section w-full"):
        _section_head("2", "Empresas e pessoas deste caso")
        _help(
            "Um nome por linha, ou anexe a planilha do Nexus (.xlsx) com Nome e "
            "CPF/CNPJ. O app troca cada nome onde aparecer. CNPJ e CPF da carteira "
            "entram so com digito verificador; orgao publico (Uniao) nao entra. "
            "Uniao, PGFN e a lista de exclusao nunca sao trocados."
        )
        case_companies, case_people, case_ids = _bind_target_lists(session)
        case_companies.classes("q-mt-sm")
        with ui.row().classes("aj-actions q-mt-sm"):
            save_case_btn = ui.button("Salvar lista deste caso", icon="save").props(
                "unelevated no-caps"
            )
            import_case_btn = ui.button("Anexar planilha", icon="table_chart").props(
                "outline no-caps"
            )

    with ui.card().classes("aj-section w-full"):
        _section_head("3", "Onde gravar")
        _help(
            "Textos anonimizados e mapa para reverter ficam em pastas diferentes. "
            "Cada lote ganha o mesmo identificador nas duas arvores."
        )
        with ui.row().classes("w-full items-center q-mt-sm aj-actions"):
            export_label = ui.label(
                _dir_label("Textos anonimizados", session.export_dir)
            ).classes("aj-path col-grow")
            change_export_btn = ui.button("Trocar pasta", icon="folder_open").props(
                "outline no-caps"
            )
        with ui.row().classes("w-full items-center q-mt-sm aj-actions"):
            private_label = ui.label(
                _dir_label("Mapas privados", session.private_dir)
            ).classes("aj-path col-grow")
            change_private_btn = ui.button("Trocar pasta", icon="lock").props(
                "outline no-caps"
            )

    with ui.card().classes("aj-section w-full"):
        _section_head("4", "Motor automatico")
        _help(
            "O motor local (GLiNER) roda nesta maquina. Na duvida ele nao troca o trecho."
        )
        local_toggle = ui.checkbox(
            "Usar motor local (GLiNER / spaCy PT)",
            value=session.settings.local_ner_enabled,
        ).classes("q-mt-sm")

    with ui.card().classes("aj-section w-full"):
        _section_head("5", "Executar")
        _help(
            "Um clique processa a fila inteira. Identificadores iguais recebem o mesmo token. "
            "OCR so entra se alguma pagina nao tiver texto util."
        )
        run_button = ui.button("Anonimizar", icon="play_arrow").props(
            "color=primary unelevated size=lg no-caps"
        )
        progress = ui.linear_progress(value=0).props("instant-feedback").classes("w-full q-mt-sm")
        progress.visible = False
        status = ui.label("Nenhum arquivo selecionado.").classes("aj-empty")

    with ui.card().classes("aj-section w-full"):
        ui.label("Resultado").classes("aj-section-title")
        result_area = ui.column().classes("w-full")
        with result_area:
            ui.label("O resumo aparece aqui depois da execucao.").classes("aj-empty")

    with ui.expansion("O que esta versao detecta — e o que ainda nao").classes("w-full"):
        ui.label("Detectado pelo Presidio").classes("text-weight-medium")
        for tag in STRUCTURED_TAGS:
            ui.label(f"{tag.token} — {tag.description}")
        ui.label("Detectado no proprio computador (GLiNER; spaCy PT se o GLiNER faltar)").classes(
            "text-weight-medium q-mt-sm"
        )
        for tag in CONTEXTUAL_TAGS:
            ui.label(f"{tag.token} — {tag.description}")
        ui.label(FIDELITY_PRINCIPLE).classes("aj-muted q-mt-sm")
        ui.label("Extracao de PDF").classes("text-weight-medium q-mt-sm")
        ui.label(
            "O Docling e o extrator padrao. OCR so e ligado se alguma pagina "
            "nao tiver texto nativo suficiente. Se o Docling falhar, o PyMuPDF assume."
        )
        ui.label("Leitura externa").classes("text-weight-medium q-mt-sm")
        ui.label(GEMINI_POLICY)

    def refresh_files() -> None:
        file_list.clear()
        with file_list:
            if not session.files:
                ui.label("Nenhum arquivo na fila.").classes("aj-empty")
                status.text = "Nenhum arquivo selecionado."
                return
            for path in list(session.files):
                with ui.row().classes("w-full items-center justify-between aj-file"):
                    with ui.column().classes("col-grow"):
                        ui.label(path.name).classes("text-weight-medium")
                        ui.label(str(path)).classes("aj-path")
                    remover = ui.button(
                        icon="close",
                        on_click=lambda p=path: remove_file(p),
                    ).props("outline round dense")
                    remover.tooltip("Retirar da fila")
        status.text = f"{len(session.files)} arquivo(s) na fila."

    def add_paths(paths: list[Path]) -> None:
        accepted: list[Path] = []
        ignored = 0
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                children = [
                    child
                    for child in sorted(path.iterdir())
                    if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES
                ]
                if not children:
                    ignored += 1
                    continue
                accepted.extend(children)
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                ignored += 1
                continue
            accepted.append(path)
        existing = {item.resolve() for item in session.files}
        added = 0
        for path in accepted:
            resolved = path.resolve()
            if resolved in existing:
                continue
            if not resolved.exists():
                ignored += 1
                continue
            session.files.append(resolved)
            existing.add(resolved)
            added += 1
        refresh_files()
        if added:
            ui.notify(f"{added} arquivo(s) adicionados.", type="positive")
        if ignored:
            ui.notify(
                f"{ignored} item(ns) ignorados. Use PDF, DOCX, TXT ou MD.",
                type="warning",
            )

    def remove_file(path: Path) -> None:
        session.files = [item for item in session.files if item != path]
        refresh_files()

    def clear_files() -> None:
        session.files.clear()
        refresh_files()

    async def choose_files() -> None:
        selected = await run.io_bound(_pick_files)
        if selected:
            add_paths([Path(item) for item in selected])

    async def choose_folder() -> None:
        selected = await run.io_bound(_pick_folder, "Escolher pasta com documentos")
        if selected:
            add_paths([Path(selected)])

    async def choose_export_dir() -> None:
        selected = await run.io_bound(_pick_folder, "Pasta dos textos anonimizados")
        if selected:
            session.export_dir = Path(selected)
            export_label.text = _dir_label("Textos anonimizados", session.export_dir)

    async def choose_private_dir() -> None:
        selected = await run.io_bound(_pick_folder, "Pasta dos mapas privados")
        if selected:
            session.private_dir = Path(selected)
            session.settings = load_settings(session.private_dir)
            _sync_target_list_text(session)
            case_companies.value = session.target_companies_text
            case_people.value = session.target_people_text
            case_ids.value = session.target_ids_text
            private_label.text = _dir_label(
                "Mapas privados (para reverter)", session.private_dir
            )
            local_toggle.value = session.settings.local_ner_enabled

    async def anonymize() -> None:
        if session.running:
            return
        if not session.files:
            ui.notify("Escolha pelo menos um arquivo.", type="warning")
            return
        missing = [path.name for path in session.files if not path.exists()]
        if missing:
            ui.notify(f"Arquivo nao encontrado: {', '.join(missing)}", type="negative")
            return

        session.settings.local_ner_enabled = bool(local_toggle.value)
        _commit_target_lists(
            session, case_companies.value, case_people.value, case_ids.value
        )
        save_settings(session.private_dir, session.settings)
        try:
            contextual = build_contextual_engine(
                session.settings,
                use_ner_cache=True,
                ner_cache_dir=default_ner_cache_dir(session.private_dir),
            )
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return

        session.running = True
        run_button.disable()
        progress.visible = True
        progress.value = 0
        result_area.clear()
        with result_area:
            ui.label("Processando...").classes("aj-muted")

        identifier = new_job_id()
        export_dir, map_path, split_warning = resolve_lote_dirs(
            session.export_dir, session.private_dir, identifier
        )
        export_dir.mkdir(parents=True, exist_ok=True)
        map_path.parent.mkdir(parents=True, exist_ok=True)
        mapping = PseudonymMap(token_labels=session.settings.token_labels())
        detector = StructuredDetector(recognizers=build_recognizers(session.settings))
        outcomes: list[FileOutcome] = []
        total = len(session.files)

        try:
            for index, source in enumerate(session.files):
                status.text = f"Extraindo {source.name} ({index + 1} de {total})"
                progress.value = index / total
                outcome = await run.io_bound(
                    process_file,
                    source,
                    export_dir=export_dir,
                    detector=detector,
                    mapping=mapping,
                    contextual=contextual,
                    exclusions=session.settings.exclusions,
                    use_cache=True,
                    cache_dir=session.private_dir / "cache_extracao",
                )
                outcomes.append(outcome)

            status.text = "Gravando mapa privado"
            progress.value = 0.97
            await run.io_bound(mapping.dump, map_path)
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
        except Exception as exc:  # noqa: BLE001
            status.text = "A execucao falhou."
            ui.notify(str(exc), type="negative")
            session.running = False
            run_button.enable()
            return

        job = build_job_result(
            job_id=identifier,
            export_dir=export_dir,
            map_path=map_path,
            outcomes=tuple(outcomes),
            record_count=len(mapping.records),
            contextual_used=contextual is not None,
            extra_warning=split_warning,
        )
        session.last_job = job
        progress.value = 1
        status.text = (
            f"Concluido: {len(job.succeeded)} ok, {len(job.failed)} com erro. "
            "Textos e mapa estao em pastas diferentes; veja o LEIA-ME.txt de cada uma."
        )
        _render_results(result_area, job)
        session.running = False
        run_button.enable()
        if job.failed:
            ui.notify("Alguns arquivos falharam. Veja o resultado abaixo.", type="warning")
        elif job.export_warning:
            ui.notify(job.export_warning, type="warning")
        else:
            ui.notify("Lote concluido.", type="positive")

    choose_files_btn.on_click(choose_files)
    choose_folder_btn.on_click(choose_folder)
    clear_btn.on_click(clear_files)
    change_export_btn.on_click(choose_export_dir)
    change_private_btn.on_click(choose_private_dir)
    run_button.on_click(anonymize)

    def save_case_targets() -> None:
        _commit_target_lists(
            session, case_companies.value, case_people.value, case_ids.value
        )
        save_settings(session.private_dir, session.settings)
        ui.notify("Lista deste caso salva.", type="positive")

    async def import_case_sheet() -> None:
        _commit_target_lists(
            session, case_companies.value, case_people.value, case_ids.value
        )
        selected = await run.io_bound(_pick_spreadsheet)
        if not selected:
            return
        try:
            message = _import_spreadsheet_into_session(session, Path(selected))
        except (OSError, ValueError) as exc:
            ui.notify(str(exc), type="warning")
            return
        case_companies.value = session.target_companies_text
        case_people.value = session.target_people_text
        case_ids.value = session.target_ids_text
        ui.notify(message, type="positive")

    save_case_btn.on_click(save_case_targets)
    import_case_btn.on_click(import_case_sheet)

    def submit_path() -> None:
        value = (path_input.value or "").strip().strip('"')
        if not value:
            ui.notify("Informe o caminho de um arquivo ou pasta.", type="warning")
            return
        add_paths([Path(value)])
        path_input.value = ""

    path_input.on("keydown.enter", lambda _: submit_path())
    add_path_btn.on_click(submit_path)
    refresh_files()


def _build_rules_tab(session: Session) -> None:
    _help(
        "Aqui voce desliga uma tag, inclui um padrao estreito ou protege termos "
        "que nao devem ser trocados. Os validadores de CPF, CNPJ e processo CNJ "
        "continuam no codigo; nesta aba so se liga ou desliga a categoria."
    )

    ui.label("Tags estruturadas").classes("text-h6 q-mt-md")
    tag_box = ui.column().classes("w-full")

    def persist() -> None:
        save_settings(session.private_dir, session.settings)

    def toggle_tag(entity_type: str, enabled: bool) -> None:
        session.settings.enabled_tags[entity_type] = bool(enabled)
        persist()

    def render_tags() -> None:
        tag_box.clear()
        with tag_box:
            for tag in STRUCTURED_TAGS:
                ui.checkbox(
                    f"{tag.token} — {tag.description}",
                    value=session.settings.enabled_tags.get(tag.entity_type, True),
                    on_change=lambda e, entity=tag.entity_type: toggle_tag(
                        entity, bool(e.value)
                    ),
                )

    ui.label("Regras personalizadas").classes("text-h6 q-mt-md")
    _help(
        "Prefira expressoes estreitas, com um rotulo (PIS, NIS) ou um bloco de "
        "digitos. Uma regex larga troca teses e pecas por tokens e prejudica a analise."
    )
    rule_name = ui.input("Nome da regra, por exemplo PIS").props("dense outlined").classes("w-full")
    rule_regex = ui.input(
        r"Expressao regular, por exemplo (?i)\bPIS\s+\d{3}\.\d{5}\.\d{2}-\d\b"
    ).props("dense outlined").classes("w-full")
    rule_label = ui.input("Rotulo do token, por exemplo PIS").props("dense outlined").classes(
        "w-full"
    )
    add_rule_btn = ui.button("Incluir regra", icon="add").props("unelevated no-caps")
    custom_list = ui.column().classes("w-full")

    def render_custom() -> None:
        custom_list.clear()
        with custom_list:
            if not session.settings.custom_rules:
                ui.label("Nenhuma regra personalizada.").classes("aj-muted")
                return
            for index, rule in enumerate(session.settings.custom_rules):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("col-grow"):
                        ui.label(f"{rule.name} → [{rule.token_label}_n]").classes(
                            "text-weight-medium"
                        )
                        ui.label(rule.regex).classes("aj-path aj-muted")
                    ui.button(
                        icon="close",
                        on_click=lambda i=index: remove_rule(i),
                    ).props("flat round dense")

    def add_rule() -> None:
        name = str(rule_name.value or "").strip()
        regex = str(rule_regex.value or "").strip()
        label = str(rule_label.value or "").strip() or name
        try:
            validate_custom_rule(name, regex, label)
            entity = slug_entity_type(name)
        except InvalidRuleError as exc:
            ui.notify(str(exc), type="warning")
            return
        session.settings.custom_rules.append(
            CustomRule(
                name=name,
                entity_type=entity,
                token_label=label.upper(),
                regex=regex,
                score=0.65,
            )
        )
        persist()
        rule_name.value = ""
        rule_regex.value = ""
        rule_label.value = ""
        render_custom()
        ui.notify("Regra incluida. Ela vale no proximo lote.", type="positive")

    def remove_rule(index: int) -> None:
        if 0 <= index < len(session.settings.custom_rules):
            session.settings.custom_rules.pop(index)
            persist()
            render_custom()

    add_rule_btn.on_click(add_rule)

    ui.label("Lista de alvos").classes("text-h6 q-mt-md")
    _help(
        "Nomes e CPF/CNPJ que devem sempre virar token neste caso. Um por linha, "
        "ou anexe a planilha do Nexus (.xlsx) com Nome e CPF/CNPJ. "
        "So entra documento com digito verificador. Orgaos protegidos "
        "(Uniao, PGFN) continuam intocados."
    )
    target_companies_box, target_people_box, target_ids_box = _bind_target_lists(session)
    with ui.row().classes("aj-actions q-mt-sm"):
        save_targets_btn = ui.button("Salvar lista de alvos", icon="save").props(
            "unelevated no-caps"
        )
        import_rules_btn = ui.button("Anexar planilha", icon="table_chart").props(
            "outline no-caps"
        )

    def save_targets() -> None:
        _commit_target_lists(
            session,
            target_companies_box.value,
            target_people_box.value,
            target_ids_box.value,
        )
        persist()
        ui.notify("Lista de alvos salva. Ela vale no proximo lote.", type="positive")

    async def import_rules_sheet() -> None:
        _commit_target_lists(
            session,
            target_companies_box.value,
            target_people_box.value,
            target_ids_box.value,
        )
        selected = await run.io_bound(_pick_spreadsheet)
        if not selected:
            return
        try:
            message = _import_spreadsheet_into_session(session, Path(selected))
        except (OSError, ValueError) as exc:
            ui.notify(str(exc), type="warning")
            return
        target_companies_box.value = session.target_companies_text
        target_people_box.value = session.target_people_text
        target_ids_box.value = session.target_ids_text
        ui.notify(message, type="positive")

    save_targets_btn.on_click(save_targets)
    import_rules_btn.on_click(import_rules_sheet)

    ui.label("Base local de CNPJ (regiao Sul)").classes("text-h6 q-mt-md")
    index = CnpjIndex(default_index_path(PROJECT_ROOT))
    stats = index.stats()
    if stats.get("presente") == "sim":
        ui.label(
            f"Indice encontrado: {stats.get('registros', '?')} estabelecimentos. "
            "Quando o Presidio achar um CNPJ, a razao social e tokenizada no texto inteiro. "
            "Orgaos (natureza 1xxx) nao viram empresa."
        ).classes("aj-muted")
    else:
        ui.label(
            "Nao ha indice da Receita. Para este caso, anexe a planilha do Nexus "
            "(Nome e CPF/CNPJ) nas listas acima. O indice anual da Receita continua "
            "opcional e separado."
        ).classes("aj-muted")
    cnpj_toggle = ui.checkbox(
        "Usar a base CNPJ quando o arquivo existir",
        value=session.settings.cnpj_lookup_enabled,
        on_change=lambda e: persist_cnpj_flag(bool(e.value)),
    )

    def persist_cnpj_flag(enabled: bool) -> None:
        session.settings.cnpj_lookup_enabled = enabled
        persist()

    def persist_shadow_flag(enabled: bool) -> None:
        session.settings.case_shadow_enabled = enabled
        persist()

    with ui.expansion("Experimento: passada-sombra de caixa (GLiNER)").classes("w-full"):
        ui.label(
            "Roda o GLiNER atual (limiar 0,75) sobre uma copia em que trechos "
            "TODO-MAIUSCULOS viram Title Case. O texto original nao muda. "
            "Deixe desligado ate medir a heuristica de capa. Um fator por vez."
        ).classes("aj-muted")
        ui.checkbox(
            "Ligar passada-sombra de caixa",
            value=session.settings.case_shadow_enabled,
            on_change=lambda e: persist_shadow_flag(bool(e.value)),
        )

    ui.label("Lista de exclusao").classes("text-h6 q-mt-md")
    _help(
        "Termos que nunca devem virar token — nomes de orgaos, teses ou pecas "
        "que o motor possa marcar por engano. Um por linha."
    )
    exclusions_box = ui.textarea(
        "Termos protegidos",
        value="\n".join(session.settings.exclusions),
    ).props("outlined").classes("w-full")
    save_excl_btn = ui.button("Salvar exclusoes", icon="save").props("unelevated no-caps")

    def save_exclusions() -> None:
        lines = str(exclusions_box.value or "").splitlines()
        session.settings.exclusions = [item.strip() for item in lines if item.strip()]
        persist()
        ui.notify("Lista de exclusao salva.", type="positive")

    save_excl_btn.on_click(save_exclusions)
    render_tags()
    render_custom()


def _render_results(container, job: JobResult) -> None:
    container.clear()
    with container:
        with ui.card().classes("w-full"):
            ui.label(f"Lote {job.job_id}").classes("text-h6")
            ui.label(job.export_block_reason).classes("aj-muted")
            if job.export_warning:
                with ui.card().classes("w-full bg-orange-1"):
                    ui.label(job.export_warning)

            ui.label("Conjunto deste lote").classes("text-weight-medium q-mt-sm")
            ui.label(
                "1. Textos anonimizados — o que voce le e copia. "
                f"Pasta: {job.export_dir}"
            ).classes("aj-path")
            for outcome in job.succeeded:
                if outcome.markdown_path is not None:
                    ui.label(f"   {outcome.markdown_path.name}").classes("aj-path")
            ui.label(
                "2. Mapa para reverter — a chave dos tokens. "
                f"Arquivo: {job.map_path} ({job.record_count} registros)"
            ).classes("aj-path")
            ui.label(
                "Em cada pasta ha um LEIA-ME.txt. "
                "A pasta dos textos tambem ganha um AUDITORIA.txt (conferencia automatica). "
                "Para reverter: no mapa.json, cada token aponta para o texto original."
            ).classes("aj-muted")

            with ui.row().classes("q-mt-sm"):
                ui.button(
                    "Abrir pasta dos textos anonimizados",
                    icon="folder_open",
                    on_click=lambda: _open_path(job.export_dir),
                ).props("unelevated no-caps")
                ui.button(
                    "Abrir pasta do mapa (reverter)",
                    icon="lock",
                    on_click=lambda: _open_path(job.map_path.parent),
                ).props("outline no-caps")
                if job.export_allowed:
                    ui.button(
                        "Copiar resultado para a area de transferencia",
                        icon="content_copy",
                        on_click=lambda: _copy_markdown(job),
                    ).props("unelevated color=positive no-caps")
                else:
                    blocked = ui.button(
                        "Copiar resultado para a area de transferencia",
                        icon="block",
                    ).props("disable")
                    blocked.tooltip(job.export_block_reason)

        for outcome in job.outcomes:
            with ui.card().classes("w-full"):
                _render_outcome(outcome)

        first_ok = next((item for item in job.outcomes if item.ok and item.markdown_path), None)
        if first_ok and first_ok.markdown_path is not None:
            preview = first_ok.markdown_path.read_text(encoding="utf-8")[:2500]
            with ui.expansion(f"Trecho de {first_ok.markdown_path.name}").classes("w-full"):
                ui.markdown(f"```\n{preview}\n```")


def _render_outcome(outcome: FileOutcome) -> None:
    ui.label(outcome.source.name).classes("text-weight-medium")
    if not outcome.ok:
        ui.label(outcome.error).classes("text-negative")
        return
    counts = ", ".join(
        f"{key}: {value}" for key, value in sorted(outcome.entity_counts.items())
    ) or "nenhum identificador estruturado"
    ui.label(
        f"{outcome.replacements} substituicao(oes) · {outcome.page_count} pagina(s) "
        f"· extrator {outcome.extractor} · {counts}"
    ).classes("aj-muted")
    if outcome.markdown_path is not None:
        ui.label(str(outcome.markdown_path)).classes("aj-path")
    if outcome.residual_hits:
        ui.label(
            "Revisao residual (nao substituido): " + ", ".join(outcome.residual_hits[:8])
        ).classes("aj-muted")


def _copy_markdown(job: JobResult) -> None:
    parts: list[str] = []
    for outcome in job.succeeded:
        if outcome.markdown_path is not None:
            parts.append(outcome.markdown_path.read_text(encoding="utf-8"))
    if not parts:
        ui.notify("Nao ha Markdown para copiar.", type="warning")
        return
    payload = json.dumps("\n\n---\n\n".join(parts))
    ui.run_javascript(f"navigator.clipboard.writeText({payload})")
    ui.notify("Resultado copiado para a area de transferencia. O mapa nao foi copiado.", type="positive")


def _app_manifest() -> str:
    from urllib.parse import quote

    payload = (
        '{"name":"Anonimizador Juridico","short_name":"Anonimizador",'
        f'"start_url":"{APP_URL}","display":"standalone",'
        '"background_color":"#f4f1ea","theme_color":"#1b365d"}'
    )
    return quote(payload, safe="")


def _dir_label(caption: str, path: Path) -> str:
    return f"{caption}: {path}"


def _pick_spreadsheet() -> str:
    root = _hidden_tk()
    try:
        from tkinter import filedialog

        selected = filedialog.askopenfilename(
            parent=root,
            title="Anexar planilha de nomes (Nexus)",
            filetypes=[
                ("Planilha", "*.xlsx *.xlsm *.csv"),
                ("Excel", "*.xlsx *.xlsm"),
                ("CSV", "*.csv"),
                ("Todos", "*.*"),
            ],
        )
        return selected or ""
    finally:
        root.destroy()


def _pick_files() -> list[str]:
    root = _hidden_tk()
    try:
        from tkinter import filedialog

        selected = filedialog.askopenfilenames(
            parent=root,
            title="Escolher documentos para anonimizar",
            filetypes=[
                ("Documentos suportados", "*.pdf *.docx *.txt *.md"),
                ("PDF", "*.pdf"),
                ("Word", "*.docx"),
                ("Texto", "*.txt *.md"),
                ("Todos", "*.*"),
            ],
        )
        return list(selected)
    finally:
        root.destroy()


def _pick_folder(title: str) -> str:
    root = _hidden_tk()
    try:
        from tkinter import filedialog

        return filedialog.askdirectory(parent=root, title=title) or ""
    finally:
        root.destroy()


def _hidden_tk():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    return root


def _open_path(path: Path) -> None:
    target = str(path)
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 - abertura local pedida pelo usuario
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, target])  # noqa: S603

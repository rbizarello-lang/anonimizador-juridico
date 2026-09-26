from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from .compat import patch_python314

patch_python314()

from nicegui import app, run, ui

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
from .version import version_info

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_URL = "/recursos"

# Identidade: o marinho e o papel do icone; o ocre e a tarja de redacao.
_NAVY = "#14305c"
_OCHRE = "#d19b15"

_FONT_FACES = "".join(
    f'@font-face{{font-family:"{family}";font-style:{style};font-weight:{weight};'
    f"font-display:swap;src:url({RESOURCES_URL}/fontes/{file}) format(\"woff2\")}}"
    for family, style, weight, file in (
        ("Source Sans 3", "normal", 400, "source-sans-3-latin-400-normal.woff2"),
        ("Source Sans 3", "normal", 600, "source-sans-3-latin-600-normal.woff2"),
        ("Source Sans 3", "normal", 700, "source-sans-3-latin-700-normal.woff2"),
        ("Source Serif 4", "normal", 400, "source-serif-4-latin-400-normal.woff2"),
        ("Source Serif 4", "italic", 400, "source-serif-4-latin-400-italic.woff2"),
        ("Source Serif 4", "normal", 600, "source-serif-4-latin-600-normal.woff2"),
        ("Source Serif 4", "normal", 700, "source-serif-4-latin-700-normal.woff2"),
    )
)

_APP_CSS = (
    "<style>"
    + _FONT_FACES
    + """
:root {
  --aj-ink: #1d2433; --aj-ink-2: #4b5160; --aj-muted: #6f695c;
  --aj-navy: #14305c; --aj-navy-2: #1d4280; --aj-navy-ink: #0e2345; --aj-navy-soft: #eaeff7;
  --aj-paper: #f5f0e6; --aj-sheet: #fffdf8; --aj-sheet-2: #faf6ee;
  --aj-line: #e6dccb; --aj-line-2: #d6c9b1; --aj-field: #948669;
  --aj-ochre: #d19b15; --aj-ochre-ink: #7d5900; --aj-ochre-soft: #f6e7bd;
  --aj-good: #2f6b4f; --aj-good-soft: #e4efe7;
  --aj-warn: #7a4f00; --aj-warn-soft: #fbefd3;
  --aj-bad: #8b2942; --aj-bad-soft: #f7e3e7;
  --aj-shadow: 0 1px 2px rgba(40, 32, 20, .05), 0 10px 28px -16px rgba(40, 32, 20, .28);
  --aj-serif: "Source Serif 4", Cambria, Georgia, serif;
  --aj-sans: "Source Sans 3", "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
  --aj-mono: "Cascadia Mono", "Cascadia Code", Consolas, ui-monospace, monospace;
}
html, body, #app, .q-layout, .q-page-container, .q-page { background: var(--aj-paper) !important; }
body { color: var(--aj-ink); font-family: var(--aj-sans); font-size: 15px; -webkit-font-smoothing: antialiased; }
.q-btn, .q-field, .q-tab, .q-toggle, .q-item, .q-notification, .q-tooltip, .q-expansion-item {
  font-family: var(--aj-sans) !important;
}
::selection { background: var(--aj-ochre-soft); }

/* Cabecalho */
.aj-header { background: var(--aj-navy) !important; padding: 0 !important;
  border-bottom: 2px solid rgba(209, 155, 21, .75); box-shadow: 0 6px 18px -12px rgba(14, 35, 69, .6); }
.aj-header-inner { max-width: 1180px; margin: 0 auto; padding: 0 24px; min-height: 64px; gap: 14px; width: 100%; }
.aj-mark { width: 34px; height: 34px; flex: none; display: block; }
.aj-brand { font-family: var(--aj-serif); font-weight: 600; font-size: 19px; color: #fff; line-height: 1.1; }
.aj-brand-sub { font-size: 12.5px; color: #c9d3e6; line-height: 1.25; }
.aj-version { font-size: 11px; color: #9fb0cf; line-height: 1.2; letter-spacing: .02em; cursor: default; }
.aj-nav .q-tab { color: #c9d3e6; min-height: 64px; padding: 0 16px; font-weight: 600; font-size: 15px; }
.aj-nav .q-tab--active { color: #fff; }
.aj-nav .q-tab__indicator { height: 4px; border-radius: 3px 3px 0 0; background: var(--aj-ochre) !important; }
.aj-nav .q-tab:hover { color: #fff; }
.aj-nav .q-tab__icon { font-size: 19px; }
.aj-nav .q-tab__label { font-size: 15px; }
.aj-header .q-btn--round { color: #c9d3e6; }
.aj-header .q-btn--round:hover { color: #fff; }

/* Pagina */
.aj-panels, .aj-panels > .q-panel > .q-tab-panel, .aj-panels .q-tab-panel { background: transparent !important; padding: 0 !important; }
.aj-page { max-width: 1180px; margin: 0 auto; padding: 30px 24px 72px; gap: 22px; width: 100%; }
.aj-head { gap: 6px; }
.aj-kicker { display: flex; align-items: center; gap: 10px; font-size: 12.5px; font-weight: 700;
  letter-spacing: .12em; text-transform: uppercase; color: var(--aj-ochre-ink); }
.aj-kicker::before { content: ""; width: 22px; height: 5px; border-radius: 2px; background: var(--aj-ochre); }
.aj-title { font-family: var(--aj-serif); font-weight: 600; font-size: 30px; line-height: 1.15; color: var(--aj-navy); }
.aj-lead { font-size: 16px; color: var(--aj-ink-2); line-height: 1.5; max-width: 66ch; }

/* Cartoes */
.aj-card { background: var(--aj-sheet); border: 1px solid var(--aj-line); border-radius: 16px;
  box-shadow: var(--aj-shadow); padding: 20px 22px; gap: 14px; width: 100%; }
.aj-card-title { font-family: var(--aj-serif); font-size: 19px; font-weight: 600; color: var(--aj-navy); line-height: 1.2; }
.aj-card-note { font-size: 13.5px; color: var(--aj-muted); line-height: 1.45; }
.aj-label { font-size: 12px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: var(--aj-muted); }
.aj-count { font-weight: 700; font-size: 12.5px; line-height: 1; color: var(--aj-navy); background: var(--aj-navy-soft);
  border-radius: 999px; padding: 5px 10px; white-space: nowrap; }
.aj-muted { color: var(--aj-muted); }
.aj-grow { flex: 1 1 auto; min-width: 0; }

/* Lote: duas colunas, painel de execucao fixo */
.aj-grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; grid-template-rows: auto 1fr;
  grid-template-areas: "queue side" "result side"; gap: 22px; align-items: start; width: 100%; }
.aj-area-queue { grid-area: queue; min-width: 0; }
.aj-area-result { grid-area: result; min-width: 0; }
.aj-side { grid-area: side; position: sticky; top: 86px; gap: 14px; min-width: 0; }
@media (max-width: 980px) {
  .aj-grid { grid-template-columns: minmax(0, 1fr); grid-template-rows: none; grid-template-areas: "queue" "side" "result"; }
  .aj-side { position: static; }
}
@media (max-width: 760px) { .aj-brand-sub { display: none; } }

/* Fila */
.aj-file { display: flex; align-items: center; gap: 14px; padding: 10px 10px 10px 12px; width: 100%;
  background: var(--aj-sheet-2); border: 1px solid var(--aj-line); border-radius: 12px; }
.aj-file:hover { border-color: var(--aj-line-2); }
.aj-doc { position: relative; width: 34px; height: 42px; flex: none; background: #fff; border: 1px solid var(--aj-line-2);
  border-radius: 3px 10px 3px 3px; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 4px;
  font-weight: 700; font-size: 8.5px; line-height: 1; letter-spacing: .06em; color: var(--aj-navy); }
.aj-doc::before { content: ""; position: absolute; left: 6px; right: 6px; top: 11px; height: 3px; border-radius: 1px;
  background: var(--aj-ochre); box-shadow: 0 6px 0 var(--aj-ochre); }
.aj-file-name { font-weight: 600; font-size: 15px; line-height: 1.25; color: var(--aj-ink); overflow-wrap: anywhere; }
.aj-path { font-family: var(--aj-mono); font-size: 12px; color: var(--aj-muted); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; direction: rtl; text-align: left; max-width: 100%; }
.aj-size { font-size: 13px; color: var(--aj-ink-2); white-space: nowrap; font-variant-numeric: tabular-nums; }
.aj-empty { border: 1.5px dashed var(--aj-line-2); border-radius: 14px; background: var(--aj-sheet-2);
  padding: 26px 20px; align-items: center; text-align: center; gap: 6px; width: 100%; }
.aj-empty-title { font-family: var(--aj-serif); font-size: 17px; font-weight: 600; color: var(--aj-navy); }
.aj-empty-text { font-size: 14px; color: var(--aj-muted); max-width: 46ch; }

/* Botoes e campos */
.q-btn { border-radius: 10px !important; font-weight: 600 !important; letter-spacing: .005em; }
.q-btn.aj-btn { min-height: 40px; padding: 0 16px; }
.q-btn.aj-btn .q-icon { font-size: 19px; }
.q-field--outlined .q-field__control { border-radius: 10px !important; background: #fff; }
.q-field--outlined .q-field__control:before { border-color: var(--aj-field) !important; }
.q-field--outlined.q-field--highlighted .q-field__control:after { border-color: var(--aj-navy) !important; }
.q-field__native, .q-field__input, .q-field__label { font-family: var(--aj-sans) !important; }
.aj-mono textarea, .aj-mono input { font-family: var(--aj-mono) !important; font-size: 13px !important; }
.q-toggle__label { color: var(--aj-ink); line-height: 1.35; }
.q-field__native::placeholder, .q-field__input::placeholder { color: #8a8272; font-style: italic; opacity: 1; }

/* Painel de execucao */
.aj-run { padding: 0 !important; gap: 0 !important; overflow: hidden; }
.aj-run-head { background: var(--aj-navy); color: #fff; padding: 18px 20px 18px; gap: 2px; position: relative; }
.aj-run-head::after { content: ""; position: absolute; right: 20px; top: 22px; width: 38px; height: 6px; border-radius: 2px;
  background: var(--aj-ochre); box-shadow: 0 10px 0 rgba(209, 155, 21, .55), 0 20px 0 rgba(209, 155, 21, .3); }
.aj-run-count { font-size: 26px; font-weight: 600; line-height: 1.15; }
.aj-run-sub { font-size: 13.5px; color: #c9d3e6; }
.aj-run-kicker { color: #c9d3e6 !important; }
.aj-cta.q-btn { margin-top: 14px; width: 100%; min-height: 52px; font-size: 16.5px; border-radius: 12px !important;
  background: var(--aj-ochre) !important; color: var(--aj-navy-ink) !important; }
.aj-cta.q-btn:hover { background: #dcaa2a !important; }
.aj-cta.q-btn .q-icon { font-size: 24px; }
.aj-run-head .aj-progress { margin-top: 12px; }
.aj-run-head .aj-progress .q-linear-progress__track { background: rgba(255, 255, 255, .18) !important; }
.aj-run-status { font-size: 13.5px; color: #dfe6f2; line-height: 1.4; margin-top: 8px; }
.aj-run-body { padding: 6px 20px 20px; gap: 0; }
.aj-sec { border-top: 1px solid var(--aj-line); padding: 14px 0; gap: 8px; width: 100%; }
.aj-sec:first-child { border-top: none; }
.aj-dest { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; align-items: center; gap: 4px 10px; width: 100%; }
.aj-sec .q-btn--flat .q-icon { font-size: 17px; }
.aj-dest .q-icon { color: var(--aj-navy); font-size: 19px; }
.aj-dest-name { font-size: 14px; font-weight: 600; color: var(--aj-ink); line-height: 1.25; }
.aj-progress { height: 8px; border-radius: 99px; overflow: hidden; }
.aj-progress .q-linear-progress__model { background: var(--aj-ochre) !important; }
.aj-progress .q-linear-progress__track { background: #eadfc8 !important; opacity: 1 !important; }
.aj-status-line { font-size: 13.5px; color: var(--aj-ink-2); line-height: 1.4; }
.aj-principle { gap: 8px; font-size: 13.5px; color: var(--aj-muted); line-height: 1.45; padding: 0 6px; }
.aj-principle .q-icon { color: var(--aj-ochre-ink); font-size: 18px; margin-top: 1px; }

/* Resultado */
.aj-result-card { scroll-margin-top: 90px; }
.aj-status { display: inline-flex; align-items: center; gap: 6px; font-weight: 700; font-size: 13px;
  border-radius: 999px; padding: 5px 12px 5px 9px; white-space: nowrap; }
.aj-status .q-icon { font-size: 17px; }
.aj-status.ok { background: var(--aj-good-soft); color: var(--aj-good); }
.aj-status.warn { background: var(--aj-warn-soft); color: var(--aj-warn); }
.aj-status.bad { background: var(--aj-bad-soft); color: var(--aj-bad); }
.aj-lote-id { font-family: var(--aj-mono); font-size: 12.5px; color: var(--aj-muted); }
.aj-callout { border-radius: 12px; padding: 10px 14px; font-size: 14px; line-height: 1.45; gap: 10px; width: 100%; flex-wrap: nowrap; }
.aj-callout .q-icon { font-size: 19px; margin-top: 1px; }
.aj-callout.warn { background: var(--aj-warn-soft); color: #5c3d00; border: 1px solid #efd9a3; }
.aj-callout.info { background: var(--aj-navy-soft); color: var(--aj-navy-ink); border: 1px solid #d6dfec; }
.aj-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; width: 100%; }
@media (max-width: 700px) { .aj-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.aj-kpi { background: var(--aj-sheet-2); border: 1px solid var(--aj-line); border-radius: 12px; padding: 12px 14px; gap: 2px; }
.aj-kpi-label { font-size: 13px; color: var(--aj-muted); }
.aj-kpi-value { font-size: 26px; font-weight: 600; line-height: 1.15; color: var(--aj-ink); }
.aj-kpi-sub { font-size: 12.5px; color: var(--aj-muted); }
.aj-chips { display: flex; flex-wrap: wrap; gap: 6px; width: 100%; }
.aj-chip { display: inline-flex; align-items: center; gap: 7px; font-family: var(--aj-mono); font-size: 12px;
  font-weight: 600; color: var(--aj-ink); background: #fff; border: 1px solid var(--aj-line-2);
  border-radius: 999px; padding: 3px 10px 3px 7px; }
.aj-chip::before { content: ""; width: 12px; height: 4px; border-radius: 1px; background: var(--aj-ochre); }
.aj-chip b { font-family: var(--aj-sans); font-size: 12.5px; }
.aj-out { display: grid; grid-template-columns: 22px minmax(0, 1fr); gap: 4px 10px; padding: 12px 0;
  border-top: 1px solid var(--aj-line); width: 100%; }
.aj-out > .q-icon { font-size: 20px; margin-top: 1px; }
.aj-out-meta { font-size: 13.5px; color: var(--aj-ink-2); }
.aj-sets { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; width: 100%; }
@media (max-width: 700px) { .aj-sets { grid-template-columns: minmax(0, 1fr); } }
.aj-set { background: var(--aj-sheet-2); border: 1px solid var(--aj-line); border-radius: 12px; padding: 12px 14px; gap: 6px; }
.aj-set-title { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 14px; color: var(--aj-ink); }
.aj-set-title .q-icon { color: var(--aj-navy); font-size: 18px; }
.aj-set.private .aj-set-title .q-icon { color: var(--aj-bad); }
.aj-preview { background: #fffefa; border: 1px solid var(--aj-line); border-radius: 12px; padding: 18px 20px;
  max-height: 440px; overflow: auto; font-family: var(--aj-serif); font-size: 14.5px; line-height: 1.7;
  color: var(--aj-ink); white-space: pre-wrap; overflow-wrap: anywhere; width: 100%; }
.aj-tarja { display: inline-block; background: var(--aj-ochre); color: var(--aj-navy-ink); font-family: var(--aj-mono);
  font-size: 11px; font-weight: 700; letter-spacing: .04em; line-height: 1; padding: 3px 6px 3px 6px;
  border-radius: 3px; vertical-align: 1px; white-space: nowrap; }
.aj-tarja sub { font-size: 9px; vertical-align: baseline; opacity: .78; margin-left: 1px; }
.aj-pagemark { display: block; margin: 8px 0 2px; font-family: var(--aj-sans); font-size: 11px; font-weight: 700;
  letter-spacing: .1em; text-transform: uppercase; color: var(--aj-muted); border-top: 1px dashed var(--aj-line-2); padding-top: 6px; }
.aj-comment { color: var(--aj-muted); font-family: var(--aj-mono); font-size: 11.5px; }
.q-expansion-item { border-radius: 12px; }
.q-expansion-item .q-item { padding-left: 4px; padding-right: 4px; border-radius: 10px; font-weight: 600; color: var(--aj-navy); }

/* Caso */
.aj-toolbar { gap: 10px; align-items: center; flex-wrap: wrap; width: 100%; }
.aj-save-state { display: inline-flex; align-items: center; gap: 8px; font-size: 13.5px; color: var(--aj-muted); }
.aj-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.aj-dot.ok { background: var(--aj-good); }
.aj-dot.warn { background: var(--aj-ochre); }
.aj-case-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; width: 100%; }
@media (max-width: 900px) { .aj-case-grid { grid-template-columns: minmax(0, 1fr); } }
.aj-list { padding: 16px 16px 14px; gap: 10px; }
.aj-list-head { gap: 8px; align-items: center; width: 100%; }
.aj-list-head .q-icon { color: var(--aj-navy); font-size: 20px; }
.aj-list textarea { line-height: 1.5 !important; }

/* Regras */
.aj-groups { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 28px; width: 100%; }
@media (max-width: 860px) { .aj-groups { grid-template-columns: minmax(0, 1fr); } }
.aj-group { gap: 0; width: 100%; }
.aj-group .aj-label { padding: 12px 0 6px; }
.aj-tag { display: grid; grid-template-columns: 140px minmax(0, 1fr); align-items: center; gap: 6px;
  border-top: 1px solid var(--aj-line); padding: 3px 0; width: 100%; }
.aj-token { font-family: var(--aj-mono); font-size: 12px; font-weight: 600; color: var(--aj-navy); background: var(--aj-navy-soft);
  border: 1px solid #d6dfec; border-radius: 6px; padding: 3px 7px; justify-self: start; white-space: nowrap; }
.aj-tag .q-toggle__label { font-size: 14px; }
.aj-two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px; align-items: start; width: 100%; }
@media (max-width: 900px) { .aj-two { grid-template-columns: minmax(0, 1fr); } }
.aj-col { gap: 22px; min-width: 0; }
.aj-rule { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 2px 10px;
  border-top: 1px solid var(--aj-line); padding: 10px 0; width: 100%; }
.aj-rule-name { font-weight: 600; color: var(--aj-ink); }
.aj-code { font-family: var(--aj-mono); font-size: 12px; color: var(--aj-ink-2); overflow-wrap: anywhere; }

/* Dialogo */
.aj-dialog { width: min(780px, 94vw); max-width: 780px !important; border-radius: 18px !important; padding: 26px 28px !important;
  background: var(--aj-sheet) !important; gap: 16px; }
.aj-dialog h3 { font-family: var(--aj-serif); font-size: 16px; font-weight: 600; color: var(--aj-navy); margin: 0; }
.aj-dialog p { margin: 0; line-height: 1.5; color: var(--aj-ink-2); font-size: 14.5px; }
.aj-dialog ul { margin: 4px 0 0; padding-left: 0; list-style: none; }
.aj-dialog li { display: grid; grid-template-columns: 170px minmax(0, 1fr); gap: 10px; padding: 5px 0; border-top: 1px solid var(--aj-line);
  font-size: 14px; color: var(--aj-ink-2); }
.aj-dialog li code { font-family: var(--aj-mono); font-size: 12px; font-weight: 600; color: var(--aj-navy); }
.q-notification { border-radius: 12px !important; }
</style>
"""
)

_BRAND_MARK = f"""
<svg class="aj-mark" viewBox="0 0 34 34" role="img" aria-label="Anonimizador Jurídico">
  <path d="M8 4.5h12.5l6.5 6.5v18a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1v-23.5a1 1 0 0 1 1-1z" fill="#f5f0e6"/>
  <path d="M20.5 4.5V11H27z" fill="#dccfb3"/>
  <rect x="10.5" y="14.2" width="12.5" height="2.8" rx=".7" fill="{_OCHRE}"/>
  <rect x="10.5" y="19.2" width="12.5" height="2.8" rx=".7" fill="{_OCHRE}"/>
  <rect x="10.5" y="24.2" width="8" height="2.8" rx=".7" fill="{_OCHRE}"/>
</svg>
"""

_EMPTY_ART = f"""
<svg width="86" height="64" viewBox="0 0 86 64" aria-hidden="true">
  <rect x="30" y="4" width="36" height="46" rx="3" fill="#fff" stroke="#d6c9b1" transform="rotate(8 48 27)"/>
  <rect x="18" y="8" width="38" height="50" rx="3" fill="#fff" stroke="#d6c9b1"/>
  <rect x="24" y="20" width="26" height="4" rx="1" fill="{_OCHRE}"/>
  <rect x="24" y="29" width="26" height="4" rx="1" fill="{_OCHRE}"/>
  <rect x="24" y="38" width="16" height="4" rx="1" fill="{_OCHRE}"/>
  <rect x="24" y="47" width="20" height="2" rx="1" fill="#e6dccb"/>
</svg>
"""

# Etiquetas estruturadas agrupadas por assunto na aba Regras. O que nao
# estiver aqui cai em "Outros" (nada some se o catalogo crescer).
_TAG_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Pessoa física", ("BR_CPF", "BR_RG", "BR_PIS", "BR_TITULO_ELEITOR", "BR_NASCIMENTO")),
    ("Empresa e processo", ("BR_CNPJ", "BR_PROCESSO", "BR_OAB", "BR_EPROC")),
    ("Contato e endereço", ("EMAIL_ADDRESS", "BR_PHONE", "BR_CEP")),
    (
        "Bens e contas",
        (
            "BR_MATRICULA_IMOVEL",
            "BR_PIX",
            "BR_CONTA_BANCARIA",
            "BR_RENAVAM",
            "BR_PLACA",
            "BR_CHASSI",
        ),
    ),
)

_TOKEN_IN_TEXT = re.compile(r"\[([A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9_]+?)_(\d+)\]")
_PAGE_MARK = re.compile(r"&lt;!--\s*pagina-origem:\s*(\d+)\s*--&gt;\n?")
_OTHER_COMMENT = re.compile(r"&lt;!--.*?--&gt;", re.DOTALL)
_PREVIEW_CHARS = 3000


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

    resources = PROJECT_ROOT / "recursos"
    if resources.is_dir():
        app.add_static_files(RESOURCES_URL, resources)
    ui.dark_mode(False)
    ui.colors(
        primary=_NAVY,
        secondary="#7d5900",
        accent=_OCHRE,
        positive="#2f6b4f",
        negative="#8b2942",
        warning="#7a4f00",
        info=_NAVY,
    )
    ui.add_head_html(
        f"""
        <meta name="theme-color" content="{_NAVY}">
        <meta name="application-name" content="Anonimizador Juridico">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="mobile-web-app-capable" content="yes">
        <link rel="manifest" href="data:application/manifest+json,{_app_manifest()}">
        {_APP_CSS}
        """
    )

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
        kwargs["window_size"] = (1180, 820)
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
    help_dialog = _build_help_dialog()
    with ui.header().classes("aj-header"):
        with ui.row().classes("aj-header-inner items-center no-wrap"):
            ui.html(_BRAND_MARK)
            with ui.column().classes("gap-0"):
                ui.label("Anonimizador Jurídico").classes("aj-brand")
                ui.label("Pseudonimização local de peças").classes("aj-brand-sub")
                # Versao do codigo carregado: confere com a AUDITORIA.txt de cada lote.
                ui.label(version_info().short()).classes("aj-version").tooltip(
                    version_info().line()
                )
            ui.space()
            with ui.tabs().props("no-caps inline-label dense").classes("aj-nav") as tabs:
                lote_tab = ui.tab("lote", label="Lote", icon="folder_copy")
                caso_tab = ui.tab("caso", label="Caso", icon="badge")
                regras_tab = ui.tab("regras", label="Regras", icon="tune")
            ui.space()
            ui.button(icon="help_outline", on_click=help_dialog.open).props(
                'flat round aria-label="Como funciona"'
            ).tooltip("Como funciona")

    hooks: dict[str, Callable[[], None]] = {}
    with ui.tab_panels(tabs, value=lote_tab).classes("aj-panels w-full"):
        with ui.tab_panel(lote_tab):
            case_changed = _build_lote_tab(
                session,
                open_case=lambda: tabs.set_value("caso"),
                case_saved=lambda: hooks.get("case_saved", lambda: None)(),
            )
        with ui.tab_panel(caso_tab):
            hooks["case_saved"] = _build_case_tab(session, on_change=case_changed)
        with ui.tab_panel(regras_tab):
            _build_rules_tab(session)


def _page_head(kicker: str, title: str, lead: str) -> None:
    with ui.column().classes("aj-head"):
        ui.label(kicker).classes("aj-kicker")
        ui.label(title).classes("aj-title")
        ui.label(lead).classes("aj-lead")


def _build_lote_tab(
    session: Session,
    *,
    open_case: Callable[[], None],
    case_saved: Callable[[], None],
) -> Callable[[], None]:
    """Fila e resultado a esquerda; painel de execucao fixo a direita.

    Devolve a funcao que atualiza o resumo do caso (chamada pela aba Caso).
    """

    with ui.column().classes("aj-page"):
        _page_head(
            "Lote de documentos",
            "Anonimizar documentos",
            "Os identificadores viram tokens estáveis; o mapa para reverter fica em outra pasta, "
            "longe do texto que você envia para análise.",
        )
        with ui.element("div").classes("aj-grid"):
            with ui.column().classes("aj-card aj-area-queue"):
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.label("Arquivos na fila").classes("aj-card-title")
                    ui.space()
                    queue_badge = ui.label("vazia").classes("aj-count")
                file_list = ui.column().classes("w-full gap-2")
                with ui.row().classes("aj-toolbar"):
                    choose_files_btn = ui.button(
                        "Escolher arquivos", icon="note_add"
                    ).props("unelevated no-caps").classes("aj-btn")
                    choose_folder_btn = ui.button(
                        "Adicionar pasta", icon="create_new_folder"
                    ).props("outline no-caps").classes("aj-btn")
                    clear_btn = ui.button("Limpar fila", icon="delete_sweep").props(
                        "flat no-caps color=negative"
                    ).classes("aj-btn")
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    path_input = (
                        ui.input(placeholder="Ou cole o caminho de um arquivo ou pasta")
                        .props("outlined dense clearable")
                        .classes("aj-grow")
                    )
                    add_path_btn = ui.button("Adicionar", icon="add").props(
                        "outline no-caps"
                    ).classes("aj-btn")
                ui.label(
                    "PDF, DOCX, TXT ou MD. Pode escolher várias PARTES de uma vez; o app "
                    "processa uma após a outra, com até 15 minutos de extração por arquivo. "
                    "No mesmo lote, o mesmo dado vira o mesmo token em todos os arquivos."
                ).classes("aj-card-note")

            with ui.column().classes("aj-card aj-result-card aj-area-result"):
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.label("Resultado").classes("aj-card-title")
                result_area = ui.column().classes("w-full gap-3")
                with result_area:
                    _result_placeholder()

            with ui.column().classes("aj-side"):
                with ui.column().classes("aj-card aj-run"):
                    with ui.column().classes("aj-run-head w-full"):
                        run_kicker = ui.label("Fila vazia").classes("aj-label aj-run-kicker")
                        run_count = ui.label("Nenhum arquivo").classes("aj-run-count")
                        run_size = ui.label("Escolha os documentos do lote.").classes("aj-run-sub")
                        run_button = ui.button("Anonimizar lote", icon="play_arrow").props(
                            "unelevated no-caps"
                        ).classes("aj-cta")
                        progress = ui.linear_progress(value=0, show_value=False).props(
                            "instant-feedback"
                        ).classes("aj-progress w-full")
                        progress.visible = False
                        status = ui.label("Nenhum arquivo selecionado.").classes("aj-run-status")
                    with ui.column().classes("aj-run-body w-full"):
                        with ui.column().classes("aj-sec"):
                            with ui.row().classes("w-full items-center no-wrap"):
                                ui.label("Caso").classes("aj-label")
                                ui.space()
                                ui.button("Editar nomes", icon="edit", on_click=open_case).props(
                                    "flat dense no-caps"
                                )
                            case_summary = ui.label("").classes("aj-status-line")
                        with ui.column().classes("aj-sec"):
                            ui.label("Destinos").classes("aj-label")
                            with ui.element("div").classes("aj-dest"):
                                ui.icon("description")
                                with ui.column().classes("gap-0 aj-grow"):
                                    ui.label("Textos anonimizados").classes("aj-dest-name")
                                    export_label = ui.label("").classes("aj-path")
                                change_export_btn = ui.button("Trocar").props("flat dense no-caps")
                                change_export_btn.tooltip("Trocar a pasta dos textos anonimizados")
                            with ui.element("div").classes("aj-dest"):
                                ui.icon("lock")
                                with ui.column().classes("gap-0 aj-grow"):
                                    ui.label("Mapa para reverter (privado)").classes("aj-dest-name")
                                    private_label = ui.label("").classes("aj-path")
                                change_private_btn = ui.button("Trocar").props("flat dense no-caps")
                                change_private_btn.tooltip("Trocar a pasta dos mapas privados")
                            ui.label(
                                "Pastas separadas: a chave não viaja com o texto."
                            ).classes("aj-card-note")
                        with ui.column().classes("aj-sec"):
                            ui.label("Motor automático").classes("aj-label")
                            local_toggle = ui.switch(
                                "Motor local (GLiNER · spaCy PT)",
                                value=session.settings.local_ner_enabled,
                            ).props("color=primary")
                            ui.label(
                                "Roda neste computador. Na dúvida, o trecho original fica."
                            ).classes("aj-card-note")
                with ui.row().classes("aj-principle no-wrap items-start"):
                    ui.icon("verified_user")
                    ui.label(
                        "Datas, valores, eventos, teses e órgãos permanecem. "
                        "Um dado que escape é menos grave do que descaracterizar a peça."
                    )

    def update_destinations() -> None:
        export_label.text = _path_display(session.export_dir)
        private_label.text = _path_display(session.private_dir)
        export_label.tooltip(str(session.export_dir))
        private_label.tooltip(str(session.private_dir))

    def update_case_summary() -> None:
        companies = len(_parse_name_list(session.target_companies_text))
        people = len(_parse_name_list(session.target_people_text))
        ids = len(parse_target_ids([str(session.target_ids_text or "")]))
        if not (companies or people or ids):
            case_summary.text = (
                "Nenhum nome informado: só os detectores automáticos atuam."
            )
            return
        case_summary.text = (
            f"{_plural(companies, 'empresa', 'empresas')} · "
            f"{_plural(people, 'pessoa', 'pessoas')} · "
            f"{_plural(ids, 'CPF/CNPJ', 'CPF/CNPJ')}"
        )

    def refresh_files() -> None:
        file_list.clear()
        total_bytes = sum(_file_size(path) for path in session.files)
        with file_list:
            if not session.files:
                with ui.column().classes("aj-empty"):
                    ui.html(_EMPTY_ART)
                    ui.label("Nenhum arquivo na fila").classes("aj-empty-title")
                    ui.label(
                        "Escolha os PDFs do processo — por exemplo, as PARTES exportadas "
                        "do eproc — ou cole o caminho de uma pasta."
                    ).classes("aj-empty-text")
            for path in list(session.files):
                with ui.element("div").classes("aj-file"):
                    ui.label(_doc_kind(path)).classes("aj-doc")
                    with ui.column().classes("gap-0 aj-grow"):
                        ui.label(path.name).classes("aj-file-name")
                        ui.label(_path_display(path.parent)).classes("aj-path").tooltip(
                            str(path)
                        )
                    ui.label(_fmt_bytes(_file_size(path))).classes("aj-size")
                    remover = ui.button(
                        icon="close",
                        on_click=lambda p=path: remove_file(p),
                    ).props('flat round dense aria-label="Retirar da fila"')
                    remover.tooltip("Retirar da fila")
        count = len(session.files)
        if count:
            queue_badge.text = _plural(count, "arquivo", "arquivos")
            run_count.text = _plural(count, "arquivo", "arquivos")
            run_size.text = f"{_fmt_bytes(total_bytes)} no total"
            if not session.running:
                run_kicker.text = "Pronto para executar"
                status.text = "Confira o caso e os destinos abaixo."
        else:
            queue_badge.text = "vazia"
            run_count.text = "Nenhum arquivo"
            run_size.text = "Escolha os documentos do lote."
            if not session.running:
                run_kicker.text = "Fila vazia"
                status.text = "Nenhum arquivo selecionado."

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
        if session.running:
            ui.notify("Espere o lote terminar para mexer na fila.", type="warning")
            return
        session.files = [item for item in session.files if item != path]
        refresh_files()

    def clear_files() -> None:
        if session.running:
            ui.notify("Espere o lote terminar para mexer na fila.", type="warning")
            return
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
            update_destinations()

    async def choose_private_dir() -> None:
        selected = await run.io_bound(_pick_folder, "Pasta dos mapas privados")
        if selected:
            session.private_dir = Path(selected)
            session.settings = load_settings(session.private_dir)
            _sync_target_list_text(session)
            local_toggle.value = session.settings.local_ner_enabled
            update_destinations()
            update_case_summary()
            case_saved()

    async def anonymize() -> None:
        if session.running:
            return
        if not session.files:
            ui.notify("Escolha pelo menos um arquivo.", type="warning")
            return
        missing = [path.name for path in session.files if not path.exists()]
        if missing:
            ui.notify(f"Arquivo não encontrado: {', '.join(missing)}", type="negative")
            return

        session.settings.local_ner_enabled = bool(local_toggle.value)
        _commit_target_lists(
            session,
            session.target_companies_text,
            session.target_people_text,
            session.target_ids_text,
        )
        save_settings(session.private_dir, session.settings)
        update_case_summary()
        case_saved()
        try:
            contextual = build_contextual_engine(
                session.settings,
                use_ner_cache=True,
                ner_cache_dir=default_ner_cache_dir(session.private_dir),
            )
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return

        # Copia da fila: mexer nela durante o lote nao muda o que esta rodando.
        sources = list(session.files)
        session.running = True
        run_kicker.text = "Executando"
        run_button.disable()
        progress.visible = True
        progress.value = 0
        result_area.clear()
        with result_area:
            with ui.row().classes("items-center gap-3"):
                ui.spinner(size="md", color="accent")
                ui.label("Processando o lote…").classes("aj-muted")

        identifier = new_job_id()
        export_dir, map_path, split_warning = resolve_lote_dirs(
            session.export_dir, session.private_dir, identifier
        )
        export_dir.mkdir(parents=True, exist_ok=True)
        map_path.parent.mkdir(parents=True, exist_ok=True)
        mapping = PseudonymMap(token_labels=session.settings.token_labels())
        detector = StructuredDetector(recognizers=build_recognizers(session.settings))
        outcomes: list[FileOutcome] = []
        total = len(sources)

        try:
            for index, source in enumerate(sources):
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
            f"Concluído: {len(job.succeeded)} ok, {len(job.failed)} com erro. "
            "Textos e mapa estão em pastas diferentes; veja o LEIA-ME.txt de cada uma."
        )
        _render_results(result_area, job, _token_labels(session))
        session.running = False
        run_kicker.text = "Concluído"
        run_button.enable()
        ui.run_javascript(
            "document.querySelector('.aj-result-card')"
            "?.scrollIntoView({behavior: 'smooth', block: 'start'})"
        )
        if job.failed:
            ui.notify("Alguns arquivos falharam. Veja o resultado.", type="warning")
        elif job.export_warning:
            ui.notify(job.export_warning, type="warning")
        else:
            ui.notify("Lote concluído.", type="positive")

    def submit_path() -> None:
        value = (path_input.value or "").strip().strip('"')
        if not value:
            ui.notify("Informe o caminho de um arquivo ou pasta.", type="warning")
            return
        add_paths([Path(value)])
        path_input.value = ""

    choose_files_btn.on_click(choose_files)
    choose_folder_btn.on_click(choose_folder)
    clear_btn.on_click(clear_files)
    change_export_btn.on_click(choose_export_dir)
    change_private_btn.on_click(choose_private_dir)
    run_button.on_click(anonymize)
    path_input.on("keydown.enter", lambda _: submit_path())
    add_path_btn.on_click(submit_path)

    update_destinations()
    update_case_summary()
    refresh_files()
    return update_case_summary


def _build_case_tab(session: Session, *, on_change: Callable[[], None]) -> Callable[[], None]:
    """Listas do caso num lugar so. Devolve a funcao que redesenha o estado 'salvo'."""

    with ui.column().classes("aj-page"):
        _page_head(
            "Caso",
            "Nomes e documentos deste caso",
            "Um por linha. O app troca cada nome onde ele aparecer, em todos os arquivos do lote — "
            "inclusive em CAIXA ALTA, sem acento ou quebrado entre linhas.",
        )
        with ui.row().classes("aj-toolbar"):
            import_btn = ui.button("Anexar planilha do Nexus", icon="table_chart").props(
                "outline no-caps"
            ).classes("aj-btn")
            save_btn = ui.button("Salvar lista", icon="save").props("unelevated no-caps").classes(
                "aj-btn"
            )
            save_state = ui.html("").classes("aj-save-state")

        with ui.element("div").classes("aj-case-grid"):
            companies = _list_card(
                session,
                title="Empresas",
                icon="domain",
                attribute="target_companies_text",
                placeholder="ex.: MADEIREIRA EXEMPLO LTDA\nex.: Outra Empresa S.A.",
                hint="Razão social como aparece na peça. Sufixo societário (LTDA, ME, EPP) é opcional.",
                count=lambda text: len(_parse_name_list(text)),
            )
            people = _list_card(
                session,
                title="Pessoas",
                icon="person",
                attribute="target_people_text",
                placeholder="ex.: Maria Exemplo da Silva\nex.: JOÃO FICTÍCIO SOUZA",
                hint="Nome completo. Sobrenome comum (Silva, Souza) sozinho só é trocado com rótulo de parte.",
                count=lambda text: len(_parse_name_list(text)),
            )
            ids = _list_card(
                session,
                title="CPF e CNPJ da carteira",
                icon="fingerprint",
                attribute="target_ids_text",
                placeholder="ex.: 000.000.000-00\nex.: 00.000.000/0000-00",
                hint="Só entra número com dígito verificador válido. CNPJ de órgão público (União) é ignorado.",
                count=lambda text: len(parse_target_ids([str(text or "")])),
                mono=True,
            )

        with ui.row().classes("aj-callout info items-start"):
            ui.icon("shield")
            ui.label(
                "União, PGFN e a lista de termos protegidos (aba Regras) nunca são trocados. "
                "CPF e CNPJ que aparecem na peça já viram token sozinhos; a planilha serve "
                "sobretudo para os nomes."
            )

    def is_dirty() -> bool:
        return (
            _parse_name_list(session.target_companies_text) != session.settings.target_companies
            or _parse_name_list(session.target_people_text) != session.settings.target_people
            or list(parse_target_ids([str(session.target_ids_text or "")]))
            != list(session.settings.target_ids)
        )

    def refresh_state() -> None:
        if is_dirty():
            save_state.content = '<span class="aj-dot warn"></span>Alterações não salvas'
        else:
            save_state.content = '<span class="aj-dot ok"></span>Lista salva'
        on_change()

    def save_case_targets() -> None:
        _commit_target_lists(session, companies.value, people.value, ids.value)
        save_settings(session.private_dir, session.settings)
        refresh_state()
        ui.notify("Lista deste caso salva.", type="positive")

    async def import_case_sheet() -> None:
        _commit_target_lists(session, companies.value, people.value, ids.value)
        selected = await run.io_bound(_pick_spreadsheet)
        if not selected:
            refresh_state()
            return
        try:
            message = _import_spreadsheet_into_session(session, Path(selected))
        except (OSError, ValueError) as exc:
            ui.notify(str(exc), type="warning")
            return
        companies.value = session.target_companies_text
        people.value = session.target_people_text
        ids.value = session.target_ids_text
        refresh_state()
        ui.notify(message, type="positive")

    for box in (companies, people, ids):
        box.on_value_change(lambda _event: refresh_state())
    save_btn.on_click(save_case_targets)
    import_btn.on_click(import_case_sheet)
    refresh_state()
    return refresh_state


def _list_card(
    session: Session,
    *,
    title: str,
    icon: str,
    attribute: str,
    placeholder: str,
    hint: str,
    count: Callable[[str], int],
    mono: bool = False,
):
    with ui.column().classes("aj-card aj-list"):
        with ui.row().classes("aj-list-head no-wrap"):
            ui.icon(icon)
            ui.label(title).classes("aj-card-title").style("font-size:17px")
            ui.space()
            ui.label().classes("aj-count").bind_text_from(
                session, attribute, backward=lambda text: str(count(text))
            )
        box = (
            ui.textarea(placeholder=placeholder)
            .props("outlined rows=12")
            .classes("w-full" + (" aj-mono" if mono else ""))
            .bind_value(session, attribute)
        )
        ui.label(hint).classes("aj-card-note")
    return box


def _build_rules_tab(session: Session) -> None:
    def persist() -> None:
        save_settings(session.private_dir, session.settings)

    with ui.column().classes("aj-page"):
        _page_head(
            "Regras",
            "O que o app procura",
            "Desligue uma categoria, inclua um padrão estreito ou proteja termos que não devem "
            "ser trocados. Os validadores de CPF, CNPJ e processo CNJ continuam no código; "
            "aqui só se liga ou desliga a categoria.",
        )

        with ui.column().classes("aj-card"):
            with ui.row().classes("w-full items-center no-wrap"):
                ui.label("Identificadores").classes("aj-card-title")
                ui.space()
                enabled_badge = ui.label("").classes("aj-count")
            ui.label(
                "Detectados pelo Presidio, com dígito verificador ou com rótulo no texto. "
                "Valem no próximo lote."
            ).classes("aj-card-note")
            groups_box = ui.element("div").classes("aj-groups")

        with ui.element("div").classes("aj-two"):
            with ui.column().classes("aj-col"):
                with ui.column().classes("aj-card"):
                    ui.label("Regras personalizadas").classes("aj-card-title")
                    ui.label(
                        "Prefira expressões estreitas, com um rótulo (PIS, NIS) ou um bloco de "
                        "dígitos. Uma regex larga troca teses e peças por tokens e prejudica a análise."
                    ).classes("aj-card-note")
                    with ui.row().classes("w-full no-wrap gap-2"):
                        rule_name = ui.input("Nome da regra", placeholder="PIS").props(
                            "outlined dense"
                        ).classes("aj-grow")
                        rule_label = ui.input("Rótulo do token", placeholder="PIS").props(
                            "outlined dense"
                        ).classes("aj-grow")
                    rule_regex = ui.input(
                        "Expressão regular",
                        placeholder=r"(?i)\bPIS\s+\d{3}\.\d{5}\.\d{2}-\d\b",
                    ).props("outlined dense").classes("w-full aj-mono")
                    add_rule_btn = ui.button("Incluir regra", icon="add").props(
                        "unelevated no-caps"
                    ).classes("aj-btn")
                    custom_list = ui.column().classes("w-full gap-0")

                with ui.column().classes("aj-card"):
                    ui.label("Termos protegidos").classes("aj-card-title")
                    ui.label(
                        "Termos que nunca devem virar token — órgãos, teses ou peças que o motor "
                        "possa marcar por engano. Um por linha."
                    ).classes("aj-card-note")
                    exclusions_box = ui.textarea(
                        placeholder="ex.: Tribunal Regional Federal\nex.: Cooperativa Exemplo",
                        value="\n".join(session.settings.exclusions),
                    ).props("outlined").classes("w-full")
                    save_excl_btn = ui.button("Salvar termos protegidos", icon="save").props(
                        "unelevated no-caps"
                    ).classes("aj-btn")

            with ui.column().classes("aj-col"):
                with ui.column().classes("aj-card"):
                    ui.label("Base local de CNPJ").classes("aj-card-title")
                    index = CnpjIndex(default_index_path(PROJECT_ROOT))
                    stats = index.stats()
                    if stats.get("presente") == "sim":
                        with ui.row().classes("aj-callout info items-start"):
                            ui.icon("check_circle")
                            ui.label(
                                f"Índice encontrado: {stats.get('registros', '?')} estabelecimentos "
                                "(região Sul). Quando um CNPJ aparece, a razão social é tokenizada no "
                                "texto inteiro. Órgãos (natureza 1xxx) não viram empresa."
                            )
                    else:
                        with ui.row().classes("aj-callout warn items-start"):
                            ui.icon("info")
                            ui.label(
                                "Não há índice da Receita neste computador. Para este caso, anexe a "
                                "planilha do Nexus na aba Caso. O índice anual continua opcional."
                            )
                    ui.switch(
                        "Usar a base CNPJ quando o arquivo existir",
                        value=session.settings.cnpj_lookup_enabled,
                        on_change=lambda e: persist_cnpj_flag(bool(e.value)),
                    ).props("color=primary")

                with ui.column().classes("aj-card"):
                    ui.label("Experimentos").classes("aj-card-title")
                    with ui.expansion(
                        "Passada-sombra de caixa (GLiNER)", icon="science"
                    ).classes("w-full"):
                        ui.label(
                            "Roda o GLiNER atual (limiar 0,75) sobre uma cópia em que trechos "
                            "TODO-MAIÚSCULOS viram Title Case. O texto original não muda. "
                            "Deixe desligado até medir a heurística de capa. Um fator por vez."
                        ).classes("aj-card-note")
                        ui.switch(
                            "Ligar passada-sombra de caixa",
                            value=session.settings.case_shadow_enabled,
                            on_change=lambda e: persist_shadow_flag(bool(e.value)),
                        ).props("color=primary")

    def toggle_tag(entity_type: str, enabled: bool) -> None:
        session.settings.enabled_tags[entity_type] = bool(enabled)
        persist()
        update_enabled_badge()

    def update_enabled_badge() -> None:
        on = sum(
            1 for tag in STRUCTURED_TAGS if session.settings.enabled_tags.get(tag.entity_type, True)
        )
        enabled_badge.text = f"{on} de {len(STRUCTURED_TAGS)} ligadas"

    def render_tags() -> None:
        groups_box.clear()
        by_type = {tag.entity_type: tag for tag in STRUCTURED_TAGS}
        grouped = {entity for _title, members in _TAG_GROUPS for entity in members}
        groups = [
            (title, [by_type[item] for item in members if item in by_type])
            for title, members in _TAG_GROUPS
        ]
        others = [tag for tag in STRUCTURED_TAGS if tag.entity_type not in grouped]
        if others:
            groups.append(("Outros", others))
        with groups_box:
            for title, tags in groups:
                if not tags:
                    continue
                with ui.column().classes("aj-group"):
                    ui.label(title).classes("aj-label")
                    for tag in tags:
                        with ui.element("div").classes("aj-tag"):
                            ui.label(_token_stem(tag.token)).classes("aj-token")
                            ui.switch(
                                tag.description,
                                value=session.settings.enabled_tags.get(tag.entity_type, True),
                                on_change=lambda e, entity=tag.entity_type: toggle_tag(
                                    entity, bool(e.value)
                                ),
                            ).props("color=primary dense")
        update_enabled_badge()

    def render_custom() -> None:
        custom_list.clear()
        with custom_list:
            if not session.settings.custom_rules:
                ui.label("Nenhuma regra personalizada.").classes("aj-card-note")
                return
            for index, rule in enumerate(session.settings.custom_rules):
                with ui.element("div").classes("aj-rule"):
                    with ui.column().classes("gap-1"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(rule.name).classes("aj-rule-name")
                            ui.label(f"[{rule.token_label}_n]").classes("aj-token")
                        ui.label(rule.regex).classes("aj-code")
                    remover = ui.button(
                        icon="close",
                        on_click=lambda i=index: remove_rule(i),
                    ).props('flat round dense aria-label="Excluir regra"')
                    remover.tooltip("Excluir regra")

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
        ui.notify("Regra incluída. Ela vale no próximo lote.", type="positive")

    def remove_rule(index: int) -> None:
        if 0 <= index < len(session.settings.custom_rules):
            session.settings.custom_rules.pop(index)
            persist()
            render_custom()

    def persist_cnpj_flag(enabled: bool) -> None:
        session.settings.cnpj_lookup_enabled = enabled
        persist()

    def persist_shadow_flag(enabled: bool) -> None:
        session.settings.case_shadow_enabled = enabled
        persist()

    def save_exclusions() -> None:
        lines = str(exclusions_box.value or "").splitlines()
        session.settings.exclusions = [item.strip() for item in lines if item.strip()]
        persist()
        ui.notify("Termos protegidos salvos.", type="positive")

    add_rule_btn.on_click(add_rule)
    save_excl_btn.on_click(save_exclusions)
    render_tags()
    render_custom()


def _build_help_dialog():
    with ui.dialog() as dialog, ui.column().classes("aj-dialog"):
        with ui.row().classes("w-full items-center no-wrap"):
            with ui.column().classes("gap-1"):
                ui.label("Referência").classes("aj-kicker")
                ui.label("Como funciona").classes("aj-title").style("font-size:24px")
            ui.space()
            ui.button(icon="close", on_click=dialog.close).props(
                'flat round aria-label="Fechar"'
            )
        ui.html(
            "<h3>O que permanece na peça</h3>"
            f"<p>{html.escape(FIDELITY_PRINCIPLE)}</p>"
        ).classes("w-full")
        ui.html(
            "<h3>Identificadores (Presidio, com validação)</h3><ul>"
            + "".join(
                f"<li><code>{html.escape(tag.token)}</code><span>{html.escape(tag.description)}</span></li>"
                for tag in STRUCTURED_TAGS
            )
            + "</ul>"
        ).classes("w-full")
        ui.html(
            "<h3>Nomes, empresas e endereços (neste computador)</h3><ul>"
            + "".join(
                f"<li><code>{html.escape(tag.token)}</code><span>{html.escape(tag.description)}</span></li>"
                for tag in CONTEXTUAL_TAGS
            )
            + "</ul><p style='margin-top:6px'>GLiNER no próprio computador; spaCy PT se o GLiNER faltar.</p>"
        ).classes("w-full")
        ui.html(
            "<h3>Extração de PDF</h3>"
            "<p>O texto sai do PDF folha a folha (PyMuPDF): cada folha leva a sua âncora e "
            "os marcadores do eproc (evento, documento, página) viram títulos. Tabela com "
            "linhas vira tabela. OCR só em folha sem texto legível: digitalizada, com o texto "
            "desenhado em vetor ou com fonte quebrada. Folha que não pôde ser lida fica "
            "marcada no texto, aparece na AUDITORIA e bloqueia o envio.</p>"
        ).classes("w-full")
        ui.html(f"<h3>Leitura externa</h3><p>{html.escape(GEMINI_POLICY)}</p>").classes("w-full")
        ui.html(
            "<h3>Versão em uso</h3>"
            f"<p><code>{html.escape(version_info().line())}</code></p>"
            "<p>O “código” muda com qualquer alteração do app, mesmo sem commit. A mesma "
            "linha vai para a AUDITORIA.txt e o LEIA-ME.txt de cada lote: é assim que se "
            "confere qual versão gerou cada texto. Se a janela mostrar um código antigo, "
            "feche o app e abra de novo.</p>"
        ).classes("w-full")
        ui.label(f"Endereço local deste app: {APP_URL}").classes("aj-card-note")
    return dialog


def _result_placeholder() -> None:
    with ui.column().classes("aj-empty"):
        ui.icon("receipt_long").style("font-size:34px;color:var(--aj-line-2)")
        ui.label("O resumo do lote aparece aqui").classes("aj-empty-title")
        ui.label(
            "Depois da execução: substituições por tipo, onde ficaram os textos e o mapa, "
            "e uma prévia com os tokens destacados."
        ).classes("aj-empty-text")


def _render_results(container, job: JobResult, token_labels: dict[str, str]) -> None:
    container.clear()
    succeeded = job.succeeded
    totals = Counter()
    for outcome in succeeded:
        totals.update(outcome.entity_counts)
    with container:
        with ui.row().classes("w-full items-center gap-3"):
            _status_chip(job)
            ui.label(f"Lote {job.job_id}").classes("aj-lote-id")
        ui.label(job.export_block_reason).classes("aj-status-line")
        if job.export_warning:
            with ui.row().classes("aj-callout warn items-start"):
                ui.icon("warning")
                ui.label(job.export_warning)

        with ui.element("div").classes("aj-kpis"):
            _kpi(
                "Arquivos",
                _fmt_int(len(succeeded)),
                f"de {_fmt_int(len(job.outcomes))} sem erro",
            )
            _kpi(
                "Substituições",
                _fmt_int(sum(item.replacements for item in succeeded)),
                "trechos trocados",
            )
            _kpi("Registros no mapa", _fmt_int(job.record_count), "dados distintos")
            _kpi(
                "Páginas",
                _fmt_int(sum(item.page_count for item in succeeded)),
                "lidas no lote",
            )

        if totals:
            with ui.column().classes("w-full gap-1"):
                ui.label("Trechos trocados por tipo").classes("aj-label")
                _chips(totals, token_labels)

        with ui.element("div").classes("aj-sets"):
            with ui.column().classes("aj-set"):
                with ui.element("div").classes("aj-set-title"):
                    ui.icon("description")
                    ui.label("1. Textos anonimizados")
                ui.label(
                    "O que você lê, copia ou envia para análise. Com LEIA-ME.txt e AUDITORIA.txt."
                ).classes("aj-card-note")
                ui.label(_path_display(job.export_dir)).classes("aj-path").tooltip(str(job.export_dir))
                for outcome in succeeded:
                    if outcome.markdown_path is not None:
                        ui.label(outcome.markdown_path.name).classes("aj-code")
                ui.button(
                    "Abrir pasta dos textos",
                    icon="folder_open",
                    on_click=lambda: _open_path(job.export_dir),
                ).props("outline no-caps dense").classes("aj-btn q-mt-xs")
            with ui.column().classes("aj-set private"):
                with ui.element("div").classes("aj-set-title"):
                    ui.icon("lock")
                    ui.label("2. Mapa para reverter")
                ui.label(
                    f"A chave dos tokens ({_plural(job.record_count, 'registro', 'registros')}). "
                    "Não envie esta pasta para fora deste computador."
                ).classes("aj-card-note")
                ui.label(_path_display(job.map_path)).classes("aj-path").tooltip(str(job.map_path))
                ui.button(
                    "Abrir pasta do mapa",
                    icon="lock_open",
                    on_click=lambda: _open_path(job.map_path.parent),
                ).props("outline no-caps dense").classes("aj-btn q-mt-xs")

        with ui.row().classes("w-full items-center gap-3"):
            if job.export_allowed:
                ui.button(
                    "Copiar resultado para a área de transferência",
                    icon="content_copy",
                    on_click=lambda: _copy_markdown(job),
                ).props("unelevated color=positive no-caps").classes("aj-btn")
            else:
                blocked = ui.button(
                    "Copiar resultado para a área de transferência",
                    icon="block",
                ).props("disable no-caps").classes("aj-btn")
                blocked.tooltip(job.export_block_reason)
            ui.label(
                "Para reverter: no mapa.json, cada token aponta para o texto original."
            ).classes("aj-card-note")

        with ui.column().classes("w-full gap-0"):
            ui.label("Arquivos").classes("aj-label q-mb-xs")
            for outcome in job.outcomes:
                _render_outcome(outcome, token_labels)

        first_ok = next((item for item in job.outcomes if item.ok and item.markdown_path), None)
        if first_ok and first_ok.markdown_path is not None:
            preview = first_ok.markdown_path.read_text(encoding="utf-8")[:_PREVIEW_CHARS]
            with ui.expansion(
                f"Prévia de {first_ok.markdown_path.name}", icon="visibility"
            ).classes("w-full"):
                ui.html(_preview_html(preview)).classes("aj-preview")


def _render_outcome(outcome: FileOutcome, token_labels: dict[str, str]) -> None:
    with ui.element("div").classes("aj-out"):
        if outcome.ok:
            ui.icon("check_circle").style("color: var(--aj-good)")
        else:
            ui.icon("error").style("color: var(--aj-bad)")
        with ui.column().classes("gap-1 aj-grow"):
            ui.label(outcome.source.name).classes("aj-file-name")
            if not outcome.ok:
                ui.label(outcome.error).classes("aj-out-meta").style("color: var(--aj-bad)")
                return
            ui.label(
                f"{_plural(outcome.replacements, 'substituição', 'substituições')} · "
                f"{_plural(outcome.page_count, 'página', 'páginas')} · extrator {outcome.extractor}"
            ).classes("aj-out-meta")
            if outcome.entity_counts:
                _chips(Counter(outcome.entity_counts), token_labels)
            else:
                ui.label("Nenhum identificador encontrado.").classes("aj-card-note")
            if outcome.markdown_path is not None:
                ui.label(_path_display(outcome.markdown_path)).classes("aj-path").tooltip(
                    str(outcome.markdown_path)
                )
            if outcome.residual_hits:
                with ui.row().classes("aj-callout warn items-start"):
                    ui.icon("manage_search")
                    ui.label(
                        "Revisão residual (não substituído): "
                        + ", ".join(outcome.residual_hits[:8])
                    )


def _status_chip(job: JobResult) -> None:
    if job.failed:
        kind, icon, text = "bad", "error", "Arquivos com erro"
    elif not job.export_allowed:
        kind, icon, text = "warn", "block", "Cópia bloqueada"
    elif job.export_warning:
        kind, icon, text = "warn", "report", "Revise os avisos"
    else:
        kind, icon, text = "ok", "check_circle", "Pronto para análise"
    with ui.element("span").classes(f"aj-status {kind}"):
        ui.icon(icon)
        ui.label(text)


def _kpi(label: str, value: str, sub: str) -> None:
    with ui.column().classes("aj-kpi"):
        ui.label(label).classes("aj-kpi-label")
        ui.label(value).classes("aj-kpi-value")
        ui.label(sub).classes("aj-kpi-sub")


def _chips(counts: Counter, token_labels: dict[str, str]) -> None:
    with ui.element("div").classes("aj-chips"):
        for entity_type, value in counts.most_common():
            label = token_labels.get(entity_type, entity_type)
            ui.html(
                f'<span class="aj-chip">{html.escape(label)} <b>{_fmt_int(value)}</b></span>'
            )


def _token_labels(session: Session) -> dict[str, str]:
    """Tipo interno -> rotulo do token (CPF, EMPRESA, PIS...)."""

    labels = {tag.entity_type: _token_stem(tag.token) for tag in (*STRUCTURED_TAGS, *CONTEXTUAL_TAGS)}
    labels.update(session.settings.token_labels())
    return labels


def _token_stem(token: str) -> str:
    return token.strip("[]").removesuffix("_n")


def _preview_html(text: str) -> str:
    """Texto do Markdown com os tokens como tarjas e a ancora de pagina como marcador."""

    escaped = html.escape(text)
    escaped = _PAGE_MARK.sub(
        lambda match: f'<span class="aj-pagemark">página {match.group(1)}</span>', escaped
    )
    escaped = _OTHER_COMMENT.sub(
        lambda match: f'<span class="aj-comment">{match.group(0)}</span>', escaped
    )
    return _TOKEN_IN_TEXT.sub(
        lambda match: (
            f'<span class="aj-tarja" title="{match.group(0)}">'
            f"{match.group(1)}<sub>{match.group(2)}</sub></span>"
        ),
        escaped,
    )


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{_fmt_int(count)} {singular if count == 1 else plural}"


def _fmt_int(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def _fmt_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    for unit, scale in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= scale:
            value = size / scale
            text = f"{value:.1f}" if value < 100 else f"{value:.0f}"
            return f"{text.replace('.', ',')} {unit}"
    return f"{size} B"


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _doc_kind(path: Path) -> str:
    return (path.suffix.lstrip(".") or "arq").upper()[:4]


def _path_display(path: Path) -> str:
    """Caminho para rotulo com reticencias a esquerda (a ponta e o que importa)."""

    return f"‎{path}‎"


def _copy_markdown(job: JobResult) -> None:
    parts: list[str] = []
    for outcome in job.succeeded:
        if outcome.markdown_path is not None:
            parts.append(outcome.markdown_path.read_text(encoding="utf-8"))
    if not parts:
        ui.notify("Não há Markdown para copiar.", type="warning")
        return
    payload = json.dumps("\n\n---\n\n".join(parts))
    ui.run_javascript(f"navigator.clipboard.writeText({payload})")
    ui.notify("Resultado copiado para a área de transferência. O mapa não foi copiado.", type="positive")


def _app_manifest() -> str:
    from urllib.parse import quote

    payload = (
        '{"name":"Anonimizador Juridico","short_name":"Anonimizador",'
        f'"start_url":"{APP_URL}","display":"standalone",'
        f'"background_color":"#f5f0e6","theme_color":"{_NAVY}"}}'
    )
    return quote(payload, safe="")


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

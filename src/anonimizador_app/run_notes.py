"""H9-o: linhas a mais no bloco "motor" da AUDITORIA, para comparar rodadas (bracos) entre si.

- quantas linhas da lista do caso NAO tem operacao (valem em qualquer operacao) quando uma operacao foi escolhida;
- quais etiquetas de identificador estao desligadas na rodada (ex.: chassi, RENAVAM);
- qual registro de revisao externa (pasta ``revisoes``) foi aplicado entre o lote anterior e este.

So contagens, rotulos de etiqueta e nomes de arquivo do proprio app (``revisao_<lote>_<data>.txt``): nenhum nome
do caso. O registro da revisao e lido de forma generica (nome do arquivo, data no nome e a linha "Aplicadas: N",
se houver), sem depender do importador (``review_import``).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .catalog import CONTEXTUAL_TAGS, STRUCTURED_TAGS
from .settings import AppSettings, case_item_key

REVIEWS_DIRNAME = "revisoes"

_LIST_LABELS = (
    ("target_people", "pessoas"),
    ("target_companies", "empresas"),
    ("target_ids", "CPF/CNPJ/NIRE"),
    ("target_aliases", "grafias"),
    ("target_processes", "processos"),
    ("target_cdas", "CDAs"),
    ("target_contacts", "contatos/veiculos"),
)
_STAMP = re.compile(r"(\d{8}_\d{6})")
_APPLIED = re.compile(r"^Aplicadas:\s*(\d+)", re.M)


def operation_free_lines(settings: AppSettings | None) -> list[str]:
    """Com operacao escolhida: quantas linhas da lista usada nao tem operacao na planilha (valem em todas)."""

    if settings is None or not settings.case_operation.strip():
        return []
    operations = settings.case_operations
    counts: list[tuple[str, int]] = []
    for field_name, label in _LIST_LABELS:
        values = getattr(settings, field_name, None) or []
        free = sum(1 for value in values if case_item_key(field_name, value) not in operations)
        if free:
            counts.append((label, free))
    total = sum(count for _label, count in counts)
    if not total:
        return ["Linhas da lista do caso sem operacao (valem em qualquer operacao): 0"]
    detail = ", ".join(f"{label} {count}" for label, count in counts)
    return [f"Linhas da lista do caso sem operacao (valem em qualquer operacao): {total} ({detail})"]


def _tag_label(entity_type: str) -> str:
    for tag in (*STRUCTURED_TAGS, *CONTEXTUAL_TAGS):
        if tag.entity_type == entity_type:
            return tag.token.strip("[]").removesuffix("_n")
    return entity_type.removeprefix("BR_")


def disabled_tag_lines(settings: AppSettings | None) -> list[str]:
    """Etiquetas de identificador desligadas nesta rodada (aba Regras)."""

    if settings is None:
        return []
    off = sorted(_tag_label(name) for name, enabled in settings.enabled_tags.items() if not enabled)
    lines = [
        "Etiquetas desligadas nesta rodada: " + ", ".join(off)
        if off
        else "Etiquetas desligadas nesta rodada: nenhuma"
    ]
    if not settings.local_ner_enabled:
        lines.append("Detector de nomes do motor local (NER): DESLIGADO nesta rodada.")
    return lines


def review_record_lines(lotes_root: Path, job_id: str) -> list[str]:
    """Registros de revisao externa (``revisoes\\revisao_*.txt``) gravados depois do lote anterior e ate este.

    ``lotes_root`` e a pasta dos lotes privados (pai da pasta do lote). Procura ``revisoes`` nela e, se ela se
    chama ``privados``, tambem na pasta acima (a pasta privada escolhida na tela)."""

    lotes_root = Path(lotes_root)
    current = _STAMP.search(job_id or "")
    if current is None:
        return []
    now = current.group(1)
    previous = ""
    try:
        for child in lotes_root.iterdir():
            match = _STAMP.search(child.name)
            if (
                child.is_dir()
                and child.name.startswith("lote_")
                and child.name != job_id
                and match is not None
                and match.group(1) < now
                and (child / "mapa.json").is_file()
            ):
                previous = max(previous, match.group(1))
    except OSError:
        return []
    folders = [lotes_root / REVIEWS_DIRNAME]
    if lotes_root.name == "privados":
        folders.append(lotes_root.parent / REVIEWS_DIRNAME)
    lines: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        try:
            files = sorted(path for path in folder.glob("revisao_*.txt") if path.is_file())
        except OSError:
            continue
        for path in files:
            if path.name in seen:
                continue
            stamps = _STAMP.findall(path.name)
            if not stamps or not (previous < stamps[-1] <= now):
                continue
            seen.add(path.name)
            when = datetime.strptime(stamps[-1], "%Y%m%d_%H%M%S").strftime("%d/%m/%Y %H:%M")
            try:
                applied = _APPLIED.search(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                applied = None
            suffix = f"; {applied.group(1)} linha(s) aplicada(s)" if applied else ""
            lines.append(f"Revisao externa aplicada: {REVIEWS_DIRNAME}\\{path.name} (registrada em {when}{suffix})")
    return lines


def run_note_lines(settings: AppSettings | None, lotes_root: Path, job_id: str) -> list[str]:
    """As tres partes juntas, na ordem do bloco "motor"."""

    return [
        *operation_free_lines(settings),
        *disabled_tag_lines(settings),
        *review_record_lines(lotes_root, job_id),
    ]

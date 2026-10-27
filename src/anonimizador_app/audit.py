"""Auto-auditoria pos-lote: conferencia mecanica dos criterios de fidelidade."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from .coverage import Coverage, pages_label, parse_coverage
from .fidelity import FidelityDropStats
from .gemini_detector import KEEP_TERMS
from .header_heuristic import street_values

_TOKEN = re.compile(r"\[([A-ZÁÉÍÓÚÂÊÔÃÕÇ_]+)_(\d+)(?:·G\d+)?\]")
_TOKEN_MARK = r"\[[A-ZÁÉÍÓÚÂÊÔÃÕÇ_]+_\d+(?:·G\d+)?\]"
# Colado a R$ sem espaço, ou token no meio do valor (R$ [TOKEN],28).
# Não usa \s: CAPITAL R$ + pessoa na linha seguinte e "[TOKEN] R$2.971,28" não alertam.
_MONEY_TOUCH = re.compile(
    rf"R\${_TOKEN_MARK}"
    rf"|{_TOKEN_MARK}R\$"
    rf"|R\$[ \t]+{_TOKEN_MARK}(?:,\d|\.\d)",
    re.IGNORECASE,
)
_DATE_GLUED = re.compile(
    r"\[[A-ZÁÉÍÓÚÂÊÔÃÕÇ_]+_\d+(?:·G\d+)?\]\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}\[[A-ZÁÉÍÓÚÂÊÔÃÕÇ_]+_\d+(?:·G\d+)?\]"
)
_CNPJ_LEFT = re.compile(r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)")
_CPF_LEFT = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
_CEP_LEFT = re.compile(r"(?<!\d)\d{5}-\d{3}(?!\d)")


def _token_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in _TOKEN.finditer(text):
        label = match.group(1)
        counts[label] = counts.get(label, 0) + 1
    return counts


def token_counts(text: str) -> dict[str, int]:
    """Contagem de tokens no Markdown anonimizado (rótulo → ocorrências)."""

    return _token_counts(text)


def fidelity_preservation_notes(stats: FidelityDropStats | None) -> list[str]:
    """Seção curta da AUDITORIA: só contagens, sem valores."""

    if stats is None:
        return []
    return [
        "Preservados por regra de fidelidade (somente contagens):",
        f"CNPJ público: {stats.cnpj_publico}",
        f"NIRE/protocolo: {stats.nire_protocolo}",
        f"telefone sem rótulo: {stats.telefone_sem_rotulo}",
        f"natureza jurídica: {stats.natureza_juridica}",
        f"parte de precedente: {stats.parte_precedente}",
        f"processo de precedente: {stats.processo_precedente}",
        f"higiene HTML/rotulo/fusao/tamanho/limite: "
        f"{stats.higiene_html}/{stats.higiene_rotulo}/"
        f"{stats.higiene_fusao}/{stats.higiene_tamanho}/{stats.higiene_limite}",
    ]


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "nenhum"
    return ", ".join(f"[{key}_n] x{value}" for key, value in sorted(counts.items()))


def extractor_note(extractor: str) -> str:
    """Deixa registrado no relatorio qual motor leu o PDF (o desvio de reserva ja foi silencioso)."""

    if extractor == "pymupdf-reserva":
        return (
            "ALERTA: extrator pymupdf-reserva (o Docling falhou neste arquivo; "
            "estrutura de tabelas pode ter se perdido)."
        )
    if extractor.startswith("docling"):
        return (
            f"Extrator: {extractor} (legado: ancora por contagem de quebras e "
            "teto de 15 min; confira a cobertura)"
        )
    return f"Extrator: {extractor}"


def audit_markdown(text: str) -> list[str]:
    notes: list[str] = []
    counts = _token_counts(text)
    if counts:
        notes.append(f"Tokens: {_format_counts(counts)}")
    else:
        notes.append("Nenhum token no texto.")

    surviving = [
        term
        for term in sorted(KEEP_TERMS)
        if term in text.casefold()
    ]
    if surviving:
        notes.append("Termos protegidos que permaneceram: " + ", ".join(surviving[:12]))
    else:
        notes.append(
            "Nenhum termo da lista KEEP_TERMS apareceu neste Markdown "
            "(pode ser peca sem orgao/sistema, ou troca indevida)."
        )

    if _MONEY_TOUCH.search(text):
        notes.append("ALERTA: token encostado em valor em R$.")
    if _DATE_GLUED.search(text):
        notes.append("ALERTA: token colado em data (sem espaco).")

    cpf_left = len(_CPF_LEFT.findall(text))
    cnpj_left = len(_CNPJ_LEFT.findall(text))
    if cpf_left:
        notes.append(f"ALERTA: {cpf_left} CPF formatado(s) em claro.")
    if cnpj_left:
        notes.append(
            f"CNPJ formatado em claro: {cnpj_left} "
            "(pode incluir orgao publico deixado de proposito)."
        )
    cep_left = len(_CEP_LEFT.findall(text))
    if cep_left:
        notes.append(
            f"CEP formatado em claro: {cep_left} "
            "(coluna sem cabecalho CEP ou peca publica)."
        )

    streets = street_values(text)
    if streets and counts.get("ENDERECO", 0) == 0:
        notes.append(
            f"ALERTA: {len(streets)} via(s) com numero no texto e nenhum [ENDERECO_n]."
        )

    capa = text[:4500]
    corpo = text[4500:]
    capa_counts = _token_counts(capa)
    corpo_counts = _token_counts(corpo)
    notes.append(
        "Tokens na capa (primeiros 4500 caracteres): " + _format_counts(capa_counts)
    )
    notes.append("Tokens no corpo: " + _format_counts(corpo_counts))

    anchors = len(re.findall(r"<!-- pagina-origem:\s*\d+", text))
    if anchors:
        notes.append(f"Ancoras de pagina: {anchors}")
    else:
        notes.append("Sem ancora de pagina neste arquivo.")
    coverage = parse_coverage(text)
    if coverage is not None:
        notes.append(coverage_note(coverage))
        if anchors and anchors != coverage.pages:
            notes.append(
                f"ALERTA: {anchors} ancora(s) para {coverage.pages} folha(s); "
                "a citacao por pagina pode estar deslocada."
            )
        if coverage.unread:
            notes.append(
                f"ALERTA: {len(coverage.unread)} folha(s) nao lida(s): "
                f"{pages_label(coverage.unread)}. Marcadas no texto; exportacao bloqueada."
            )
    else:
        ocr_pages = re.search(r"<!--\s*ocr-paginas:\s*(\d+)", text)
        if ocr_pages:
            notes.append(f"Paginas lidas por OCR (so imagem): {ocr_pages.group(1)}")
    ocr_failed = re.search(r"<!--\s*ocr-falhas:\s*(\d+)", text)
    if ocr_failed:
        notes.append(
            f"ALERTA: OCR falhou em {ocr_failed.group(1)} pagina(s) digitalizada(s); "
            "essas folhas ficaram so com o carimbo eproc."
        )
    return notes


def coverage_note(coverage: Coverage) -> str:
    """Uma linha: de onde veio o texto de cada folha (nativo, OCR, em branco, nao lida)."""

    return (
        f"Cobertura: {coverage.pages} folha(s); {coverage.native} com texto nativo; "
        f"{coverage.ocr} por OCR ({coverage.scanned} escaneada(s), "
        f"{coverage.vector} com texto vetorizado, {coverage.broken_font} com fonte quebrada"
        + (f", {coverage.hybrid} hibrida(s): texto nativo curto e corpo em imagem" if coverage.hybrid else "")
        + "); "
        + f"{coverage.blank} em branco; {len(coverage.unread)} nao lida(s)."
        + embedded_note(coverage)
    )


def embedded_note(coverage: Coverage) -> str:
    """v0.4.12: imagens embutidas em folha de texto, lidas por OCR (nada se perde sem aviso)."""

    if not (coverage.embedded or coverage.embedded_empty or coverage.embedded_failed):
        return ""
    note = (
        f" {coverage.embedded} imagem(ns) embutida(s) lida(s) por OCR em {coverage.embedded_pages} folha(s) de texto"
        f"; {coverage.embedded_empty} sem texto (foto, grafico)"
    )
    if coverage.embedded_failed:
        note += f"; ALERTA: {coverage.embedded_failed} imagem(ns) embutida(s) NAO lida(s) (falha do OCR): conferir no PDF"
    return note + "."


def audit_map_records(records: Sequence[object]) -> list[str]:
    """Varre o mapa sem copiar PII: so contagens e palavras de dicionario."""

    from anonimizador_juridico.pseudonymizer import fold_lexical

    from .target_list import is_blocked_particle

    blocked: list[str] = []
    html = 0
    fused = 0
    label = 0
    items = list(records)
    for rec in items:
        originals = [str(item) for item in (getattr(rec, "originals", None) or [])]
        canonical = str(getattr(rec, "canonical", "") or "")
        blob = [canonical, *originals]
        if any("<!--" in item for item in blob):
            html += 1
        if any(item.count("\n") >= 2 for item in blob):
            fused += 1
        if any(
            re.fullmatch(
                r"(?i)(?:CEP|Conta|Ag[eê]ncia|N[uú]mero)\s*:?\s*",
                item.strip(),
            )
            for item in blob
        ):
            label += 1
        for item in blob:
            if not is_blocked_particle(item):
                continue
            word = fold_lexical(item)
            if word and word not in blocked:
                blocked.append(word)
    notes = [f"Registros no mapa: {len(items)}"]
    if blocked:
        notes.append(
            "ALERTA: originais que sao palavra de dicionario/UF/tese: "
            + ", ".join(sorted(blocked)[:24])
        )
    if html:
        notes.append(f"ALERTA: {html} original(is) com ancora HTML.")
    if fused:
        notes.append(
            f"ALERTA: {fused} original(is) com duas ou mais quebras de linha."
        )
    if label:
        notes.append(f"ALERTA: {label} original(is) que sao so rotulo de campo.")
    if not blocked and not html and not fused and not label:
        notes.append("Nenhum original de dicionario, HTML, fusao ou rotulo no mapa.")
    return notes


def write_audit_report(
    export_dir: Path,
    notes_by_file: Sequence[tuple[str, list[str]]],
    *,
    engine: str = "local",
) -> Path:
    from .local_ner import ner_status_lines
    from .version import version_info

    lines = [
        "AUDITORIA AUTOMATICA DESTE LOTE",
        "Conferencia dos criterios de fidelidade. Nao substitui a revisao humana.",
        f"Versao do app que gerou este lote: {version_info().line(engine)}",
        *ner_status_lines(),
        "",
    ]
    for name, notes in notes_by_file:
        lines.append(f"## {name}")
        lines.extend(f"- {item}" for item in notes)
        lines.append("")
    path = Path(export_dir) / "AUDITORIA.txt"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path

"""Harness de medição de um lote anonimizado (ciclo 0). Sem dependência extra."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .audit import token_counts
from .gemini_detector import KEEP_TERMS
from .geography import fold_key

_TOKEN = re.compile(r"\[([A-ZÁÉÍÓÚÂÊÔÃÕÇ_]+)_(\d+)\]")

_TIPO_ENTITY = {
    "PESSOA": {"PERSON", "PESSOA"},
    "EMPRESA": {"ORGANIZATION", "EMPRESA"},
    "ENDERECO": {"LOCATION", "ENDERECO"},
    "CPF": {"BR_CPF", "CPF"},
    "CNPJ": {"BR_CNPJ", "CNPJ"},
    "CONTA": {"BR_CONTA_BANCARIA", "CONTA"},
    "TELEFONE": {"BR_PHONE", "TELEFONE"},
    "CEP": {"BR_CEP", "CEP"},
    "CODIGO": set(),
    "RG": {"BR_RG", "RG"},
}


def normalize_search(text: str) -> str:
    return re.sub(r"\s+", " ", fold_key(text)).strip()


def find_markdown(lote_dir: Path) -> Path:
    files = sorted(path for path in Path(lote_dir).glob("*.md") if path.is_file())
    if not files:
        raise FileNotFoundError(f"Nenhum Markdown em {lote_dir}")
    if len(files) == 1:
        return files[0]
    return max(files, key=lambda path: path.stat().st_size)


def resolve_mapa(
    lote_dir: Path,
    *,
    mapa: Path | None = None,
    project_root: Path | None = None,
) -> Path:
    if mapa is not None:
        path = Path(mapa)
        if not path.is_file():
            raise FileNotFoundError(f"Mapa nao encontrado: {path}")
        return path
    local = Path(lote_dir) / "mapa.json"
    if local.is_file():
        return local
    root = project_root or Path.cwd()
    fallback = root / "saidas" / "privados" / Path(lote_dir).name / "mapa.json"
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(
        f"mapa.json nao esta em {lote_dir} nem em {fallback}"
    )


def unique_tokens_by_label(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        token = str(record.get("token") or "")
        label = token.strip("[]").rsplit("_", 1)[0]
        if not label:
            continue
        counts[label] = counts.get(label, 0) + 1
    return counts


def surviving_keep_terms(text: str) -> list[str]:
    folded = text.casefold()
    return [term for term in sorted(KEEP_TERMS) if term in folded]


def locate_normalized(text: str, needle: str) -> list[dict[str, Any]]:
    """Busca case-insensitive, com fold de acento e espaços/quebras colapsados."""

    target = normalize_search(needle)
    if not target:
        return []
    lines = text.splitlines()
    hits: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if target in normalize_search(line):
            hits.append({"linha": index, "trecho": _clip(line.strip())})
    if hits:
        return hits
    folded, line_at = _collapsed_index(text)
    start = 0
    while True:
        pos = folded.find(target, start)
        if pos < 0:
            break
        line_no = line_at[pos] if pos < len(line_at) else 0
        snippet = lines[line_no - 1].strip() if 1 <= line_no <= len(lines) else folded[pos : pos + 80]
        hits.append({"linha": line_no, "trecho": _clip(snippet), "quebrado": True})
        start = pos + max(1, len(target))
    return hits


def tokens_for_entity(
    records: Sequence[Mapping[str, Any]],
    *,
    nome: str,
    variantes: Iterable[str] = (),
    tipo: str = "",
) -> list[str]:
    keys = {normalize_search(nome), *(normalize_search(item) for item in variantes)}
    keys.discard("")
    alvo_hits: list[str] = []
    nome_n = normalize_search(nome)
    for record in records:
        alvo = normalize_search(str(record.get("alvo") or ""))
        if not alvo or not nome_n:
            continue
        if nome_n == alvo or nome_n in alvo or (
            alvo in nome_n and (" " in alvo or len(alvo) >= 12)
        ):
            token = str(record.get("token") or "")
            if token and token not in alvo_hits:
                alvo_hits.append(token)
    if alvo_hits:
        return alvo_hits
    allowed = _TIPO_ENTITY.get(tipo.upper(), set())
    found: list[str] = []
    for record in records:
        if allowed:
            entity_type = str(record.get("entity_type") or "")
            token_label = str(record.get("token") or "").strip("[]").rsplit("_", 1)[0]
            if entity_type not in allowed and token_label not in allowed:
                continue
        fields = [str(record.get("canonical") or "")]
        fields.extend(str(item) for item in record.get("originals") or [])
        blob = " ".join(normalize_search(item) for item in fields if item)
        if any(key in blob or blob in key for key in keys if key):
            token = str(record.get("token") or "")
            if token and token not in found:
                found.append(token)
    return found


def analyze_lote(markdown: str, mapa: Mapping[str, Any]) -> dict[str, Any]:
    records = list(mapa.get("records") or [])
    return {
        "ocorrencias_tags": token_counts(markdown),
        "tokens_unicos": unique_tokens_by_label(records),
        "termos_protegidos": surviving_keep_terms(markdown),
        "records": records,
    }


def compare_stats(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ocorrencias_tags": _delta_table(
            before.get("ocorrencias_tags") or {},
            after.get("ocorrencias_tags") or {},
        ),
        "tokens_unicos": _delta_table(
            before.get("tokens_unicos") or {},
            after.get("tokens_unicos") or {},
        ),
    }


def evaluate_gabarito(
    markdown: str,
    records: Sequence[Mapping[str, Any]],
    gabarito: Mapping[str, Any],
) -> dict[str, Any]:
    falhas: list[str] = []
    vazamentos: list[dict[str, Any]] = []
    for item in gabarito.get("nao_pode_aparecer") or []:
        texto = str(item.get("texto") or "")
        hits = locate_normalized(markdown, texto)
        entry = {**item, "hits": hits, "presente": bool(hits)}
        vazamentos.append(entry)
        if hits:
            falhas.append(f"nao_pode_aparecer: {texto!r} ({len(hits)} hit(s))")

    permanencias: list[dict[str, Any]] = []
    for item in gabarito.get("deve_permanecer") or []:
        texto = str(item.get("texto") or "")
        hits = locate_normalized(markdown, texto)
        entry = {**item, "hits": hits, "presente": bool(hits)}
        permanencias.append(entry)
        if not hits:
            falhas.append(f"deve_permanecer: {texto!r} ausente")

    entidades: list[dict[str, Any]] = []
    for item in gabarito.get("entidades") or []:
        nome = str(item.get("nome") or "")
        tipo = str(item.get("tipo") or "")
        variantes = [str(v) for v in item.get("variantes") or []]
        tokens = tokens_for_entity(
            records, nome=nome, variantes=variantes, tipo=tipo
        )
        ok = len(tokens) == 1
        entidades.append({**item, "tokens": tokens, "n_tokens": len(tokens), "ok": ok})
        if not ok:
            falhas.append(
                f"entidades: {nome!r} fragmentada em {len(tokens)} token(s) {tokens}"
            )

    observar = _advisory(markdown, gabarito.get("observar") or [])
    ciclo4 = _advisory(markdown, gabarito.get("escopo_ciclo4") or [])
    for item in ciclo4:
        if item.get("presente"):
            texto = str(item.get("texto") or "")
            falhas.append(f"escopo_ciclo4: {texto!r} ainda em claro")
    return {
        "nao_pode_aparecer": vazamentos,
        "deve_permanecer": permanencias,
        "entidades": entidades,
        "observar": observar,
        "escopo_ciclo4": ciclo4,
        "falhas": falhas,
        "n_falhas": len(falhas),
        "resultado": "APROVADO" if not falhas else "REPROVADO",
    }


def format_report(
    *,
    lote_dir: Path,
    stats: Mapping[str, Any],
    gabarito_eval: Mapping[str, Any] | None = None,
    comparacao: Mapping[str, Any] | None = None,
    comparar_nome: str = "",
) -> str:
    lines = [
        f"LOTE {Path(lote_dir).name}",
        f"Markdown: {stats.get('markdown_name', '')}",
        f"Mapa: {stats.get('mapa_path', '')}",
        "",
        "Ocorrencias por tag no .md:",
        _format_counts(stats.get("ocorrencias_tags") or {}),
        "",
        "Tokens unicos no mapa (Metrica 3):",
        _format_counts(stats.get("tokens_unicos") or {}),
        "",
        "Termos protegidos presentes:",
        ", ".join(stats.get("termos_protegidos") or []) or "(nenhum)",
    ]
    if comparacao:
        lines.extend(
            [
                "",
                f"COMPARACAO com {comparar_nome or 'lote anterior'}",
                "ocorrencias por tag (tag | anterior | atual | delta):",
                _format_delta(comparacao.get("ocorrencias_tags") or []),
                "tokens unicos (tag | anterior | atual | delta):",
                _format_delta(comparacao.get("tokens_unicos") or []),
            ]
        )
    if gabarito_eval:
        lines.extend(["", "GABARITO", f"Resultado: {gabarito_eval.get('resultado')}"])
        lines.append(f"Falhas: {gabarito_eval.get('n_falhas', 0)}")
        for fail in gabarito_eval.get("falhas") or []:
            lines.append(f"  - {fail}")
        lines.append("nao_pode_aparecer:")
        for item in gabarito_eval.get("nao_pode_aparecer") or []:
            flag = "VAZAMENTO" if item.get("presente") else "ok"
            extra = ""
            if item.get("hits"):
                first = item["hits"][0]
                extra = f"  l.{first.get('linha')} {first.get('trecho')!r}"
            lines.append(f"  [{flag}] {item.get('texto')!r}{extra}")
        lines.append("deve_permanecer:")
        for item in gabarito_eval.get("deve_permanecer") or []:
            flag = "ok" if item.get("presente") else "SUMIU"
            lines.append(f"  [{flag}] {item.get('texto')!r}")
        lines.append("entidades (meta = 1 token):")
        for item in gabarito_eval.get("entidades") or []:
            flag = "ok" if item.get("ok") else "FRAGMENTADA"
            lines.append(
                f"  [{flag}] {item.get('nome')!r} n={item.get('n_tokens')} "
                f"{item.get('tokens')}"
            )
        lines.append("observar:")
        for item in gabarito_eval.get("observar") or []:
            estado = "presente" if item.get("presente") else "ausente"
            lines.append(f"  [aviso] {item.get('texto')!r} ({estado})")
        if gabarito_eval.get("escopo_ciclo4"):
            lines.append("escopo_ciclo4 (decisao pendente):")
            for item in gabarito_eval.get("escopo_ciclo4") or []:
                estado = "presente" if item.get("presente") else "ausente"
                lines.append(f"  [aviso] {item.get('texto')!r} ({estado})")
    lines.append("")
    if gabarito_eval:
        lines.append(str(gabarito_eval.get("resultado") or "REPROVADO"))
    return "\n".join(lines) + "\n"


def audit_lote_dir(
    lote_dir: Path,
    *,
    mapa: Path | None = None,
    comparar: Path | None = None,
    gabarito: Path | None = None,
    project_root: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    lote_dir = Path(lote_dir)
    md_path = find_markdown(lote_dir)
    mapa_path = resolve_mapa(lote_dir, mapa=mapa, project_root=project_root)
    markdown = md_path.read_text(encoding="utf-8")
    mapa_payload = json.loads(mapa_path.read_text(encoding="utf-8"))
    stats = analyze_lote(markdown, mapa_payload)
    stats["markdown_name"] = md_path.name
    stats["mapa_path"] = str(mapa_path)
    comparacao = None
    comparar_nome = ""
    if comparar is not None:
        other = Path(comparar)
        other_md = find_markdown(other).read_text(encoding="utf-8")
        other_mapa_path = resolve_mapa(other, project_root=project_root)
        other_mapa = json.loads(other_mapa_path.read_text(encoding="utf-8"))
        comparacao = compare_stats(analyze_lote(other_md, other_mapa), stats)
        comparar_nome = other.name
    gabarito_eval = None
    if gabarito is not None:
        payload = json.loads(Path(gabarito).read_text(encoding="utf-8"))
        gabarito_eval = evaluate_gabarito(markdown, stats["records"], payload)
    report = format_report(
        lote_dir=lote_dir,
        stats=stats,
        gabarito_eval=gabarito_eval,
        comparacao=comparacao,
        comparar_nome=comparar_nome,
    )
    summary = {
        "lote": lote_dir.name,
        "markdown": md_path.name,
        "mapa": str(mapa_path),
        "ocorrencias_tags": stats["ocorrencias_tags"],
        "tokens_unicos": stats["tokens_unicos"],
        "termos_protegidos": stats["termos_protegidos"],
        "comparacao": comparacao,
        "gabarito": gabarito_eval,
        "resultado": (gabarito_eval or {}).get("resultado"),
        "n_falhas": (gabarito_eval or {}).get("n_falhas", 0),
    }
    return report, summary


def _advisory(markdown: str, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        texto = str(item.get("texto") or "")
        hits = locate_normalized(markdown, texto)
        result.append({**item, "hits": hits, "presente": bool(hits)})
    return result


def _collapsed_index(text: str) -> tuple[str, list[int]]:
    folded_chars: list[str] = []
    line_at: list[int] = []
    line = 1
    pending_space = False
    started = False
    for char in text:
        if char == "\n":
            line += 1
            pending_space = started
            continue
        if char.isspace():
            pending_space = started
            continue
        piece = fold_key(char)
        if not piece:
            continue
        if pending_space:
            folded_chars.append(" ")
            line_at.append(line)
            pending_space = False
        for folded in piece:
            folded_chars.append(folded)
            line_at.append(line)
        started = True
    return "".join(folded_chars), line_at


def _delta_table(
    before: Mapping[str, int], after: Mapping[str, int]
) -> list[dict[str, Any]]:
    keys = sorted(set(before) | set(after))
    rows = []
    for key in keys:
        old = int(before.get(key, 0))
        new = int(after.get(key, 0))
        rows.append({"tag": key, "anterior": old, "atual": new, "delta": new - old})
    return rows


def _format_counts(counts: Mapping[str, int]) -> str:
    if not counts:
        return "  (nenhum)"
    width = max(len(key) for key in counts)
    return "\n".join(
        f"  {key:<{width}}  {value}" for key, value in sorted(counts.items())
    )


def _format_delta(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return "  (nenhum)"
    width = max(len(str(row.get("tag", ""))) for row in rows)
    lines = [f"  {'tag':<{width}}  anterior  atual  delta"]
    for row in rows:
        lines.append(
            f"  {row['tag']:<{width}}  {row['anterior']:>8}  {row['atual']:>5}  {row['delta']:>5}"
        )
    return "\n".join(lines)


def _clip(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"

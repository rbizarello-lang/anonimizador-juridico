"""Audita um lote anonimizado: tags, fragmentacao, gabarito, comparacao."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anonimizador_app.lote_audit import audit_lote_dir


def audit_lote(
    lote_dir,
    *,
    mapa=None,
    comparar=None,
    mapa_anterior=None,
    gabarito=None,
    project_root=None,
):
    """Compatível com tests/test_auditar_lote.py (API do Ciclo 0–3)."""

    del mapa_anterior
    from pathlib import Path

    report, summary = audit_lote_dir(
        Path(lote_dir),
        mapa=Path(mapa) if mapa else None,
        comparar=Path(comparar) if comparar else None,
        gabarito=Path(gabarito) if gabarito else None,
        project_root=Path(project_root) if project_root else ROOT,
    )
    gab = summary.get("gabarito") or {}
    comparacao = summary.get("comparacao")
    old_comp = None
    if comparacao:
        old_comp = {
            "ocorrencias": [
                (row["tag"], row["anterior"], row["atual"], row["delta"])
                for row in comparacao.get("ocorrencias_tags") or []
            ],
            "tokens_unicos": [
                (row["tag"], row["anterior"], row["atual"], row["delta"])
                for row in comparacao.get("tokens_unicos") or []
            ],
        }
    falhas = list(gab.get("falhas") or [])
    return {
        "lote": str(lote_dir),
        "markdown": [summary.get("markdown")],
        "mapa": summary.get("mapa"),
        "ocorrencias": summary.get("ocorrencias_tags") or {},
        "tokens_unicos": summary.get("tokens_unicos") or {},
        "termos_protegidos": summary.get("termos_protegidos") or [],
        "falhas": falhas,
        "avisos": [],
        "veredito": gab.get("resultado") or ("APROVADO" if not falhas else "REPROVADO"),
        "contagem_falhas": gab.get("n_falhas", len(falhas)),
        "comparacao": old_comp,
        "nao_pode_aparecer": gab.get("nao_pode_aparecer") or [],
        "deve_permanecer": gab.get("deve_permanecer") or [],
        "entidades": gab.get("entidades") or [],
        "observar": gab.get("observar") or [],
        "escopo_ciclo4": gab.get("escopo_ciclo4") or [],
        "relatorio": report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mede um lote: ocorrencias, tokens unicos, gabarito."
    )
    parser.add_argument("dir_lote", help="Pasta do lote (com o .md anonimizado).")
    parser.add_argument("--mapa", help="mapa.json se nao estiver no lote nem em saidas/privados.")
    parser.add_argument("--comparar", help="Lote anterior para tabela de delta.")
    parser.add_argument("--gabarito", help="JSON privado de gabarito.")
    parser.add_argument("--json", dest="json_out", help="Grava resumo JSON neste caminho.")
    args = parser.parse_args(argv)

    lote_dir = Path(args.dir_lote)
    if not lote_dir.is_dir():
        print(f"Pasta nao encontrada: {lote_dir}", file=sys.stderr)
        return 2

    try:
        report, summary = audit_lote_dir(
            lote_dir,
            mapa=Path(args.mapa) if args.mapa else None,
            comparar=Path(args.comparar) if args.comparar else None,
            gabarito=Path(args.gabarito) if args.gabarito else None,
            project_root=ROOT,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    sys.stdout.write(report)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if summary.get("gabarito") and summary.get("resultado") == "REPROVADO":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
